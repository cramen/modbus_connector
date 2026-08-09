import itertools
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Qt needs system libraries (libEGL etc.) that headless Linux CI runners lack;
# skip the whole module there — it still runs on macOS/Windows CI and dev machines.
# A plain try/except is needed: pytest.importorskip re-raises ImportErrors coming
# from a missing shared library inside an otherwise installed package.
try:
    from PySide6.QtWidgets import QApplication  # noqa: E402
except ImportError as exc:
    pytest.skip(f"Qt system libraries not available: {exc}", allow_module_level=True)

from PySide6.QtGui import QColor  # noqa: E402

from modbus_connector.models import RegisterRow  # noqa: E402
from modbus_connector.registers_panel import (  # noqa: E402
    COL_ADDRESS,
    COL_NEW_VALUE,
    COL_POLL,
    COL_UNIT_ID,
    COL_VALUE,
    RegistersPanel,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_same_value_written_twice(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    writes: list[tuple] = []
    panel.writeRequested.connect(lambda *args: writes.append(args))

    item = panel._table.item(0, COL_NEW_VALUE)
    assert item is not None

    item.setText("5")
    assert len(writes) == 1
    assert item.text() == ""  # cleared after the write is issued

    item.setText("5")
    assert len(writes) == 2
    assert writes[0][1:] == writes[1][1:]  # same unit/row/values, only request id differs

    item.setText("")  # empty text never triggers a write
    assert len(writes) == 2


def _read_row(panel: RegistersPanel, index: int) -> int:
    reads: list[tuple] = []
    panel.readRequested.connect(lambda *args: reads.append(args))
    panel._read_table_row(index)
    assert len(reads) == 1
    return int(reads[0][0])


def test_changed_value_flashes_background(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    token = panel._token_at(0)

    request_id = _read_row(panel, 0)
    panel.handle_read_finished(request_id, True, [1], "")
    item = panel._table.item(0, COL_VALUE)
    assert item is not None
    assert item.text() == "1"
    assert item.background().color() == QColor(144, 238, 144)  # changed: flashed
    generation = panel._flash_generations[token]

    request_id = _read_row(panel, 0)
    panel.handle_read_finished(request_id, True, [1], "")
    assert panel._flash_generations[token] == generation  # same value: no new flash


def test_filter_hides_and_unhides_rows(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_state(
        [
            {"name": "temp", "kind": "holding_registers", "address": 10, "count": 1},
            {"name": "pressure", "kind": "input_registers", "address": 20, "count": 1},
        ]
    )

    panel._filter_edit.setText("TEMP")  # case-insensitive name match
    assert not panel._table.isRowHidden(0)
    assert panel._table.isRowHidden(1)

    panel._filter_edit.setText("20")  # address match
    assert panel._table.isRowHidden(0)
    assert not panel._table.isRowHidden(1)

    panel._filter_edit.setText("input")  # type match
    assert panel._table.isRowHidden(0)
    assert not panel._table.isRowHidden(1)

    panel._filter_edit.setText("")  # clearing unhides everything
    assert not panel._table.isRowHidden(0)
    assert not panel._table.isRowHidden(1)

    panel._filter_edit.setText("temp")  # newly added rows are filtered too
    panel._add_row(RegisterRow(name="new", kind="holding_registers", address=30))
    assert panel._table.isRowHidden(2)


def test_sort_preserves_tokens_and_pending_reads(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_state(
        [
            {"name": "c", "kind": "holding_registers", "address": 30, "count": 1},
            {"name": "a", "kind": "holding_registers", "address": 10, "count": 1},
            {"name": "b", "kind": "holding_registers", "address": 20, "count": 1},
        ]
    )
    tokens_before = [panel._token_at(i) for i in range(3)]
    request_id = _read_row(panel, 0)  # pending read on row "c"

    panel._sort_by_address()

    addresses = [panel._table.item(i, COL_ADDRESS).text() for i in range(3)]
    assert addresses == ["10", "20", "30"]
    tokens_after = [panel._token_at(i) for i in range(3)]
    assert tokens_after == [tokens_before[1], tokens_before[2], tokens_before[0]]

    panel.handle_read_finished(request_id, True, [42], "")
    assert panel._table.item(2, COL_VALUE).text() == "42"  # "c" followed its token
    assert panel._table.item(0, COL_VALUE).text() == ""


def test_per_row_unit_id_used_for_reads_and_writes(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_state(
        [
            {"name": "a", "kind": "holding_registers", "address": 0, "count": 1},
            {"name": "b", "kind": "holding_registers", "address": 1, "count": 1},
        ]
    )
    panel.set_unit_id(3)
    unit_item = panel._table.item(0, COL_UNIT_ID)
    assert unit_item is not None
    unit_item.setText("5")

    reads: list[tuple] = []
    writes: list[tuple] = []
    panel.readRequested.connect(lambda *args: reads.append(args))
    panel.writeRequested.connect(lambda *args: writes.append(args))
    panel._read_table_row(0)
    panel._read_table_row(1)
    assert reads[0][1] == 5  # per-row unit wins
    assert reads[1][1] == 3  # empty unit falls back to the global unit

    new_value = panel._table.item(0, COL_NEW_VALUE)
    assert new_value is not None
    new_value.setText("7")
    assert writes[0][1] == 5


def test_unit_id_state_roundtrip(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_state(
        [
            {"name": "a", "kind": "holding_registers", "address": 0, "count": 1,
             "unit_id": "5"},
            {"name": "b", "kind": "holding_registers", "address": 1, "count": 1},
            {"name": "c", "kind": "holding_registers", "address": 2, "count": 1,
             "unit_id": "junk"},
            {"name": "d", "kind": "holding_registers", "address": 3, "count": 1,
             "unit_id": "300"},
        ]
    )
    cells = [panel._table.item(i, COL_UNIT_ID).text() for i in range(4)]
    assert cells == ["5", "", "", ""]  # invalid values tolerated as empty

    state = panel.state()
    assert [entry["unit_id"] for entry in state] == ["5", "", "", ""]

    panel.set_state(state)
    cells = [panel._table.item(i, COL_UNIT_ID).text() for i in range(4)]
    assert cells == ["5", "", "", ""]


def test_due_rows_respect_per_row_interval(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_state(
        [
            {"name": "fast", "kind": "holding_registers", "address": 0, "count": 1},
            {"name": "slow", "kind": "holding_registers", "address": 1, "count": 1,
             "poll_ms": "5000"},
            {"name": "junk", "kind": "holding_registers", "address": 2, "count": 1,
             "poll_ms": "junk"},
        ]
    )
    panel._last_poll[panel._token_at(1)] = 0.0
    assert panel._due_rows(1.0) == [0, 2]  # 1s < 5000ms: slow row not due
    assert panel._due_rows(5.0) == [0, 1, 2]  # due exactly at 5s


def test_stop_polling_resets_last_poll(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel._last_poll[panel._token_at(0)] = 123.0
    panel.stop_polling()
    assert panel._last_poll == {}


def test_poll_ms_state_roundtrip(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_state(
        [
            {"name": "a", "kind": "holding_registers", "address": 0, "count": 1,
             "poll_ms": "5000"},
            {"name": "b", "kind": "holding_registers", "address": 1, "count": 1},
            {"name": "c", "kind": "holding_registers", "address": 2, "count": 1,
             "poll_ms": "junk"},
            {"name": "d", "kind": "holding_registers", "address": 3, "count": 1,
             "poll_ms": "50"},
        ]
    )
    cells = [panel._table.item(i, COL_POLL).text() for i in range(4)]
    assert cells == ["5000", "", "", ""]  # junk and <100 tolerated as global

    state = panel.state()
    assert [entry["poll_ms"] for entry in state] == ["5000", "", "", ""]

    panel.set_state(state)
    cells = [panel._table.item(i, COL_POLL).text() for i in range(4)]
    assert cells == ["5000", "", "", ""]
