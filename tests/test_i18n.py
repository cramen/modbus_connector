import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Qt needs system libraries (libEGL etc.) that headless Linux CI runners lack;
# skip the whole module there — it still runs on macOS/Windows CI and dev machines.
try:
    from PySide6.QtWidgets import QApplication  # noqa: E402
except ImportError as exc:
    pytest.skip(f"Qt system libraries not available: {exc}", allow_module_level=True)

from modbus_connector import i18n  # noqa: E402
from modbus_connector.main_window import MainWindow  # noqa: E402
from modbus_connector.models import RtuOverUdpParams  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _english() -> None:
    i18n.set_language("en")
    yield
    i18n.set_language("en")


@pytest.fixture(autouse=True)
def _destroy_windows(qapp: QApplication) -> Iterator[None]:
    yield
    from modbus_connector.registers_panel import RegistersPanel

    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, MainWindow):
            widget._shutdown_sessions()
        if isinstance(widget, MainWindow | RegistersPanel):
            widget.close()
            widget.deleteLater()
    qapp.processEvents()


def test_tr_falls_back_to_english_for_unknown_keys(qapp: QApplication) -> None:
    i18n.set_language("ru")
    assert i18n.tr("Connect") == "Подключиться"
    assert i18n.tr("No such string anywhere") == "No such string anywhere"
    assert i18n.tr("  top: {kind}", kind="boom") == "  частая: boom"
    i18n.set_language("en")
    assert i18n.tr("Connect") == "Connect"


def test_set_language_is_tolerant(qapp: QApplication) -> None:
    i18n.set_language("ru")
    assert i18n.current_language() == "ru"
    i18n.set_language("klingon")
    assert i18n.current_language() == "en"  # garbage falls back to English
    i18n.set_language(None)
    assert i18n.current_language() in i18n.LANGUAGES  # system detection


def _fresh_window() -> MainWindow:
    window = MainWindow()
    window._clear_sessions()  # drop whatever the user's settings file restored
    if window._tabs.count() == 0:
        window._add_session()
    i18n.set_language("en")  # construction re-applied the persisted language
    return window


def test_menu_switch_retranslates_window_and_panels(qapp: QApplication) -> None:
    window = _fresh_window()
    session = window._tabs.widget(0)
    assert window._file_menu.title() == "File"
    assert session.connection_panel._button.text() == "Connect"

    window._language_actions["ru"].trigger()
    assert window._file_menu.title() == "Файл"
    assert window._theme_menu.title() == "Тема"
    assert session.connection_panel._button.text() == "Подключиться"
    assert session._scanner_button.text() == "Сканер…"
    assert window._tabs.tabText(0) == "Новое подключение"
    assert window._language_actions["ru"].isChecked()
    assert not window._language_actions["en"].isChecked()  # exclusive group

    window._language_actions["en"].trigger()
    assert window._file_menu.title() == "File"
    assert session.connection_panel._button.text() == "Connect"


def test_language_persists_in_settings(qapp: QApplication) -> None:
    window = _fresh_window()
    window._language_actions["ru"].trigger()
    assert window._collect_state()["language"] == "ru"

    fresh = _fresh_window()
    fresh._apply_state({"language": "ru"})
    assert i18n.current_language() == "ru"
    assert fresh._file_menu.title() == "Файл"
    fresh._apply_state({"language": "junk", "tabs": []})  # tolerated
    assert i18n.current_language() == "en"


def test_type_combo_keeps_english_state_and_params(qapp: QApplication) -> None:
    window = _fresh_window()
    panel = window._tabs.widget(0).connection_panel
    panel.set_state({"type": "TCP"})
    i18n.set_language("ru")
    panel.retranslate()
    assert panel._type_combo.currentText() == "TCP"  # acronym, untranslated
    panel._type_combo.setCurrentIndex(3)  # RTU over UDP (shown in Russian)
    assert panel.state()["type"] == "RTU over UDP"  # English in the settings
    params = panel._build_params()
    assert isinstance(params, RtuOverUdpParams)


def test_registers_panel_retranslates(qapp: QApplication) -> None:
    import itertools

    from modbus_connector.registers_panel import RegistersPanel

    panel = RegistersPanel(itertools.count(1).__next__)
    i18n.set_language("ru")
    panel.retranslate()
    headers = [
        panel._table.horizontalHeaderItem(col).text()
        for col in range(panel._table.columnCount())
    ]
    assert headers[:3] == ["Имя", "Тип", "Адрес"]  # headers, not kind values
    assert panel._poll_button.text() == "Начать опрос с записью"  # record default

    panel.start_polling(True)
    assert panel._poll_button.text() == "Остановить опрос"  # state-dependent
    panel.stop_polling()
    panel.retranslate()
    assert panel._poll_button.text() == "Начать опрос с записью"

    i18n.set_language("en")
    panel.retranslate()
    assert headers[0] != panel._table.horizontalHeaderItem(0).text()
    assert panel._table.horizontalHeaderItem(0).text() == "Name"
    assert panel._poll_button.text() == "Start polling and record"


def test_csv_dialog_titles_follow_language(qapp: QApplication) -> None:
    from modbus_connector.csv_dialogs import ExportColumnsDialog, ImportMappingDialog

    i18n.set_language("ru")
    export = ExportColumnsDialog()
    assert export.windowTitle() == "Экспорт CSV"
    imp = ImportMappingDialog(["Register Name", "type"])
    assert imp.windowTitle() == "Импорт CSV"
    skip_combo = imp._table.cellWidget(0, 1)
    assert skip_combo.currentText() == "— пропустить —"
    assert imp.mapping() == {"type": "kind"}  # sentinel decoupled from display
    i18n.set_language("en")


def test_worker_log_templates_format_in_russian(qapp: QApplication) -> None:
    i18n.set_language("ru")
    line = i18n.tr(
        "→ read {kind} unit={unit} addr={address} count={count}",
        kind="holding_registers", unit=1, address=0, count=2,
    )
    assert line == "→ чтение holding_registers unit=1 адр=0 кол-во=2"
    assert "holding_registers" in line  # kind strings are never translated
    i18n.set_language("en")
