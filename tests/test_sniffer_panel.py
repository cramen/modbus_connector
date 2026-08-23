import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Qt needs system libraries (libEGL etc.) that headless Linux CI runners lack;
# skip the whole module there — it still runs on macOS/Windows CI and dev machines.
try:
    from PySide6.QtTest import QSignalSpy  # noqa: E402
    from PySide6.QtWidgets import (  # noqa: E402
        QApplication,
        QDialog,
        QFileDialog,
        QTextBrowser,
    )
except ImportError as exc:
    pytest.skip(f"Qt system libraries not available: {exc}", allow_module_level=True)

from modbus_connector import theme  # noqa: E402
from modbus_connector.csv_dialogs import ExportColumnsDialog  # noqa: E402
from modbus_connector.i18n import current_language, set_language, tr  # noqa: E402
from modbus_connector.models import RtuParams, rows_from_csv  # noqa: E402
from modbus_connector.sniffer_panel import (  # noqa: E402
    COL_ADDRESS,
    COL_FORMAT,
    COL_NAME,
    COL_TYPE,
    COL_VALUE,
    SnifferPanel,
    UnitTab,
)
from modbus_connector.timeseries import TimeSeries  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def panel(qapp: QApplication) -> SnifferPanel:
    widget = SnifferPanel()
    yield widget
    widget.deleteLater()
    qapp.processEvents()


def _tab(panel: SnifferPanel, unit: int) -> UnitTab:
    return panel._unit_tabs[unit]


def test_tab_created_on_values(panel: SnifferPanel) -> None:
    assert panel._tabs.count() == 0
    panel.handle_values(1, "holding_registers", 5, [100, 200])
    assert panel._tabs.count() == 1
    assert panel._tabs.tabText(0) == "unit 1"
    tab = _tab(panel, 1)
    assert tab._table.rowCount() == 1
    assert tab._table.item(0, COL_ADDRESS).text() == "5"
    assert tab._table.item(0, COL_TYPE).text() == "holding_registers"
    assert tab._table.item(0, COL_VALUE).text() == "100, 200"
    # неизменённое повторное значение — та же строка, без flash
    panel.handle_values(1, "holding_registers", 5, [100, 200])
    assert tab._table.rowCount() == 1


def test_rows_sorted_by_address(panel: SnifferPanel) -> None:
    panel.handle_values(1, "holding_registers", 10, [10])
    panel.handle_values(1, "holding_registers", 3, [3])
    panel.handle_values(1, "coils", 7, [True])
    tab = _tab(panel, 1)
    addresses = [int(tab._table.item(i, COL_ADDRESS).text()) for i in range(3)]
    assert addresses == [3, 7, 10]
    kinds = [tab._table.item(i, COL_TYPE).text() for i in range(3)]
    assert kinds == ["holding_registers", "coils", "holding_registers"]
    # битовая область показывает 0/1
    assert tab._table.item(1, COL_VALUE).text() == "1"


def test_flash_on_changed_value(panel: SnifferPanel) -> None:
    panel.handle_values(2, "holding_registers", 0, [1])
    tab = _tab(panel, 2)
    item = tab._table.item(0, COL_VALUE)
    assert item.background().color() == theme.flash_color()
    panel.handle_values(2, "holding_registers", 0, [1])  # без изменений — без flash
    generations = dict(tab._flash_generations)
    panel.handle_values(2, "holding_registers", 0, [2])
    assert tab._table.item(0, COL_VALUE).text() == "2"
    assert tab._flash_generations[("holding_registers", 0)] > generations[(
        "holding_registers", 0)]


def test_name_editable_and_in_state(panel: SnifferPanel) -> None:
    panel.handle_values(1, "holding_registers", 5, [100])
    tab = _tab(panel, 1)
    name_item = tab._table.item(0, COL_NAME)
    name_item.setText("temp")
    rows = panel.state()["units"][0]["rows"]
    assert rows[0]["name"] == "temp"


def test_format_affects_value(panel: SnifferPanel) -> None:
    panel.handle_values(1, "holding_registers", 0, [0x00AB])
    tab = _tab(panel, 1)
    combo = tab._table.cellWidget(0, COL_FORMAT)
    assert tab._table.item(0, COL_VALUE).text() == "171"
    combo.setCurrentText("hex")
    assert tab._table.item(0, COL_VALUE).text() == "0x00AB"
    combo.setCurrentText("s16")
    assert tab._table.item(0, COL_VALUE).text() == "171"


def test_per_unit_log_filtered(panel: SnifferPanel) -> None:
    panel.handle_values(1, "coils", 0, [True])
    panel.handle_frame_for_unit(1, "→ read coils unit=1")
    panel.handle_frame_for_unit(2, "× exception Illegal Function unit=2")
    assert panel._tabs.count() == 2  # вкладка 2 создана и по кадру без значений
    assert "read coils" in _tab(panel, 1)._log.toPlainText()
    assert "exception" not in _tab(panel, 1)._log.toPlainText()
    assert "exception" in _tab(panel, 2)._log.toPlainText()


def test_trend_collects_points(panel: SnifferPanel) -> None:
    panel.handle_values(1, "holding_registers", 0, [10])
    panel.handle_values(1, "holding_registers", 0, [20])
    panel.handle_values(1, "coils", 0, [True])
    tab = _tab(panel, 1)
    series = tab._series[("holding_registers", 0)]
    assert len(series) == 2
    assert series.points()[1] == [10.0, 20.0]
    assert len(tab._series[("coils", 0)]) == 1
    # hex-формат в тренд не пишется
    tab._table.cellWidget(0, COL_FORMAT).setCurrentText("hex")
    panel.handle_values(1, "holding_registers", 0, [30])
    assert len(tab._series[("holding_registers", 0)]) == 2


def test_state_roundtrip(panel: SnifferPanel) -> None:
    panel.set_state(
        {
            "params": {"port": "/dev/ttyFAKE0", "baudrate": 19200,
                       "bytesize": 7, "parity": "E", "stopbits": 2},
            "units": [
                {"unit": 3, "rows": [
                    {"address": 5, "kind": "holding_registers", "name": "temp",
                     "format": "f32", "value": [100, 200]},
                    {"address": 0, "kind": "coils", "name": "", "format": "dec",
                     "value": [True]},
                ]},
            ],
        }
    )
    collected = panel.state()
    params = collected["params"]
    assert params == {"port": "/dev/ttyFAKE0", "baudrate": 19200,
                      "bytesize": 7, "parity": "E", "stopbits": 2}
    units = collected["units"]
    assert len(units) == 1 and units[0]["unit"] == 3
    rows = units[0]["rows"]
    assert rows[0] == {"address": 0, "kind": "coils", "name": "", "format": "dec",
                       "value": [True]}
    assert rows[1] == {"address": 5, "kind": "holding_registers", "name": "temp",
                       "format": "f32", "value": [100, 200]}
    # толерантный разбор: мусор пропускается, валидное остаётся
    panel.set_state(
        {"params": "junk",
         "units": [{"unit": "x"}, {"unit": 999}, 42,
                   {"unit": 4, "rows": [{"address": "bad"}, {"kind": "nope"}]}]}
    )
    collected = panel.state()
    assert collected["params"]["port"] == "/dev/ttyFAKE0"
    by_unit = {u["unit"]: u for u in collected["units"]}
    assert 999 not in by_unit
    assert len(by_unit[4]["rows"]) == 1  # address "bad" пропущен, kind "nope" → hr
    assert by_unit[4]["rows"][0]["kind"] == "holding_registers"


def test_start_stop_signals(panel: SnifferPanel) -> None:
    spy = QSignalSpy(panel.startRequested)
    panel.set_state({"params": {"port": "/dev/ttyFAKE0", "baudrate": 19200}})
    panel._button.click()
    assert spy.count() == 1
    params = spy.at(0)[0]
    assert isinstance(params, RtuParams)
    assert params.port == "/dev/ttyFAKE0" and params.baudrate == 19200
    assert panel._port.isEnabled()  # пока не running — параметры активны
    panel.set_sniffing(True, "Listening (sniff rtu /dev/ttyFAKE0 @ 19200)")
    assert not panel._port.isEnabled()
    assert panel._button.text() == "Stop sniffing"
    stop_spy = QSignalSpy(panel.stopRequested)
    panel._button.click()
    assert stop_spy.count() == 1
    panel.set_sniffing(False, "Stopped")
    assert panel._port.isEnabled()
    assert panel._button.text() == "Start sniffing"


def test_sniffing_description(panel: SnifferPanel) -> None:
    assert panel.sniffing_description() is None
    panel.set_state({"params": {"port": "/dev/ttyFAKE0", "baudrate": 38400}})
    panel._button.click()  # startRequested без worker'а — params запомнились
    panel.set_sniffing(True, "Listening")
    assert panel.sniffing_description() == "sniff rtu /dev/ttyFAKE0 @ 38400"
    panel.set_sniffing(False, "Stopped")
    assert panel.sniffing_description() is None


def test_retranslate(panel: SnifferPanel, qapp: QApplication) -> None:
    panel.handle_values(1, "holding_registers", 5, [100])
    panel.handle_frame_for_unit(1, "line")
    previous = current_language()
    set_language("ru")
    try:
        panel.retranslate()
        assert panel._tabs.tabText(0) == "юнит 1"
        assert panel._button.text() == "Начать сниффинг"
        header = _tab(panel, 1)._table.horizontalHeaderItem(COL_VALUE).text()
        assert header == "Значение"
    finally:
        set_language(previous)
    panel.retranslate()
    assert panel._tabs.tabText(0) == tr("unit {unit}", unit=1)


def test_graph_accessors(panel: SnifferPanel) -> None:
    panel.handle_values(1, "holding_registers", 0, [10])
    panel.handle_values(1, "coils", 3, [True])
    tab = _tab(panel, 1)
    tokens = tab.row_tokens()
    assert len(tokens) == 2
    assert all(tab.row_poll_enabled(t) for t in tokens)
    assert isinstance(tab.series(tokens[0]), TimeSeries)
    assert tab.series(-1) is None
    # без имени — «kind@addr», с именем — имя
    assert tab.row_label(tokens[0]) == "holding_registers@0"
    tab._table.item(0, COL_NAME).setText("temp")
    assert tab.row_label(tokens[0]) == "temp"
    tab.clear_series()
    assert len(tab.series(tokens[0])) == 0


def test_rows_changed_signal(panel: SnifferPanel) -> None:
    panel.handle_values(1, "holding_registers", 0, [1])
    tab = _tab(panel, 1)
    spy = QSignalSpy(tab.rowsChanged)
    panel.handle_values(1, "holding_registers", 5, [2])  # новая строка
    assert spy.count() == 1
    panel.handle_values(1, "holding_registers", 5, [3])  # смена значения — не emit
    assert spy.count() == 1
    tab._table.item(1, COL_NAME).setText("flow")  # переименование
    assert spy.count() == 2


def test_graph_window(panel: SnifferPanel, qapp: QApplication) -> None:
    panel.handle_values(1, "holding_registers", 0, [10])
    panel.handle_values(1, "coils", 3, [True])
    tab = _tab(panel, 1)
    tab._graph_button.click()
    window = tab._graph_window
    assert window is not None
    assert window.windowTitle() == "unit 1"
    assert window._rows_list.count() == 2
    # сниффер не поллит: кнопка-дублёр поллинга скрыта
    assert window._poll_button.isHidden()
    tab._graph_button.click()  # повторное нажатие поднимает то же окно
    assert tab._graph_window is window
    # новая строка вкладки появляется в чек-листе по rowsChanged
    panel.handle_values(1, "holding_registers", 7, [1])
    assert window._rows_list.count() == 3
    window.close()


def test_csv_export_roundtrip(
    panel: SnifferPanel, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel.handle_values(1, "holding_registers", 0, [100])
    panel.handle_values(1, "coils", 2, [True])
    tab = _tab(panel, 1)
    tab._table.item(0, COL_NAME).setText("temp")
    path = tmp_path / "unit1.csv"
    monkeypatch.setattr(
        ExportColumnsDialog, "exec", lambda self: QDialog.DialogCode.Accepted
    )
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        lambda *args, **kwargs: (str(path), "CSV (*.csv)"),
    )
    tab._csv_button.click()
    parsed = rows_from_csv(path.read_text(encoding="utf-8-sig"))
    assert [(row.name, row.kind, row.address) for row, _display in parsed] == [
        ("temp", "holding_registers", 0),
        ("", "coils", 2),
    ]
    assert all(row.count == 1 for row, _display in parsed)
    assert "exported" in tab._log.toPlainText()


def test_help_button(panel: SnifferPanel, qapp: QApplication) -> None:
    panel._help_button.click()
    qapp.processEvents()
    dialogs = [
        widget for widget in qapp.topLevelWidgets()
        if isinstance(widget, QDialog) and widget.windowTitle() == "Sniffer — Help"
    ]
    assert len(dialogs) == 1
    browser = dialogs[0].findChild(QTextBrowser)
    assert "sniff" in browser.toPlainText().lower()
    dialogs[0].close()
