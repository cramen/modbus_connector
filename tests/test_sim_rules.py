import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Qt needs system libraries (libEGL etc.) that headless Linux CI runners lack;
# skip the whole module there — it still runs on macOS/Windows CI and dev machines.
try:
    from PySide6.QtCore import Qt  # noqa: E402
    from PySide6.QtTest import QSignalSpy  # noqa: E402
    from PySide6.QtWidgets import QApplication  # noqa: E402
except ImportError as exc:
    pytest.skip(f"Qt system libraries not available: {exc}", allow_module_level=True)

from modbus_connector.models import decode_register_values  # noqa: E402
from modbus_connector.sim_panel import (  # noqa: E402
    COL_RULE,
    COL_RULE_TEXT,
    COL_VALUE,
    SimPanel,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def panel(qapp: QApplication) -> SimPanel:
    widget = SimPanel()
    yield widget
    widget.deleteLater()
    qapp.processEvents()


def _set_rule(panel: SimPanel, index: int, text: str) -> None:
    panel._table.cellWidget(index, COL_RULE).setCurrentIndex(1)  # expression
    panel._table.item(index, COL_RULE_TEXT).setText(text)


def test_rule_state_roundtrip(panel: SimPanel) -> None:
    panel.set_state(
        {
            "tick_ms": 250,
            "rows": [
                {"name": "a", "kind": "holding_registers", "address": 0,
                 "values": [1], "rule": "expression", "rule_text": "prev + 1"},
                {"name": "b", "kind": "coils", "address": 0, "values": [True]},
                {"name": "c", "kind": "coils", "address": 1, "values": [False],
                 "rule": "junk", "rule_text": "оставлен"},
            ],
        }
    )
    collected = panel.state()
    assert collected["tick_ms"] == 250
    rows = collected["rows"]
    assert rows[0]["rule"] == "expression"
    assert rows[0]["rule_text"] == "prev + 1"
    assert rows[1]["rule"] == "manual"
    assert rows[1]["rule_text"] == ""
    # невалидный режим → manual, текст правила не хранится
    assert rows[2]["rule"] == "manual"
    assert rows[2]["rule_text"] == ""


def test_tick_ms_clamped_and_signal(panel: SimPanel) -> None:
    spy = QSignalSpy(panel.setTickIntervalRequested)
    panel._tick_spin.setValue(500)
    assert spy.count() == 1
    assert spy.at(0)[0] == 500
    panel.set_state({"tick_ms": "junk"})
    assert panel.state()["tick_ms"] == 500  # мусор — прежнее значение
    panel.set_state({"tick_ms": 5})
    assert panel.state()["tick_ms"] == 100  # обрезано до диапазона spinbox'а


def test_invalid_rule_shows_warning(panel: SimPanel) -> None:
    panel._add_row({"kind": "holding_registers", "address": 0, "values": [7],
                    "rule": "expression", "rule_text": "1 +"})
    item = panel._table.item(0, COL_VALUE)
    assert item.text() == "⚠"
    assert item.toolTip()  # текст ошибки парсера
    # apply_rules строку пропускает: записи в backend нет
    spy = QSignalSpy(panel.setValuesRequested)
    panel.apply_rules()
    assert spy.count() == 0
    assert item.text() == "⚠"


def test_rule_cells_editable_gating(panel: SimPanel) -> None:
    panel._add_row({"kind": "holding_registers", "address": 0, "values": [1]})
    value_item = panel._table.item(0, COL_VALUE)
    text_item = panel._table.item(0, COL_RULE_TEXT)
    editable = Qt.ItemFlag.ItemIsEditable
    enabled = Qt.ItemFlag.ItemIsEnabled
    assert value_item.flags() & editable
    assert not (text_item.flags() & enabled)
    combo = panel._table.cellWidget(0, COL_RULE)
    combo.setCurrentIndex(1)  # expression
    assert not (value_item.flags() & editable)
    assert text_item.flags() & editable
    text_item.setText("[a] * 2")
    combo.setCurrentIndex(0)  # обратно manual — текст правила очищается
    assert text_item.text() == ""
    assert not (text_item.flags() & enabled)
    assert value_item.flags() & editable


def test_apply_rules_reference(panel: SimPanel) -> None:
    panel._add_row({"name": "a", "kind": "holding_registers", "address": 0,
                    "values": [3]})
    panel._add_row({"name": "b", "kind": "holding_registers", "address": 10,
                    "values": [0]})
    _set_rule(panel, 1, "[a] * 2")
    spy = QSignalSpy(panel.setValuesRequested)
    panel.apply_rules()
    assert spy.count() == 1
    kind, address, values = spy.at(0)
    assert (kind, address, list(values)) == ("holding_registers", 10, [6])
    assert panel._table.item(1, COL_VALUE).text() == "6"


def test_apply_rules_prev_counter(panel: SimPanel) -> None:
    panel._add_row({"name": "cnt", "kind": "holding_registers", "address": 0,
                    "values": [5]})
    _set_rule(panel, 0, "prev + 1")
    spy = QSignalSpy(panel.setValuesRequested)
    panel.apply_rules()  # первый тик: prev = текущее значение (5)
    panel.apply_rules()
    panel.apply_rules()
    assert [list(spy.at(i)[2]) for i in range(3)] == [[6], [7], [8]]


def test_apply_rules_t_seconds(panel: SimPanel) -> None:
    panel._add_row({"name": "elapsed", "kind": "holding_registers", "address": 0,
                    "values": [0]})
    _set_rule(panel, 0, "t")
    panel._started_at = time.monotonic() - 5  # как будто сервер стартовал 5 с назад
    panel.apply_rules()
    assert panel._table.item(0, COL_VALUE).text() == "5"


def test_apply_rules_rand(panel: SimPanel) -> None:
    panel._add_row({"name": "r1", "kind": "holding_registers", "address": 0,
                    "values": [0], "format": "f32", "count": 2})
    panel._add_row({"name": "r2", "kind": "holding_registers", "address": 2,
                    "values": [0]})
    _set_rule(panel, 0, "rand()")
    _set_rule(panel, 1, "randint(5, 5)")  # детерминированно
    spy = QSignalSpy(panel.setValuesRequested)
    panel.apply_rules()
    assert spy.count() == 2
    rand_regs = list(spy.at(0)[2])
    value = decode_register_values(rand_regs, "f32")[0]
    assert 0.0 <= value < 1.0
    assert list(spy.at(1)[2]) == [5]


def test_nan_shows_dash_and_skips_write(panel: SimPanel) -> None:
    panel._add_row({"name": "bad", "kind": "holding_registers", "address": 0,
                    "values": [9]})
    _set_rule(panel, 0, "1/0")
    spy = QSignalSpy(panel.setValuesRequested)
    panel.apply_rules()
    assert spy.count() == 0  # datastore не тронут
    assert panel._table.item(0, COL_VALUE).text() == "—"
    # prev не обновлён: следующий тик с валидным правилом снова берёт значение строки
    panel._table.item(0, COL_RULE_TEXT).setText("prev + 1")
    panel.apply_rules()
    assert list(spy.at(0)[2]) == [10]


def test_missing_dependency_shows_dash(panel: SimPanel) -> None:
    panel._add_row({"name": "x", "kind": "holding_registers", "address": 0,
                    "values": [1]})
    _set_rule(panel, 0, "[ghost] + 1")
    spy = QSignalSpy(panel.setValuesRequested)
    panel.apply_rules()
    assert spy.count() == 0
    assert panel._table.item(0, COL_VALUE).text() == "—"


def test_encode_formats(panel: SimPanel) -> None:
    panel._add_row({"name": "f", "kind": "holding_registers", "address": 0,
                    "count": 2, "format": "f32", "values": [0, 0]})
    panel._add_row({"name": "u", "kind": "holding_registers", "address": 2,
                    "count": 2, "format": "u32", "values": [0, 0]})
    panel._add_row({"name": "s", "kind": "holding_registers", "address": 4,
                    "format": "s16", "values": [0]})
    panel._add_row({"name": "c", "kind": "coils", "address": 0,
                    "count": 2, "values": [False, False]})
    _set_rule(panel, 0, "1.5")
    _set_rule(panel, 1, "70000 + 0.4")  # u32 без clamp, round
    _set_rule(panel, 2, "40000")  # s16 clamp до 32767
    _set_rule(panel, 3, "0.6")  # round → 1
    spy = QSignalSpy(panel.setValuesRequested)
    panel.apply_rules()
    assert decode_register_values(list(spy.at(0)[2]), "f32") == [1.5]
    assert decode_register_values(list(spy.at(1)[2]), "u32") == [70000]
    assert list(spy.at(2)[2]) == [0x7FFF]
    assert list(spy.at(3)[2]) == [True, True]
