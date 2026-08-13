import itertools
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Qt needs system libraries (libEGL etc.) that headless Linux CI runners lack;
# skip the whole module there — it still runs on macOS/Windows CI and dev machines.
try:
    import pyqtgraph as pg  # noqa: E402
    from PySide6.QtCore import QPoint, Qt  # noqa: E402
    from PySide6.QtTest import QTest  # noqa: E402
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
    window.set_bus_enabled(True)  # polling needs a connection
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


def test_crosshair_readout_snaps_to_nearest_sample(qapp: QApplication) -> None:
    panel = _panel()
    window = GraphWindow(panel)
    temp = panel.series(panel._token_at(0))
    pressure = panel.series(panel._token_at(1))
    assert temp is not None and pressure is not None
    for i in range(50):
        temp.append(1000.0 + i, float(i))
        pressure.append(1000.0 + i, 2.0 * i)
    window._refresh()  # curves now hold relative x 0..49

    window._update_crosshair(10.4)  # the nearest sample is index 10
    assert window._crosshair.isVisible()
    assert window._crosshair.value() == 10.4
    text = window._readout.textItem.toPlainText()
    assert "t = 10.4 s" in text
    assert "temp: 10" in text
    assert "pressure: 20" in text
    dot_xs, dot_ys = window._crosshair_dots.getData()
    assert list(dot_xs) == [10.0, 10.0]
    assert list(dot_ys) == [10.0, 20.0]

    window._update_crosshair(10.6)  # the nearest sample is index 11
    text = window._readout.textItem.toPlainText()
    assert "temp: 11" in text and "pressure: 22" in text

    window._update_crosshair(500.0)  # beyond every series' extent
    text = window._readout.textItem.toPlainText()
    assert "temp: —" in text and "pressure: —" in text
    assert window._crosshair_dots.getData()[0].size == 0

    window._update_crosshair(None)  # the cursor left the plot area
    assert not window._crosshair.isVisible()
    assert not window._readout.isVisible()


def test_crosshair_follows_real_mouse_moves(qapp: QApplication) -> None:
    # regression for the macOS hover bug: hover moves without a pressed button
    # only reach the handler with mouse tracking enabled, and SignalProxy
    # delivers the signal's arguments as a TUPLE the handler must unpack —
    # both broke the event path while _update_crosshair itself worked
    panel = _panel()
    window = GraphWindow(panel)
    series = panel.series(panel._token_at(0))
    assert series is not None
    for i in range(50):
        series.append(1000.0 + i, float(i))
    window.show()
    window._refresh()
    assert window._plot.hasMouseTracking()
    assert window._plot.viewport().hasMouseTracking()

    viewport = window._plot.viewport()
    center = QPoint(viewport.width() // 2, viewport.height() // 2)
    QTest.mouseMove(viewport, center)
    QTest.mouseMove(viewport, QPoint(center.x() + 3, center.y()))
    qapp.processEvents()  # SignalProxy rate-limits through a QTimer flush
    assert window._crosshair.isVisible()  # the event → proxy → handler chain works
    assert "temp:" in window._readout.textItem.toPlainText()
    (vx0, vx1), _ = window._viewbox.viewRange()
    assert vx0 <= window._crosshair.value() <= vx1
    window.hide()


def test_crosshair_readout_stays_put_when_panning(qapp: QApplication) -> None:
    # the readout is anchored to the PlotItem in item coordinates (like the
    # legend); the hair stays in data coordinates and must pan with the data
    panel = _panel()
    window = GraphWindow(panel)
    series = panel.series(panel._token_at(0))
    assert series is not None
    for i in range(50):
        series.append(1000.0 + i, float(i))
    window.show()
    window._refresh()
    window._update_crosshair(20.0)
    qapp.processEvents()

    readout_before = window._readout.scenePos()
    hair_scene_x_before = window._viewbox.mapViewToScene(pg.Point(20.0, 0.0)).x()

    window._viewbox.setXRange(500.0, 560.0, padding=0)  # pan far from the data
    qapp.processEvents()

    readout_after = window._readout.scenePos()
    # re-pinned to the same view corner (a few px of jitter are the axes
    # relayouting; pre-fix the readout drifted with the data by 100s of px)
    assert abs(readout_after.x() - readout_before.x()) < 30
    assert abs(readout_after.y() - readout_before.y()) < 30
    hair_scene_x_after = window._viewbox.mapViewToScene(pg.Point(20.0, 0.0)).x()
    assert abs(hair_scene_x_after - hair_scene_x_before) > 100  # moved with data
    window.hide()


def test_graph_follows_theme(qapp: QApplication) -> None:
    from modbus_connector import theme

    panel = _panel()
    window = GraphWindow(panel)
    try:
        theme.apply_theme("light")
        window.update_theme()
        assert pg.getConfigOption("background") == "w"
        assert window._crosshair.pen.color() == theme.crosshair_color()

        theme.apply_theme("dark")
        window.update_theme()
        assert pg.getConfigOption("background") == "k"
        assert window._crosshair.pen.color() == theme.crosshair_color()
    finally:
        theme.apply_theme("system")  # theme and pg config are app-global
