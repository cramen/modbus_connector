import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Qt needs system libraries (libEGL etc.) that headless Linux CI runners lack;
# skip the whole module there — it still runs on macOS/Windows CI and dev machines.
try:
    from PySide6.QtWidgets import QApplication  # noqa: E402
except ImportError as exc:
    pytest.skip(f"Qt system libraries not available: {exc}", allow_module_level=True)

from modbus_connector import theme  # noqa: E402
from modbus_connector.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _restore_theme() -> None:
    yield
    theme.apply_theme("system")  # the theme is app-global: leave no trace


def test_apply_theme_switches_stylesheet(qapp: QApplication) -> None:
    theme.apply_theme("light")
    light = qapp.styleSheet()
    assert theme.current_theme() == "light"
    assert not theme.is_dark()

    theme.apply_theme("dark")
    dark = qapp.styleSheet()
    assert theme.current_theme() == "dark"
    assert theme.is_dark()

    assert light and dark and light != dark  # qdarktheme restyles live


def test_unknown_theme_falls_back_to_system(qapp: QApplication) -> None:
    theme.apply_theme("nope")
    assert theme.current_theme() == "system"


def test_theme_key_roundtrip(qapp: QApplication) -> None:
    window = MainWindow()
    window._clear_sessions()  # drop whatever the user's settings file restored
    window._apply_state({"theme": "dark"})
    assert theme.current_theme() == "dark"
    assert window._theme_actions["dark"].isChecked()
    assert window._collect_state()["theme"] == "dark"

    window._apply_state({"tabs": []})  # missing key: theme unchanged
    assert window._collect_state()["theme"] == "dark"

    window._apply_state({"theme": "junk"})  # unknown: tolerated as system
    assert theme.current_theme() == "system"
    assert window._theme_actions["system"].isChecked()
    window._shutdown_sessions()


def test_view_menu_actions_apply_theme(qapp: QApplication) -> None:
    window = MainWindow()
    window._theme_actions["light"].trigger()
    assert theme.current_theme() == "light"
    assert window._theme_actions["light"].isChecked()
    assert not window._theme_actions["dark"].isChecked()  # exclusive group
    assert not window._theme_actions["system"].isChecked()

    window._theme_actions["dark"].trigger()
    assert theme.current_theme() == "dark"
    assert window._theme_actions["dark"].isChecked()
    assert not window._theme_actions["light"].isChecked()
    window._shutdown_sessions()
