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

from modbus_connector.i18n import set_language  # noqa: E402
from modbus_connector.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _destroy_windows(qapp: QApplication) -> Iterator[None]:
    yield
    # a leaked window's table can crash a later layout pass in another widget
    # (QTableView.updateEditorGeometries); destroy windows after each test
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


def test_multi_tab_state_roundtrip(qapp: QApplication) -> None:
    window = _fresh_window()
    window._add_session({"connection": {"type": "TCP", "tcp_host": "10.0.0.1"}})
    assert window._tabs.tabText(0) == "New connection"
    window._add_session({"connection": {"type": "TCP", "tcp_host": "10.0.0.2", "unit": 5}})
    window._tabs.setCurrentIndex(1)

    state = window._collect_state()
    assert len(state["tabs"]) == 2
    assert state["active_tab"] == 1

    window._clear_sessions()
    assert window._tabs.count() == 0
    window._apply_state(state)
    assert window._tabs.count() == 2
    restored = window._collect_state()
    assert restored["tabs"][0]["connection"]["tcp_host"] == "10.0.0.1"
    assert restored["tabs"][1]["connection"]["tcp_host"] == "10.0.0.2"
    assert restored["tabs"][1]["connection"]["unit"] == 5
    assert restored["active_tab"] == 1
    window._shutdown_sessions()


def test_old_single_session_settings_load_as_one_tab(qapp: QApplication) -> None:
    window = _fresh_window()
    window._apply_state(
        {
            "connection": {"type": "TCP", "tcp_host": "192.168.1.10"},
            "registers": [
                {"name": "temp", "kind": "holding_registers", "address": 5, "count": 1}
            ],
            "scanner": {"start": 3, "end": 8},
        }
    )
    assert window._tabs.count() == 1
    state = window._collect_state()
    assert state["tabs"][0]["connection"]["tcp_host"] == "192.168.1.10"
    assert state["tabs"][0]["registers"][0]["name"] == "temp"
    assert state["tabs"][0]["scanner"]["start"] == 3
    window._shutdown_sessions()


def test_close_tab_keeps_last_and_shutdown_clean(qapp: QApplication) -> None:
    window = _fresh_window()
    window._add_session()
    window._add_session()
    assert window._tabs.count() == 2
    window._close_tab(1)
    assert window._tabs.count() == 1
    window._close_tab(0)  # the last tab is not closed
    assert window._tabs.count() == 1
    window._shutdown_sessions()  # must stop the worker thread without hanging
