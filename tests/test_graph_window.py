import itertools
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Qt needs system libraries (libEGL etc.) that headless Linux CI runners lack;
# skip the whole module there — it still runs on macOS/Windows CI and dev machines.
try:
    import pyqtgraph  # noqa: E402,F401
    from PySide6.QtCore import Qt  # noqa: E402
    from PySide6.QtWidgets import QApplication  # noqa: E402
except ImportError as exc:
    pytest.skip(f"Qt/pyqtgraph not available: {exc}", allow_module_level=True)

from modbus_connector.graph_window import GraphWindow  # noqa: E402
from modbus_connector.models import RegisterRow  # noqa: E402
from modbus_connector.registers_panel import RegistersPanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _panel() -> RegistersPanel:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_state(
        [
            {"name": "temp", "kind": "holding_registers", "address": 0, "count": 1},
            {"name": "pressure", "kind": "holding_registers", "address": 2, "count": 1},
        ]
    )
    return panel


def test_checklist_lists_rows_and_tracks_tokens(qapp: QApplication) -> None:
    panel = _panel()
    window = GraphWindow(panel)
    assert window._rows_list.count() == 2
    assert len(window._curves) == 2  # rows are plotted on first sight

    panel._add_row(RegisterRow(name="flow", kind="holding_registers", address=4))
    assert window._rows_list.count() == 3  # rowsChanged rebuilds the checklist
    assert len(window._curves) == 3

    window._rows_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert panel._token_at(0) not in window._curves
    panel._add_row(RegisterRow(name="x", kind="coils", address=0))
    assert panel._token_at(0) not in window._curves  # the uncheck survives rebuilds
    assert window._rows_list.count() == 4


def test_marker_stats_match_timeseries_math(qapp: QApplication) -> None:
    panel = _panel()
    window = GraphWindow(panel)
    token = panel._token_at(0)
    series = panel.series(token)
    assert series is not None
    for i in range(10):
        series.append(float(i), float(i))
    window._refresh()
    assert window._origin == 0.0

    window._toggle_markers(True)
    window._marker_lines[0].setValue(2.0)
    window._marker_lines[1].setValue(5.0)
    window._update_stats()
    assert window._delta_label.text() == "Δt = 3 s"
    assert window._stats_table.item(0, 0).text() == "temp"
    assert window._stats_table.item(0, 1).text() == "2"
    assert window._stats_table.item(0, 2).text() == "5"
    assert window._stats_table.item(0, 3).text() == "3.5"

    window._toggle_markers(False)
    assert window._stats_box.isHidden()


def test_follow_mode_scrolls_x_on_refresh(qapp: QApplication) -> None:
    panel = _panel()
    window = GraphWindow(panel)
    token = panel._token_at(0)
    series = panel.series(token)
    assert series is not None
    for i in range(200):
        series.append(float(i), 1.0)
    window._window_spin.setValue(10)
    window._refresh()
    (x0, x1), _ = window._viewbox.viewRange()
    assert x1 == 199
    assert x0 == pytest.approx(189)


def test_user_zoom_switches_to_manual(qapp: QApplication) -> None:
    panel = _panel()
    window = GraphWindow(panel)
    token = panel._token_at(0)
    series = panel.series(token)
    assert series is not None
    for i in range(50):
        series.append(float(i), float(i))
    window._refresh()  # Follow range change is guarded, mode stays
    assert window._mode_combo.currentText() == "Follow"
    window._viewbox.setXRange(0, 5, padding=0)  # user zoom: no guard
    assert window._mode_combo.currentText() == "Manual"


def test_markers_land_on_data_when_toggled_before_first_refresh(
    qapp: QApplication,
) -> None:
    panel = _panel()
    window = GraphWindow(panel)
    for index in range(2):
        series = panel.series(panel._token_at(index))
        assert series is not None
        for i in range(240):
            series.append(float(i), float(index * 10 + i % 5))

    window._toggle_markers(True)  # the bug order: before any refresh
    window._refresh()

    a, b = [line.value() for line in window._marker_lines]
    assert 0 < a < b <= 239  # inside the data range, not the default 0..1 view
    (vx0, vx1), _ = window._viewbox.viewRange()
    assert vx0 <= a < b <= vx1  # and visible in the Follow window
    assert window._stats_table.item(0, 1).text() != "—"
    assert window._stats_table.item(1, 1).text() != "—"

    # a second refresh must not override user placement
    window._marker_lines[0].setValue(vx0 + 5)
    window._refresh()
    assert window._marker_lines[0].value() == vx0 + 5


def test_clear_restarts_history_and_time_axis(qapp: QApplication) -> None:
    panel = _panel()
    window = GraphWindow(panel)
    series = panel.series(panel._token_at(0))
    assert series is not None
    for i in range(50):
        series.append(1000.0 + i, float(i))
    window._refresh()
    assert window._origin == 1000.0

    window._toggle_markers(True)
    window._refresh()
    window._clear_button.click()
    assert len(series) == 0
    assert window._origin is None
    assert window._markers_need_placement  # re-place when new data arrives

    for i in range(30):
        series.append(5000.0 + i, float(i))
    window._refresh()
    assert window._origin == 5000.0  # relative axis restarts near zero
    times, _ = window._curves[panel._token_at(0)].getData()
    assert list(times) == [float(i) for i in range(30)]
    assert not window._markers_need_placement  # placed again on the new data


def test_poll_button_drives_panel_and_follows_state(qapp: QApplication) -> None:
    panel = _panel()
    window = GraphWindow(panel)
    assert window._poll_button.text() == "Start polling and record"

    window._poll_button.click()  # starts polling with recording on the panel
    assert panel.is_polling() and panel.is_recording()
    assert window._poll_button.text() == "Stop polling"
    assert panel._poll_button.text() == "Stop polling"  # both controls in sync

    panel.start_polling(False)  # flipped to poll-only from the panel side
    assert panel.is_polling() and not panel.is_recording()
    assert window._poll_button.text() == "Start polling and record"

    window._poll_button.click()  # polling runs: enables recording on top
    assert panel.is_polling() and panel.is_recording()
    assert window._poll_button.text() == "Stop polling"

    window._poll_button.click()  # now stops everything
    assert not panel.is_polling() and not panel.is_recording()
    assert window._poll_button.text() == "Start polling and record"
    assert panel._poll_button.text() == "Start polling and record"
