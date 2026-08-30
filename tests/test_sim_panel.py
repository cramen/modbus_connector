import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Qt needs system libraries (libEGL etc.) that headless Linux CI runners lack;
# skip the whole module there — it still runs on macOS/Windows CI and dev machines.
try:
    from PySide6.QtTest import QSignalSpy  # noqa: E402
    from PySide6.QtWidgets import QApplication, QToolButton  # noqa: E402
except ImportError as exc:
    pytest.skip(f"Qt system libraries not available: {exc}", allow_module_level=True)

from modbus_connector.sim_backend import SimTcpParams  # noqa: E402
from modbus_connector.sim_panel import (  # noqa: E402
    COL_ACTIONS,
    COL_COUNT,
    COL_FORMAT,
    COL_VALUE,
    SimPanel,
)
from modbus_connector.templates import list_templates, load_template  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def panel(qapp: QApplication) -> SimPanel:
    widget = SimPanel()
    yield widget
    widget.deleteLater()
    qapp.processEvents()


def test_add_remove_rows(panel: SimPanel) -> None:
    panel._add_row({"name": "t", "kind": "holding_registers", "address": 10, "count": 2})
    assert panel._table.rowCount() == 1
    assert panel._table.item(0, COL_VALUE).text() == "0, 0"
    panel._add_row({"kind": "coils", "address": 0, "count": 3})
    assert panel._table.item(1, COL_VALUE).text() == "0, 0, 0"
    delete = panel._table.cellWidget(0, COL_ACTIONS).findChild(QToolButton)
    delete.click()
    assert panel._table.rowCount() == 1


def test_state_roundtrip(panel: SimPanel) -> None:
    panel.set_state(
        {
            "server": {
                "type": "RTU",
                "rtu_port": "/dev/ttyFAKE0",
                "rtu_baud": "19200",
                "rtu_parity": "E",
                "unit": 5,
            },
            "rows": [
                {"name": "temp", "kind": "holding_registers", "address": 5,
                 "count": 2, "format": "f32", "values": [100, 200]},
                {"name": "flag", "kind": "coils", "address": 0, "count": 1,
                 "format": "dec", "values": [True]},
            ],
        }
    )
    collected = panel.state()
    server = collected["server"]
    assert server["type"] == "RTU"
    assert server["rtu_port"] == "/dev/ttyFAKE0"
    assert server["rtu_baud"] == "19200"
    assert server["rtu_parity"] == "E"
    assert server["unit"] == 5
    rows = collected["rows"]
    assert rows[0] == {"name": "temp", "kind": "holding_registers", "address": 5,
                       "count": 2, "format": "f32", "values": [100, 200],
                       "rule": "manual", "rule_text": "", "value_names": {},
                       "bitmask": False}
    assert rows[1]["values"] == [True]


def test_state_tolerant_parsing(panel: SimPanel) -> None:
    panel.set_state("junk")  # не dict — игнорируется
    panel.set_state(
        {
            "server": {"type": "MARS", "port": "junk", "unit": "junk"},
            "rows": [
                "junk",
                {"kind": "unknown", "address": "junk", "count": "junk",
                 "values": ["junk", 7, True, 0x1FFFF]},
            ],
        }
    )
    server = panel.state()["server"]
    assert server["type"] == "TCP"  # дефолт, неизвестный тип не применился
    assert server["port"] == 1502
    assert server["unit"] == "any"
    rows = panel.state()["rows"]
    assert len(rows) == 1
    # мусор в values отброшен, добито нулями до count=1 (дефолт)
    assert rows[0]["kind"] == "holding_registers"
    assert rows[0]["values"] == [7, 1]


def test_template_load_into_map(panel: SimPanel) -> None:
    infos = list_templates()
    assert infos, "в каталоге должен быть хотя бы один шаблон"
    panel._apply_template(infos[0])
    template = load_template(infos[0])
    rows = panel.state()["rows"]
    assert len(rows) == len(template["registers"])
    first = template["registers"][0]
    assert rows[0]["name"] == first["name"]
    assert rows[0]["kind"] == first.get("kind", "holding_registers")
    assert rows[0]["address"] == first["address"]
    assert rows[0]["values"] == [0] * first.get("count", 1)
    # повторное применение — все строки дубли, карта не растёт
    panel._apply_template(infos[0])
    assert len(panel.state()["rows"]) == len(template["registers"])


def test_master_write_updates_value(panel: SimPanel) -> None:
    panel._add_row({"kind": "holding_registers", "address": 10, "count": 2})
    panel.handle_master_write("holding_registers", 10, [42, 43])
    assert panel._table.item(0, COL_VALUE).text() == "42, 43"
    # частичное перекрытие: только второй регистр строки
    panel.handle_master_write("holding_registers", 11, [7])
    assert panel._table.item(0, COL_VALUE).text() == "42, 7"
    # другая область и адрес вне диапазона — без изменений
    panel.handle_master_write("coils", 10, [True])
    panel.handle_master_write("holding_registers", 99, [1])
    assert panel._table.item(0, COL_VALUE).text() == "42, 7"
    # битовая область
    panel._add_row({"kind": "coils", "address": 4, "count": 2})
    panel.handle_master_write("coils", 4, [True])
    assert panel._table.item(1, COL_VALUE).text() == "1, 0"


def test_edit_value_emits_set_values(panel: SimPanel) -> None:
    panel._add_row({"kind": "holding_registers", "address": 5, "count": 2})
    spy = QSignalSpy(panel.setValuesRequested)
    panel._table.item(0, COL_VALUE).setText("1, 2")
    assert spy.count() == 1
    kind, address, values = spy.at(0)
    assert (kind, address, list(values)) == ("holding_registers", 5, [1, 2])
    # невалидный ввод — откат отображения, новой записи нет
    panel._table.item(0, COL_VALUE).setText("junk")
    assert spy.count() == 1
    assert panel._table.item(0, COL_VALUE).text() == "1, 2"


def test_start_emits_params_and_pushes_rows(panel: SimPanel) -> None:
    panel.set_state(
        {"server": {"type": "TCP", "host": "0.0.0.0", "port": 1503, "unit": "any"},
         "rows": [{"kind": "holding_registers", "address": 3, "count": 1,
                   "values": [55]}]}
    )
    start_spy = QSignalSpy(panel.startRequested)
    values_spy = QSignalSpy(panel.setValuesRequested)
    panel._button.click()
    assert start_spy.count() == 1
    params, unit = start_spy.at(0)
    assert isinstance(params, SimTcpParams)
    assert (params.host, params.port) == ("0.0.0.0", 1503)
    assert unit is None
    # значения карты ушли в backend до старта сервера
    assert values_spy.count() == 1
    assert list(values_spy.at(0)[2]) == [55]


def test_set_running_ui(panel: SimPanel) -> None:
    panel._button.click()  # запоминает params для running_description
    panel.set_running(True, "Simulator running (tcp 127.0.0.1:1502)")
    assert panel._button.text() == "Stop server"
    assert not panel._type_combo.isEnabled()
    assert panel.running_description() == "sim tcp 127.0.0.1:1502"
    panel.handle_client_changed(True, "127.0.0.1:5000")
    assert "1" in panel._status.text()
    panel.set_running(False, "Stopped")
    assert panel._button.text() == "Start server"
    assert panel._type_combo.isEnabled()
    assert panel.running_description() is None


def test_stop_requested_when_running(panel: SimPanel) -> None:
    panel.set_running(True, "Simulator running (tcp 127.0.0.1:1502)")
    spy = QSignalSpy(panel.stopRequested)
    panel._button.click()
    assert spy.count() == 1


def test_add_manual_row_logs_no_parse_error(panel: SimPanel) -> None:
    # setFlags в _sync_rule_cells эмитит itemChanged — не должен доходить
    # до _commit_value (ложный «parse error» на ещё пустой ячейке Value)
    lines: list[str] = []
    panel.logLine.connect(lines.append)
    panel._add_row({"name": "t", "kind": "holding_registers", "address": 0,
                    "count": 1, "format": "dec", "values": [5], "rule": "manual"})
    assert lines == []
    assert panel._table.item(0, COL_VALUE).text() == "5"


def test_help_button_opens_simulator_help(panel: SimPanel) -> None:
    from PySide6.QtWidgets import QDialog

    from modbus_connector.i18n import tr

    panel._help_button.click()
    dialogs = [d for d in panel.findChildren(QDialog) if d.isVisible()]
    assert len(dialogs) == 1
    assert dialogs[0].windowTitle() == tr("Simulator — Help")
    dialogs[0].close()


def test_wider_format_bumps_count(panel: SimPanel) -> None:
    # f32 требует 2 регистра: выбор формата поднимает count 1 → 2, значения сохраняются
    panel._add_row({"name": "h", "kind": "input_registers", "address": 5,
                    "count": 1, "format": "s16", "values": [22]})
    panel._table.cellWidget(0, COL_FORMAT).setCurrentText("f32")
    assert panel._table.item(0, COL_COUNT).text() == "2"
    assert panel._values_at(0) == [22, 0]


def test_count_edit_below_format_width_clamps(panel: SimPanel) -> None:
    panel._add_row({"name": "h", "kind": "input_registers", "address": 5,
                    "count": 2, "format": "f32", "values": [16215, 46602]})
    panel._table.item(0, COL_COUNT).setText("1")  # commit через itemChanged
    assert panel._table.item(0, COL_COUNT).text() == "2"
    assert panel._values_at(0) == [16215, 46602]


def test_state_normalizes_count_to_format_width(panel: SimPanel) -> None:
    panel.set_state({
        "server": {},
        "rows": [{"name": "h", "kind": "input_registers", "address": 5,
                  "count": 1, "format": "f32", "values": [16215]}],
    })
    assert panel._table.item(0, COL_COUNT).text() == "2"
    assert panel._values_at(0) == [16215, 0]


def test_edit_value_accepts_float_for_f32(panel: SimPanel) -> None:
    # ввод в формате отображения: "0.1" кодируется в пару регистров f32
    payloads: list = []
    panel.setValuesRequested.connect(lambda *args: payloads.append(args))
    panel._add_row({"name": "h", "kind": "input_registers", "address": 5,
                    "count": 2, "format": "f32", "values": [0, 0]})
    panel._table.item(0, COL_VALUE).setText("0.1")
    values = panel._values_at(0)
    assert len(values) == 2
    assert abs(panel._primary_number(0) - 0.1) < 1e-6
    assert panel._table.item(0, COL_VALUE).text() == "0.1"
    assert payloads and payloads[-1][1:] == (5, values)


def test_edit_value_multiple_numbers_per_groups(panel: SimPanel) -> None:
    # count=4 = два значения f32: "0.5, 2.5" → 4 регистра
    panel._add_row({"name": "h", "kind": "input_registers", "address": 5,
                    "count": 4, "format": "f32", "values": [0, 0, 0, 0]})
    panel._table.item(0, COL_VALUE).setText("0.5, 2.5")
    values = panel._values_at(0)
    assert len(values) == 4
    decoded = panel._primary_number(0)
    assert abs(decoded - 0.5) < 1e-6


def test_edit_value_bad_float_keeps_old_values(panel: SimPanel) -> None:
    lines: list[str] = []
    panel.logLine.connect(lines.append)
    panel._add_row({"name": "h", "kind": "input_registers", "address": 5,
                    "count": 2, "format": "f32", "values": [16215, 46602]})
    before = panel._values_at(0)
    panel._table.item(0, COL_VALUE).setText("abc")
    assert panel._values_at(0) == before  # откат к сохранённым
    assert any("parse error" in line for line in lines)


def test_edit_value_accepts_text_for_ascii(panel: SimPanel) -> None:
    panel._add_row({"name": "dev", "kind": "holding_registers", "address": 16,
                    "count": 4, "format": "ascii", "values": [0, 0, 0, 0]})
    panel._table.item(0, COL_VALUE).setText("MC-42")
    assert panel._table.item(0, COL_VALUE).text() == "MC-42"
    assert panel._values_at(0)[:3] == [0x4D43, 0x2D34, 0x3200]


def test_master_write_flashes_value_cell(panel: SimPanel) -> None:
    from modbus_connector import theme

    panel._add_row({"name": "t", "kind": "holding_registers", "address": 10,
                    "count": 1, "values": [5]})
    panel.handle_master_write("holding_registers", 10, [42])
    item = panel._table.item(0, COL_VALUE)
    assert item.text() == "42"
    assert item.background() == theme.flash_color()  # зелёная вспышка
    # очистка по поколению — фон сброшен
    panel._clear_flash(0, panel._flash_generations[0])
    assert item.background() != theme.flash_color()
    # устаревшее поколение не сбрасывает свежую вспышку
    panel.handle_master_write("holding_registers", 10, [43])
    panel._clear_flash(0, panel._flash_generations[0] - 1)
    assert item.background() == theme.flash_color()


def test_edit_value_accepts_text_for_ascii1(panel: SimPanel) -> None:
    # ascii1: один символ на регистр (конвенция строк Wiren Board)
    panel._add_row({"name": "model", "kind": "holding_registers", "address": 200,
                    "count": 20, "format": "ascii1", "values": [0] * 20})
    panel._table.item(0, COL_VALUE).setText("WBMSW4")
    assert panel._table.item(0, COL_VALUE).text() == "WBMSW4"
    assert panel._values_at(0)[:6] == [ord(c) for c in "WBMSW4"]


def test_value_names_state_roundtrip(panel: SimPanel) -> None:
    panel.set_state(
        {"rows": [
            {"name": "pump", "kind": "holding_registers", "address": 10, "count": 1,
             "values": [2], "value_names": {"0": "Stopped", "2": "Pump running"}},
            {"name": "plain", "kind": "holding_registers", "address": 11, "count": 1},
        ]}
    )
    state = panel.state()
    assert state["rows"][0]["value_names"] == {"0": "Stopped", "2": "Pump running"}
    assert state["rows"][1]["value_names"] == {}
    combo = panel._table.cellWidget(0, COL_VALUE)
    assert combo is not None and not combo.isHidden()
    assert combo.currentText() == "2 = Pump running"  # комбо — и отображение тоже
    assert panel._table.cellWidget(1, COL_VALUE) is None  # без names — текст


def test_value_names_combo_writes_datastore(panel: SimPanel) -> None:
    panel._add_row({"kind": "holding_registers", "address": 5, "count": 1,
                    "values": [0], "value_names": {"0": "Off", "1": "On"}})
    spy = QSignalSpy(panel.setValuesRequested)
    combo = panel._table.cellWidget(0, COL_VALUE)
    combo.activated[int].emit(combo.findData(1))
    assert spy.count() == 1
    kind, address, values = spy.at(0)
    assert (kind, address, list(values)) == ("holding_registers", 5, [1])
    assert panel._values_at(0) == [1]
    assert combo.currentText() == "1 = On"  # комбо остаётся на записанном
    combo.activated[int].emit(combo.findData(1))  # повторный выбор пишет снова
    assert spy.count() == 2


def test_value_names_hex_display_and_combo_write(panel: SimPanel) -> None:
    # enum работает и для hex-строк: имя с десятичным числом, комбо пишет число
    panel._add_row({"kind": "holding_registers", "address": 5, "count": 1,
                    "format": "hex", "values": [2],
                    "value_names": {"0": "Off", "2": "On"}})
    combo = panel._table.cellWidget(0, COL_VALUE)
    assert combo is not None
    assert combo.currentText() == "2 = On"  # число в имени десятичное
    spy = QSignalSpy(panel.setValuesRequested)
    combo.activated[int].emit(combo.findData(0))
    kind, address, values = spy.at(0)
    assert (kind, address, list(values)) == ("holding_registers", 5, [0])
    assert panel._values_at(0) == [0]


def test_value_names_combo_coils_bool(panel: SimPanel) -> None:
    panel._add_row({"kind": "coils", "address": 0, "count": 1,
                    "values": [True], "value_names": {"0": "Off", "1": "On"}})
    combo = panel._table.cellWidget(0, COL_VALUE)
    assert combo.currentText() == "1 = On"
    spy = QSignalSpy(panel.setValuesRequested)
    combo.activated[int].emit(combo.findData(0))
    kind, address, values = spy.at(0)
    assert (kind, address, list(values)) == ("coils", 0, [False])
    assert isinstance(values[0], bool)
    assert panel._values_at(0) == [False]
    assert combo.currentText() == "0 = Off"


def test_value_names_dialog(panel: SimPanel, monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtWidgets import QDialog, QPlainTextEdit

    panel._add_row({"kind": "holding_registers", "address": 0, "count": 1,
                    "values": [1]})
    panel._table.setCurrentCell(0, COL_VALUE)
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(QPlainTextEdit, "toPlainText", lambda self: "0=Off\n1=On\njunk")
    panel._on_value_names()
    assert panel._value_names_at(0) == {0: "Off", 1: "On"}
    combo = panel._table.cellWidget(0, COL_VALUE)
    assert combo is not None and combo.currentText() == "1 = On"


def test_value_names_button_disabled_without_rows(panel: SimPanel) -> None:
    assert not panel._names_button.isEnabled()
    panel._add_row({"kind": "coils", "address": 0, "count": 1})
    assert panel._names_button.isEnabled()
    delete = panel._table.cellWidget(0, COL_ACTIONS).findChild(QToolButton)
    delete.click()
    assert not panel._names_button.isEnabled()


def test_value_names_expression_row_display(panel: SimPanel) -> None:
    panel._add_row({"kind": "holding_registers", "address": 0, "count": 1,
                    "rule": "expression", "rule_text": "1",
                    "value_names": {"1": "On"}})
    panel._apply_rule(0, {}, 0.0)
    assert panel._table.item(0, COL_VALUE).text() == "On (1)"
    assert panel._table.cellWidget(0, COL_VALUE) is None  # у expression комбо нет


def test_value_names_not_applied_to_wide_rows(panel: SimPanel) -> None:
    panel._add_row({"kind": "holding_registers", "address": 0, "count": 2,
                    "values": [1, 0], "value_names": {"1": "On"}})
    assert panel._table.cellWidget(0, COL_VALUE) is None  # count>1 — без комбо
    assert panel._table.item(0, COL_VALUE).text() == "1, 0"


def test_bitmask_state_roundtrip_and_display(panel: SimPanel) -> None:
    panel.set_state(
        {"rows": [
            {"name": "flags", "kind": "holding_registers", "address": 10, "count": 1,
             "values": [5], "value_names": {"0": "Running", "2": "Alarm"},
             "bitmask": True},
            {"name": "plain", "kind": "holding_registers", "address": 11, "count": 1},
        ]}
    )
    state = panel.state()
    assert state["rows"][0]["bitmask"] is True
    assert state["rows"][1]["bitmask"] is False  # ключ отсутствовал — default False
    button = panel._table.cellWidget(0, COL_VALUE)
    assert isinstance(button, QToolButton) and not button.isHidden()
    assert button.text() == "Running, Alarm (0000 0000 0000 0101)"
    assert panel._table.item(0, COL_VALUE).toolTip() == "Running, Alarm (0000 0000 0000 0101)"
    assert panel._table.cellWidget(1, COL_VALUE) is None  # без bitmask — текст


def test_bitmask_button_writes_datastore(
    panel: SimPanel, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QDialog

    from modbus_connector.bits_dialog import BitsDialog

    panel._add_row({"kind": "holding_registers", "address": 5, "count": 1,
                    "values": [0], "value_names": {"0": "Running"}, "bitmask": True})

    def fake_exec(dialog: BitsDialog) -> QDialog.DialogCode:
        dialog._boxes[1].setChecked(True)
        dialog._boxes[2].setChecked(True)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(BitsDialog, "exec", fake_exec)
    spy = QSignalSpy(panel.setValuesRequested)
    button = panel._table.cellWidget(0, COL_VALUE)
    button.click()
    assert spy.count() == 1
    kind, address, values = spy.at(0)
    assert (kind, address, list(values)) == ("holding_registers", 5, [0b110])
    assert panel._values_at(0) == [0b110]
    assert button.text() == "b1, b2 (0000 0000 0000 0110)"  # сводка обновилась


def test_bitmask_master_write_updates_display(panel: SimPanel) -> None:
    panel._add_row({"kind": "holding_registers", "address": 5, "count": 1,
                    "values": [0], "value_names": {"0": "Running", "2": "Alarm"},
                    "bitmask": True})
    panel.handle_master_write("holding_registers", 5, [0b101])
    button = panel._table.cellWidget(0, COL_VALUE)
    assert button.text() == "Running, Alarm (0000 0000 0000 0101)"


def test_bitmask_expression_row_no_button(panel: SimPanel) -> None:
    panel._add_row({"kind": "holding_registers", "address": 0, "count": 1,
                    "rule": "expression", "rule_text": "5",
                    "value_names": {"0": "Running", "2": "Alarm"}, "bitmask": True})
    panel._apply_rule(0, {}, 0.0)
    assert panel._table.item(0, COL_VALUE).text() == "Running, Alarm (0000 0000 0000 0101)"
    assert panel._table.cellWidget(0, COL_VALUE) is None  # expression — только текст


def test_bitmask_checkbox_in_names_dialog(
    panel: SimPanel, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QCheckBox, QDialog, QPlainTextEdit

    panel._add_row({"kind": "holding_registers", "address": 0, "count": 1,
                    "values": [1]})
    panel._table.setCurrentCell(0, COL_VALUE)
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(QPlainTextEdit, "toPlainText", lambda self: "0=Running")
    monkeypatch.setattr(QCheckBox, "isChecked", lambda self: True)
    panel._on_value_names()
    assert panel._bitmask_at(0) is True
    assert isinstance(panel._table.cellWidget(0, COL_VALUE), QToolButton)


def test_bitmask_coils_unaffected(panel: SimPanel) -> None:
    panel._add_row({"kind": "coils", "address": 0, "count": 1, "values": [True],
                    "value_names": {"0": "Off", "1": "On"}, "bitmask": True})
    widget = panel._table.cellWidget(0, COL_VALUE)
    assert not isinstance(widget, QToolButton)  # bitmask только для регистров
    assert widget.currentText() == "1 = On"  # enum-комбо, как раньше
