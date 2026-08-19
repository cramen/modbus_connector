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
                       "rule": "manual", "rule_text": ""}
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
    panel.handle_client_changed(True)
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
