import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Qt needs system libraries (libEGL etc.) that headless Linux CI runners lack;
# skip the whole module there — it still runs on macOS/Windows CI and dev machines.
try:
    from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402
except ImportError as exc:
    pytest.skip(f"Qt system libraries not available: {exc}", allow_module_level=True)

from modbus_connector.i18n import set_language  # noqa: E402
from modbus_connector.main_window import MainWindow  # noqa: E402
from modbus_connector.templates import load_template  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _destroy_windows(qapp: QApplication) -> Iterator[None]:
    yield
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, MainWindow):
            widget._shutdown_sessions()
            widget.close()
            widget.deleteLater()
    qapp.processEvents()


def _fresh_window() -> MainWindow:
    window = MainWindow()
    window._clear_sessions()  # drop whatever the user's settings file restored
    set_language("en")  # construction re-applied the persisted language; pin en
    return window


def _find_submenu(menu: QMenu, title: str) -> QMenu:
    for action in menu.actions():
        # one .menu() call, kept in a local: each call returns a new wrapper
        # and a discarded wrapper can delete the underlying C++ QMenu
        submenu = action.menu()
        if submenu is not None and submenu.title() == title:
            return submenu
    raise AssertionError(f"submenu {title!r} not found in {menu.title()!r}")


def test_templates_menu_structure(qapp: QApplication) -> None:
    window = _fresh_window()
    titles = [action.text() for action in window.menuBar().actions()]
    # Templates sits between File and View
    assert titles.index("Templates") == titles.index("File") + 1
    assert titles.index("Templates") == titles.index("View") - 1

    eastron = _find_submenu(window._templates_menu, "Eastron")
    names = [action.text() for action in eastron.actions()]
    assert any("SDM120" in name for name in names)
    window._shutdown_sessions()


def test_template_action_opens_new_tab(qapp: QApplication) -> None:
    window = _fresh_window()
    window._add_session()
    eastron = _find_submenu(window._templates_menu, "Eastron")
    action = next(a for a in eastron.actions() if "SDM120" in a.text())

    action.trigger()
    assert window._tabs.count() == 2
    session = window._tabs.widget(1)
    assert window._tabs.tabText(1) == action.text()

    rows = session.registers_panel.state()
    # compare against the template itself: its register list evolves
    # independently of the menu integration
    template = load_template("Eastron/SDM120")
    expected = {(reg.get("name", ""), reg["address"]) for reg in template["registers"]}
    got = {(row["name"], row["address"]) for row in rows}
    assert len(rows) == len(template["registers"]) >= 8
    assert got == expected
    assert ("Voltage", 0) in got and ("Total active energy", 342) in got
    window._shutdown_sessions()


def test_templates_menu_title_retranslates(qapp: QApplication) -> None:
    window = _fresh_window()
    assert window._templates_menu.title() == "Templates"
    set_language("ru")
    assert window._templates_menu.title() == "Шаблоны"
    set_language("en")
    assert window._templates_menu.title() == "Templates"
    window._shutdown_sessions()
