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
from modbus_connector.i18n import set_language  # noqa: E402
from modbus_connector.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _restore_theme() -> None:
    yield
    theme.apply_theme("system")  # the theme is app-global: leave no trace
    set_language("en")  # a MainWindow applies the persisted language on build


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


def test_theme_aware_colors(qapp: QApplication) -> None:
    from PySide6.QtGui import QPalette

    theme.apply_theme("dark")
    assert theme.graph_colors() == ("k", "d")
    dark_flash = theme.flash_color()
    dark_status = theme.status_colors()
    dark_hair = theme.crosshair_color()
    assert theme.sparkline_color().lightness() > 150  # visible on dark cells

    theme.apply_theme("light")
    assert theme.graph_colors() == ("w", "k")
    assert theme.flash_color() != dark_flash
    assert theme.status_colors() != dark_status
    assert theme.status_colors()["ok"] == "green"
    assert theme.crosshair_color() != dark_hair  # gray hair: darker on light
    # the light sparkline keeps the palette Highlight it always used
    assert theme.sparkline_color() == qapp.palette().color(QPalette.ColorRole.Highlight)


def test_sparkline_paints_in_both_themes(qapp: QApplication) -> None:
    import itertools

    from modbus_connector.registers_panel import RegistersPanel

    panel = RegistersPanel(itertools.count(1).__next__)
    token = panel._token_at(0)
    series = panel.series(token)
    assert series is not None
    for i in range(20):
        series.append(float(i), float(i % 5))
    for name in ("dark", "light"):
        theme.apply_theme(name)
        panel._sparklines[token].refresh()
        panel._sparklines[token].grab()  # theme.sparkline_color(): must not crash
