import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from modbus_connector.registers_panel import RegistersPanel

MODES = ("Follow", "Full", "Manual")
MARKER_PENS = (pg.mkPen((60, 180, 75), width=2), pg.mkPen((200, 60, 60), width=2))


class GraphWindow(QWidget):
    """Живой график выбранных строк таблицы регистров (отдельное окно)."""

    def __init__(self, panel: RegistersPanel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Window)
        self._panel = panel
        self.setWindowTitle("Graph")
        self.resize(950, 520)

        self._seen: set[int] = set()
        self._checked: set[int] = set()
        self._curves: dict[int, pg.PlotDataItem] = {}
        self._origin: float | None = None
        self._updating_range = False
        self._markers_need_placement = False
        self._view_ranged = False  # a refresh has ranged the view to the data

        self._rows_list = QListWidget()
        self._rows_list.itemChanged.connect(self._on_row_toggled)
        refresh_button = QPushButton("Refresh rows")
        refresh_button.clicked.connect(self._rebuild_rows)

        self._mode_combo = QComboBox()
        self._mode_combo.addItems(MODES)
        self._window_spin = QDoubleSpinBox(minimum=1, maximum=86_400, value=60)
        self._window_spin.setSuffix(" s")
        zoom_rect_button = QToolButton()
        zoom_rect_button.setText("Zoom rect")
        zoom_rect_button.setCheckable(True)
        zoom_rect_button.toggled.connect(self._on_zoom_rect_toggled)
        markers_button = QToolButton()
        markers_button.setText("Markers")
        markers_button.setCheckable(True)
        markers_button.toggled.connect(self._toggle_markers)
        reset_button = QPushButton("Reset view")
        reset_button.clicked.connect(self._reset_view)
        self._clear_button = QPushButton("Clear")
        self._clear_button.setToolTip("Clear recorded history and restart the time axis")
        self._clear_button.clicked.connect(self._on_clear)

        self._plot = pg.PlotWidget()
        self._plot.showGrid(x=True, y=True, alpha=0.3)
        self._plot.setLabel("bottom", "time, s (relative)")
        self._plot.addLegend(offset=(4, 4))
        viewbox = self._plot.getPlotItem().getViewBox()
        viewbox.sigRangeChanged.connect(self._on_range_changed)
        self._viewbox = viewbox

        self._marker_lines: list[pg.InfiniteLine] = []
        self._delta_label = QLabel()
        self._stats_table = QTableWidget(0, 4)
        self._stats_table.setHorizontalHeaderLabels(["Series", "Min", "Max", "Avg"])
        self._stats_table.verticalHeader().setVisible(False)
        self._stats_table.setMaximumHeight(120)
        self._stats_box = QWidget()
        stats_layout = QVBoxLayout(self._stats_box)
        stats_layout.setContentsMargins(0, 0, 0, 0)
        stats_layout.addWidget(self._delta_label)
        stats_layout.addWidget(self._stats_table)
        self._stats_box.hide()

        controls = QHBoxLayout()
        controls.addWidget(QLabel("X scale:"))
        controls.addWidget(self._mode_combo)
        controls.addWidget(self._window_spin)
        controls.addWidget(zoom_rect_button)
        controls.addWidget(markers_button)
        controls.addWidget(reset_button)
        controls.addWidget(self._clear_button)
        controls.addStretch(1)

        right = QVBoxLayout()
        right.addLayout(controls)
        right.addWidget(self._plot, 1)
        right.addWidget(self._stats_box)

        left = QVBoxLayout()
        left.addWidget(QLabel("Series:"))
        left.addWidget(self._rows_list, 1)
        left.addWidget(refresh_button)

        layout = QHBoxLayout(self)
        layout.addLayout(left, 0)
        layout.addLayout(right, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._refresh)

        panel.rowsChanged.connect(self._rebuild_rows)
        self._rebuild_rows()

    # --- series checklist ------------------------------------------------

    @Slot()
    def _rebuild_rows(self) -> None:
        tokens = self._panel.row_tokens()
        for token in tokens:
            if token not in self._seen:  # first sight: plot new rows by default
                self._checked.add(token)
        self._seen.update(tokens)
        self._checked &= set(tokens)  # forget deleted rows
        self._rows_list.blockSignals(True)
        self._rows_list.clear()
        for token in tokens:
            item = QListWidgetItem(self._panel.row_label(token))
            item.setData(Qt.ItemDataRole.UserRole, token)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if token in self._checked
                else Qt.CheckState.Unchecked
            )
            self._rows_list.addItem(item)
        self._rows_list.blockSignals(False)
        for token in list(self._curves):
            if token not in self._checked:
                self._remove_curve(token)
        for token in self._checked:
            if token not in self._curves:
                self._add_curve(token)

    @Slot(QListWidgetItem)
    def _on_row_toggled(self, item: QListWidgetItem) -> None:
        token = int(item.data(Qt.ItemDataRole.UserRole))
        if item.checkState() == Qt.CheckState.Checked:
            self._checked.add(token)
            if token not in self._curves:
                self._add_curve(token)
        else:
            self._checked.discard(token)
            self._remove_curve(token)

    def _add_curve(self, token: int) -> None:
        pen = pg.mkPen(pg.intColor(len(self._curves), hues=9), width=2)
        self._curves[token] = self._plot.plot(
            [], [], pen=pen, name=self._panel.row_label(token)
        )

    def _remove_curve(self, token: int) -> None:
        curve = self._curves.pop(token, None)
        if curve is not None:
            self._plot.getPlotItem().removeItem(curve)
            legend = self._plot.getPlotItem().legend
            if legend is not None:
                legend.removeItem(curve.name())

    # --- update loop ------------------------------------------------------

    @Slot()
    def _refresh(self) -> None:
        latest: float | None = None
        for token, curve in self._curves.items():
            series = self._panel.series(token)
            if series is None:
                continue
            times, values = series.points()
            if not times:
                curve.setData([], [])
                continue
            if self._origin is None:
                self._origin = times[0]
            curve.setData([t - self._origin for t in times], values)
            if latest is None or times[-1] > latest:
                latest = times[-1]
        mode = self._mode_combo.currentText()
        if latest is not None and self._origin is not None:
            now = latest - self._origin
            if mode == "Follow":
                self._set_range(now - self._window_spin.value(), now)
                self._view_ranged = True
            elif mode == "Full":
                self._updating_range = True
                try:
                    self._plot.autoRange()
                finally:
                    self._updating_range = False
                self._view_ranged = True
        if self._marker_lines:
            if self._markers_need_placement:
                self._place_markers()
            self._update_stats()

    def _set_range(self, t0: float, t1: float) -> None:
        self._updating_range = True
        try:
            self._viewbox.setXRange(t0, t1, padding=0)
            values = [
                stats
                for token in self._curves
                if (series := self._panel.series(token)) is not None
                and (stats := series.stats(t0 + (self._origin or 0.0),
                                           t1 + (self._origin or 0.0)))
            ]
            if values:
                lo = min(stat[0] for stat in values)
                hi = max(stat[1] for stat in values)
                pad = (hi - lo) * 0.05 or 1.0
                self._viewbox.setYRange(lo - pad, hi + pad, padding=0)
        finally:
            self._updating_range = False

    def _on_range_changed(self) -> None:
        # a user zoom/pan leaves Follow/Full — make the mode change visible
        if not self._updating_range and self._mode_combo.currentText() != "Manual":
            self._mode_combo.setCurrentText("Manual")

    @Slot(bool)
    def _on_zoom_rect_toggled(self, on: bool) -> None:
        self._viewbox.setMouseMode(
            pg.ViewBox.RectMode if on else pg.ViewBox.PanMode
        )

    @Slot()
    def _reset_view(self) -> None:
        self._updating_range = True
        try:
            self._plot.autoRange()
        finally:
            self._updating_range = False
        self._mode_combo.setCurrentText("Follow")

    @Slot()
    def _on_clear(self) -> None:
        self._panel.clear_series()
        for curve in self._curves.values():
            curve.setData([], [])
        self._origin = None  # the next sample restarts the relative axis at ~0
        self._view_ranged = False
        if self._marker_lines:  # re-place once new data arrives
            self._markers_need_placement = True
        self._update_stats()

    # --- markers ----------------------------------------------------------

    def _data_extent(self) -> tuple[float, float] | None:
        """Относительный диапазон данных по всем кривым; None — данных нет."""
        extents = []
        for token in self._curves:
            series = self._panel.series(token)
            if series is None:
                continue
            times, _ = series.points()
            if times:
                extents.append((times[0], times[-1]))
        if not extents:
            return None
        if self._origin is None:
            self._origin = min(start for start, _ in extents)
        return (
            min(start for start, _ in extents) - self._origin,
            max(end for _, end in extents) - self._origin,
        )

    @Slot(bool)
    def _toggle_markers(self, on: bool) -> None:
        if on and not self._marker_lines:
            for pen in MARKER_PENS:
                line = pg.InfiniteLine(
                    pos=0, angle=90, movable=True, pen=pen,
                    hoverPen=pg.mkPen(pen.color(), width=3),
                )
                line.setZValue(10)
                line.sigPositionChangeFinished.connect(self._update_stats)
                self._plot.addItem(line)
                self._marker_lines.append(line)
            # place inside the actual data range, not the default 0..1 view;
            # with no data yet the first refresh that sees data places them
            self._markers_need_placement = True
            self._place_markers()
            self._stats_box.show()
            self._update_stats()
        elif not on and self._marker_lines:
            for line in self._marker_lines:
                self._plot.removeItem(line)
            self._marker_lines.clear()
            self._stats_box.hide()

    def _place_markers(self) -> None:
        extent = self._data_extent()
        if extent is None:
            return
        lo, hi = extent
        if self._view_ranged:
            # the view has been data-ranged: land inside what the user sees
            (vx0, vx1), _ = self._viewbox.viewRange()
            lo, hi = max(lo, vx0), min(hi, vx1)
            if lo >= hi:
                return  # view is off the data; retry on the next refresh
        span = hi - lo or 1.0
        self._marker_lines[0].setValue(lo + span / 3)
        self._marker_lines[1].setValue(lo + 2 * span / 3)
        # provisional extent placement in Follow/Full is refined to the visible
        # range on the next refresh; in Manual the extent placement is final
        self._markers_need_placement = (
            not self._view_ranged and self._mode_combo.currentText() != "Manual"
        )

    @Slot()
    def _update_stats(self) -> None:
        if not self._marker_lines or self._origin is None:
            return
        a = self._marker_lines[0].value() + self._origin
        b = self._marker_lines[1].value() + self._origin
        t0, t1 = min(a, b), max(a, b)
        self._delta_label.setText(f"Δt = {t1 - t0:.4g} s")
        rows = [
            (token, series.stats(t0, t1))
            for token in self._curves
            if (series := self._panel.series(token)) is not None
        ]
        self._stats_table.setRowCount(len(rows))
        for row, (token, stats) in enumerate(rows):
            cells = [self._panel.row_label(token)]
            cells += ["—"] * 3 if stats is None else [f"{v:.4g}" for v in stats]
            for col, text in enumerate(cells):
                self._stats_table.setItem(row, col, QTableWidgetItem(text))

    # --- window lifecycle -------------------------------------------------

    def showEvent(self, event) -> None:
        self._rebuild_rows()
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.hide()  # keep the widget (and its state) for reopening
        event.accept()
