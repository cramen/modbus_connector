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
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, MainWindow):
            widget._shutdown_sessions()
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
