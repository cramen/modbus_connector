import csv
import itertools
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Qt needs system libraries (libEGL etc.) that headless Linux CI runners lack;
# skip the whole module there — it still runs on macOS/Windows CI and dev machines.
# A plain try/except is needed: pytest.importorskip re-raises ImportErrors coming
# from a missing shared library inside an otherwise installed package.
try:
    from PySide6.QtCore import QEvent, Qt  # noqa: E402
    from PySide6.QtWidgets import QApplication, QTableWidget  # noqa: E402
except ImportError as exc:
    pytest.skip(f"Qt system libraries not available: {exc}", allow_module_level=True)

from PySide6.QtGui import QColor, QKeyEvent  # noqa: E402

from modbus_connector.csv_dialogs import (  # noqa: E402
    ExportColumnsDialog,
    ImportMappingDialog,
)
from modbus_connector.datalogger import LogSettings  # noqa: E402
from modbus_connector.datalogger_dialog import LoggingSettingsDialog  # noqa: E402
from modbus_connector.models import RegisterRow, csv_header  # noqa: E402
from modbus_connector.registers_panel import (  # noqa: E402
    COL_ADDRESS,
    COL_NAME,
    COL_NEW_VALUE,
    COL_POLL,
    COL_TREND,
    COL_TYPE,
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


def test_reads_append_to_row_series(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_state(
        [
            {"name": "a", "kind": "holding_registers", "address": 0, "count": 1,
             "scale": 2.0},
            {"name": "h", "kind": "holding_registers", "address": 1, "count": 1,
             "format": "hex"},
        ]
    )
    token_a, token_h = panel._token_at(0), panel._token_at(1)
    assert panel._table.cellWidget(0, COL_TREND) is panel._sparklines[token_a]
    assert panel._table.cellWidget(1, COL_TREND) is panel._sparklines[token_h]

    panel.start_polling(True)  # history capture follows the poll+record mode
    panel.handle_read_finished(_read_row(panel, 0), True, [3], "")
    panel.handle_read_finished(_read_row(panel, 0), True, [4], "")
    panel.handle_read_finished(_read_row(panel, 1), True, [0x1A], "")
    panel.stop_polling()
    assert panel._series[token_a].points()[1] == [6.0, 8.0]  # scale applied
    assert len(panel._series[token_h]) == 0  # hex is not numeric: not captured


def test_reads_append_bits_for_coils(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_state([{"name": "c", "kind": "coils", "address": 0, "count": 4}])
    token = panel._token_at(0)
    panel.start_polling(True)
    panel.handle_read_finished(_read_row(panel, 0), True, [True, False, True], "")
    panel.stop_polling()
    assert panel._series[token].points()[1] == [1.0]  # the first bit only


def test_recording_follows_poll_mode(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    token = panel._token_at(0)

    panel.handle_read_finished(_read_row(panel, 0), True, [5], "")
    assert len(panel._series[token]) == 0  # stopped: manual reads are not recorded

    panel.start_polling(False)  # polling without recording
    panel.handle_read_finished(_read_row(panel, 0), True, [5], "")
    assert len(panel._series[token]) == 0

    panel.start_polling(True)  # flip mid-poll: recording on, timers keep running
    assert panel.is_polling()
    panel.handle_read_finished(_read_row(panel, 0), True, [6], "")
    assert panel._series[token].points()[1] == [6.0]

    panel.start_polling(False)  # flip back: capture pauses, old data kept
    panel.handle_read_finished(_read_row(panel, 0), True, [7], "")
    assert panel._series[token].points()[1] == [6.0]

    panel.stop_polling()
    assert not panel.is_recording()
    panel.handle_read_finished(_read_row(panel, 0), True, [8], "")
    assert panel._series[token].points()[1] == [6.0]  # stopped again: no capture


def test_poll_split_button_drives_mode(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    states: list[tuple] = []
    panel.pollStateChanged.connect(lambda *args: states.append(args))
    assert panel._poll_button.text() == "Start polling and record"  # default mode

    panel._start_poll_action.trigger()  # menu: poll without recording
    assert panel.is_polling() and not panel.is_recording()
    assert panel._poll_button.text() == "Stop polling"

    panel._start_record_action.trigger()  # menu while running: flip to recording
    assert panel.is_polling() and panel.is_recording()

    panel._poll_button.click()  # main action while running: stop everything
    assert not panel.is_polling() and not panel.is_recording()
    assert panel._poll_button.text() == "Start polling and record"  # last mode kept

    panel._poll_button.click()  # main action restarts with the last chosen mode
    assert panel.is_polling() and panel.is_recording()
    panel.stop_polling()
    assert states == [(True, False), (True, True), (False, False), (True, True),
                      (False, False)]


def test_clear_series_empties_history(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    token = panel._token_at(0)
    panel.start_polling(True)
    panel.handle_read_finished(_read_row(panel, 0), True, [5], "")
    panel.stop_polling()
    assert len(panel._series[token]) == 1
    panel.clear_series()
    assert len(panel._series[token]) == 0
    # an empty sparkline repaints without crashing
    panel._sparklines[token].grab()
    panel._series[token].append(1.0, 2.0)  # a single point paints fine too
    panel._sparklines[token].grab()


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


def test_display_text_decodes_then_scales(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_state(
        [
            {"name": "t", "kind": "holding_registers", "address": 0, "count": 2,
             "format": "f32", "scale": 0.1, "offset": -40.0, "unit": "°C"},
            {"name": "h", "kind": "holding_registers", "address": 2, "count": 1,
             "format": "hex", "scale": 2.0, "unit": "V"},
        ]
    )
    # Format+Order decode first, then scale/offset/unit
    assert panel._display_text(0, [0x3F80, 0x0000]) == "-39.9 °C"
    # hex bypasses scaling entirely
    assert panel._display_text(1, [0x001A]) == "0x001A"


def test_display_settings_roundtrip_with_order_inherit(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_state(
        [
            {"name": "a", "kind": "holding_registers", "address": 0, "count": 2,
             "scale": 0.1, "offset": -40.0, "unit": "°C", "order": "CDAB"},
            {"name": "b", "kind": "holding_registers", "address": 2, "count": 2},
            {"name": "c", "kind": "holding_registers", "address": 4, "count": 2,
             "order": ""},
        ]
    )
    store = panel._row_display
    assert store[panel._token_at(0)].order == "CDAB"
    assert store[panel._token_at(0)].scale == 0.1
    assert store[panel._token_at(1)].order is None  # missing key = inherit
    assert store[panel._token_at(2)].order is None  # "" = inherit

    state = panel.state()
    assert state[0]["order"] == "CDAB"
    assert state[1]["order"] == ""
    panel.set_state(state)
    store = panel._row_display
    assert store[panel._token_at(0)].order == "CDAB"
    assert store[panel._token_at(0)].unit == "°C"
    assert store[panel._token_at(1)].order is None


def test_display_text_uses_global_order_when_row_inherits(
    qapp: QApplication,
) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_state(
        [
            {"name": "inherit", "kind": "holding_registers", "address": 0,
             "count": 2, "format": "u32"},
            {"name": "explicit", "kind": "holding_registers", "address": 2,
             "count": 2, "format": "u32", "order": "ABCD"},
        ]
    )
    panel._global_order_combo.setCurrentText("CDAB")
    # word-swapped wire data: global CDAB decodes, explicit ABCD does not
    assert panel._display_text(0, [0x0000, 0x3F80]) == "1065353216"
    assert panel._display_text(1, [0x0000, 0x3F80]) == "16256"
    panel._global_order_combo.setCurrentText("ABCD")
    assert panel._display_text(0, [0x0000, 0x3F80]) == "16256"


def test_display_dialog_edits_update_the_store(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_state(
        [{"name": "a", "kind": "holding_registers", "address": 0, "count": 2}]
    )
    panel._on_display_settings()
    dialog = panel._display_dialog
    assert dialog is not None
    table = dialog.findChild(QTableWidget)
    assert table is not None
    scale_item = table.item(0, 2)
    assert scale_item is not None
    scale_item.setText("0.5")
    unit_item = table.item(0, 4)
    assert unit_item is not None
    unit_item.setText("V")
    order_combo = table.cellWidget(0, 5)
    order_combo.setCurrentText("DCBA")
    settings = panel._row_display[panel._token_at(0)]
    assert settings.scale == 0.5
    assert settings.unit == "V"
    assert settings.order == "DCBA"
    order_combo.setCurrentText("default")
    assert settings.order is None  # back to inheriting the global order
    dialog.close()
    assert panel._display_dialog is None


def test_csv_import_replaces_table(qapp: QApplication, tmp_path: Path) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    path = tmp_path / "import.csv"
    path.write_text(
        "name,kind,address,count,scale,order\n"
        "temp,holding_registers,5,2,0.1,CDAB\n"
        "pressure,holding_registers,6,1,,\n",
        encoding="utf-8",
    )
    panel.import_csv(path)
    assert panel._table.rowCount() == 2
    assert panel._table.item(0, COL_NAME).text() == "temp"
    settings = panel._row_display[panel._token_at(0)]
    assert settings.scale == 0.1
    assert settings.order == "CDAB"
    assert panel._row_display[panel._token_at(1)].order is None

    bad = tmp_path / "bad.csv"
    bad.write_text("name,kind,count\nx,coils,1\n", encoding="utf-8")
    panel.import_csv(bad)  # invalid file: table untouched
    assert panel._table.rowCount() == 2


def test_csv_export_roundtrip_and_snapshot(qapp: QApplication, tmp_path: Path) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_state(
        [
            {"name": "temp", "kind": "holding_registers", "address": 5, "count": 2,
             "format": "f32", "scale": 0.1, "offset": -40.0, "unit": "°C"}
        ]
    )
    panel._table.item(0, COL_VALUE).setText("-39.9 °C")

    export_path = tmp_path / "export.csv"
    panel.export_csv(export_path)
    text = export_path.read_text(encoding="utf-8-sig")
    header, line = text.strip().split("\n")
    assert header.endswith(",value")  # snapshot format with the displayed value
    assert line.endswith(",-39.9 °C")

    # the exported snapshot re-imports cleanly: the value column is ignored
    other = RegistersPanel(itertools.count(100).__next__)
    other.import_csv(export_path)
    assert other._table.rowCount() == 1
    exported = other._row_display[other._token_at(0)]
    assert exported.scale == 0.1
    assert exported.offset == -40.0
    assert exported.unit == "°C"
    assert other._table.item(0, COL_VALUE).text() == ""  # value is not imported


def test_export_dialog_drives_column_selection(
    qapp: QApplication, tmp_path: Path
) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    dialog = ExportColumnsDialog(panel)
    list_widget = dialog._list
    order_row = next(
        row for row in range(list_widget.count())
        if list_widget.item(row).text() == "order"
    )
    list_widget.item(order_row).setCheckState(Qt.CheckState.Unchecked)
    dialog._move_current(1)  # current row 0 ("name") moves down one position
    columns = dialog.columns()
    assert "order" not in columns
    assert columns[0] == "kind"
    assert columns[1] == "name"

    path = tmp_path / "subset.csv"
    panel.export_csv(path, columns)
    header = path.read_text(encoding="utf-8-sig").strip().split("\n")[0]
    assert header == ",".join(columns)


def test_import_dialog_mapping_drives_import(
    qapp: QApplication, tmp_path: Path
) -> None:
    path = tmp_path / "scrambled.csv"
    path.write_text("Register Name,type,address\nx,coils,5\n", encoding="utf-8")
    header = csv_header(path.read_text(encoding="utf-8-sig"))
    dialog = ImportMappingDialog(header)
    combos = {
        dialog._table.item(row, 0).text(): dialog._table.cellWidget(row, 1)
        for row in range(dialog._table.rowCount())
    }
    assert combos["type"].currentText() == "kind"  # alias guessed
    assert combos["Register Name"].currentText() == "— skip —"  # unknown: skip
    assert combos["address"].currentText() == "address"
    combos["Register Name"].setCurrentText("name")

    panel = RegistersPanel(itertools.count(1).__next__)
    panel.import_csv(path, dialog.mapping())
    assert panel._table.rowCount() == 1
    assert panel._table.item(0, COL_NAME).text() == "x"
    assert panel._table.cellWidget(0, COL_TYPE).currentText() == "coils"


def test_import_dialog_warns_on_unmapped_essential(qapp: QApplication) -> None:
    dialog = ImportMappingDialog(["comment", "notes"])
    dialog._validate()  # nothing mapped to name/kind/address
    assert not dialog._warning.isHidden()
    assert "name" in dialog._warning.text()
    assert dialog.result() == 0  # still open, not accepted


def test_per_row_poll_gets_own_timer(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_state(
        [
            {"name": "global_row", "kind": "holding_registers", "address": 0, "count": 1},
            {"name": "slow", "kind": "holding_registers", "address": 1, "count": 1,
             "poll_ms": "5000"},
        ]
    )
    panel._toggle_polling()
    global_token, slow_token = panel._token_at(0), panel._token_at(1)
    assert slow_token in panel._row_timers
    timer = panel._row_timers[slow_token]
    assert timer.isActive()
    assert timer.interval() == 5000
    assert global_token not in panel._row_timers  # stays on the global tick
    panel.stop_polling()
    assert panel._row_timers == {}  # all timers torn down
    assert not timer.isActive()


def test_poll_cell_edit_swaps_timer(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel._toggle_polling()
    token = panel._token_at(0)
    item = panel._table.item(0, COL_POLL)
    assert item is not None
    assert token not in panel._row_timers

    item.setText("200")  # valid interval: row gets its own timer
    assert token in panel._row_timers
    assert panel._row_timers[token].interval() == 200

    item.setText("5000")  # editing restarts the timer with the new interval
    assert panel._row_timers[token].interval() == 5000

    item.setText("junk")  # invalid: back to the global tick
    assert token not in panel._row_timers

    item.setText("300")
    assert token in panel._row_timers
    item.setText("")  # empty: back to the global tick
    assert token not in panel._row_timers
    panel.stop_polling()


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


# --- logging to file ---------------------------------------------------------


def test_logging_starts_polling_and_writes_csv(
    qapp: QApplication, tmp_path: Path
) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_state(
        [{"name": "temp", "kind": "holding_registers", "address": 5, "count": 2,
          "format": "f32", "scale": 0.1}]
    )
    path = tmp_path / "values.csv"
    panel.set_logging_state({"path": str(path), "format": "csv"})

    panel.start_logging()  # polling was off: logging starts it
    assert panel.is_logging()
    assert panel.is_polling() and panel.is_recording()  # default split mode is record
    assert panel._log_button.isChecked()

    panel.handle_read_finished(_read_row(panel, 0), True, [17163, 52429], "")
    panel.stop_logging()
    assert not panel.is_logging()
    assert panel.is_polling()  # polling keeps running after logging stops
    panel.stop_polling()

    rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
    assert rows[0] == ["timestamp", "name", "address", "kind", "value"]
    timestamp, name, address, kind, value = rows[1]
    assert "T" in timestamp  # ISO 8601
    assert (name, address, kind) == ("temp", "5", "holding_registers")
    assert value == "13.98"  # f32-decoded (139.8), scaled by 0.1, no unit suffix


def test_logging_poll_only_mode_starts_without_recording(
    qapp: QApplication, tmp_path: Path
) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.start_polling(False)  # remember the poll-only split mode
    panel.stop_polling()

    panel.set_logging_state({"path": str(tmp_path / "values.csv")})
    panel.start_logging()
    assert panel.is_polling() and not panel.is_recording()
    panel.stop_logging()
    panel.stop_polling()


def test_logging_jsonl_logs_bits_and_multi_values(
    qapp: QApplication, tmp_path: Path
) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_state(
        [
            {"name": "valve", "kind": "coils", "address": 0, "count": 4},
            {"name": "pair", "kind": "holding_registers", "address": 10, "count": 2},
        ]
    )
    path = tmp_path / "values.jsonl"
    panel.set_logging_state(
        {"path": str(path), "format": "jsonl", "fields": ["name", "address"]}
    )
    panel.start_logging()
    panel.handle_read_finished(_read_row(panel, 0), True, [True, False, True], "")
    panel.handle_read_finished(_read_row(panel, 1), True, [3, 4], "")
    panel.stop_logging()
    panel.stop_polling()

    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert records[0] == {"name": "valve", "address": 0, "value": "1;0;1"}
    assert records[1] == {"name": "pair", "address": 10, "value": "3;4"}


def test_logging_open_error_stays_off(qapp: QApplication, tmp_path: Path) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    lines: list[str] = []
    panel.logLine.connect(lines.append)
    panel.set_logging_state({"path": str(tmp_path / "no_such_dir" / "values.csv")})

    panel.start_logging()
    assert not panel.is_logging()
    assert not panel.is_polling()  # no reads are needed when there is no file
    assert not panel._log_button.isChecked()
    assert any("cannot open" in line for line in lines)


def test_logging_state_roundtrip(qapp: QApplication, tmp_path: Path) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_logging_state(
        {"path": str(tmp_path / "x.jsonl"), "format": "jsonl",
         "fields": ["kind", "name"], "append": False}
    )
    state = panel.logging_state()
    assert state == {"path": str(tmp_path / "x.jsonl"), "format": "jsonl",
                     "fields": ["name", "kind"], "append": False}  # canonical order

    fresh = RegistersPanel(itertools.count(100).__next__)
    fresh.set_logging_state(state)
    assert fresh.logging_state() == state
    fresh.set_logging_state({"format": "junk", "fields": "oops"})  # tolerated
    assert fresh.logging_state() == state


def test_log_flag_state_roundtrip(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_state(
        [
            {"name": "a", "kind": "holding_registers", "address": 0, "count": 1,
             "log": False},
            {"name": "b", "kind": "holding_registers", "address": 1, "count": 1},
        ]
    )
    assert panel._row_display[panel._token_at(0)].log is False
    assert panel._row_display[panel._token_at(1)].log is True  # missing key → True

    state = panel.state()
    assert [entry["log"] for entry in state] == [False, True]

    fresh = RegistersPanel(itertools.count(100).__next__)
    fresh.set_state(state)
    assert fresh._row_display[fresh._token_at(0)].log is False
    assert fresh._row_display[fresh._token_at(1)].log is True


def test_logging_dialog_row_checklist(qapp: QApplication) -> None:
    rows = [(1, "temp @ 0", True), (2, "coils@3 (unit 5)", False)]
    dialog = LoggingSettingsDialog(LogSettings(), rows)
    list_widget = dialog._rows_list
    assert list_widget.count() == 2
    assert list_widget.item(0).text() == "temp @ 0"
    assert list_widget.item(0).checkState() == Qt.CheckState.Checked
    assert list_widget.item(1).checkState() == Qt.CheckState.Unchecked
    assert dialog.row_flags() == {1: True, 2: False}

    dialog._set_all_rows(Qt.CheckState.Unchecked)
    assert dialog.row_flags() == {1: False, 2: False}
    dialog._set_all_rows(Qt.CheckState.Checked)
    assert dialog.row_flags() == {1: True, 2: True}

    list_widget.setCurrentRow(0)  # Space toggles the current item natively
    event = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier
    )
    QApplication.sendEvent(list_widget.viewport(), event)
    assert dialog.row_flags() == {1: False, 2: True}


def test_logging_skips_unchecked_rows(qapp: QApplication, tmp_path: Path) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_state(
        [
            {"name": "a", "kind": "holding_registers", "address": 0, "count": 1},
            {"name": "b", "kind": "holding_registers", "address": 1, "count": 1},
        ]
    )

    # the dialog reflects the current flags; uncheck row "b" through its API
    dialog = LoggingSettingsDialog(LogSettings(), panel._log_row_entries(), panel)
    entries = panel._log_row_entries()
    assert [label for _, label, _ in entries] == ["a @ 0", "b @ 1"]
    assert all(checked for _, _, checked in entries)
    assert dialog._rows_list.count() == 2
    dialog._rows_list.item(1).setCheckState(Qt.CheckState.Unchecked)
    panel._apply_log_row_flags(dialog.row_flags())

    path = tmp_path / "values.csv"
    panel.set_logging_state({"path": str(path), "fields": ["name"]})
    panel.start_logging()
    panel.handle_read_finished(_read_row(panel, 0), True, [10], "")
    panel.handle_read_finished(_read_row(panel, 1), True, [20], "")
    panel.stop_logging()
    panel.stop_polling()

    rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
    assert rows == [["name", "value"], ["a", "10"]]  # "b" excluded

    # "select none" → only the header lands in a fresh file
    dialog = LoggingSettingsDialog(LogSettings(), panel._log_row_entries(), panel)
    dialog._set_all_rows(Qt.CheckState.Unchecked)
    panel._apply_log_row_flags(dialog.row_flags())
    none_path = tmp_path / "none.csv"
    panel.set_logging_state({"path": str(none_path), "fields": ["name"]})
    panel.start_logging()
    panel.handle_read_finished(_read_row(panel, 0), True, [11], "")
    panel.stop_logging()
    panel.stop_polling()
    assert none_path.read_text(encoding="utf-8").splitlines() == ["name,value"]

    # a row added after the selection defaults to logged
    panel._add_row(RegisterRow(name="c", kind="holding_registers", address=2))
    new_token = panel._token_at(panel._table.rowCount() - 1)
    assert panel._row_display[new_token].log is True
