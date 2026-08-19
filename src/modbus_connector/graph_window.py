import html

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QCloseEvent, QColor
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from modbus_connector import icons, theme
from modbus_connector.help_dialog import GRAPH_HELP, make_help_button
from modbus_connector.i18n import tr
from modbus_connector.registers_panel import RegistersPanel
from modbus_connector.timeseries import TimeSeries

MODES = ("Follow", "Full", "Manual")
MARKER_PENS = (pg.mkPen((60, 180, 75), width=2), pg.mkPen((200, 60, 60), width=2))
# matplotlib tab10-like hues, darkened where tab10 is too pale on white;
# the dark theme keeps pyqtgraph's intColor palette (made for dark backgrounds)
LIGHT_CURVE_COLORS = (
    (31, 119, 180),
    (214, 116, 10),
    (44, 140, 44),
    (200, 30, 30),
    (128, 90, 175),
    (140, 86, 75),
    (200, 90, 165),
    (110, 110, 110),
    (130, 122, 15),
)


def _curve_color(index: int) -> QColor:
    """Цвет кривой по теме: intColor на тёмной, контрастный набор на светлой."""
    if theme.is_dark():
        return pg.intColor(index, hues=len(LIGHT_CURVE_COLORS))
    return QColor(*LIGHT_CURVE_COLORS[index % len(LIGHT_CURVE_COLORS)])


class GraphWindow(QWidget):
    """Живой график выбранных строк таблицы регистров (отдельное окно)."""

    def __init__(self, panel: RegistersPanel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Window)
        self._panel = panel
        self._translatable: list[tuple[QWidget, str]] = []  # (widget, English key)
        self._translatable_tips: list[tuple[QWidget, str]] = []
        self.setWindowTitle(tr("Graph"))
        self.resize(950, 520)

        self._seen: set[int] = set()
        self._checked: set[int] = set()
        self._expr_tokens: set[int] = set()  # обновляется в _rebuild_rows
        self._curves: dict[int, pg.PlotDataItem] = {}
        self._origin: float | None = None
        self._updating_range = False
        self._markers_need_placement = False
        self._view_ranged = False  # a refresh has ranged the view to the data

        self._rows_list = QListWidget()
        self._rows_list.itemChanged.connect(self._on_row_toggled)
        refresh_button = self._make_icon_button("Refresh rows", "read")
        refresh_button.clicked.connect(self._rebuild_rows)

        self._mode_combo = theme.FitComboBox()
        for mode in MODES:  # the English key rides in userData
            self._mode_combo.addItem(tr(mode), mode)
        self._window_spin = QDoubleSpinBox(minimum=1, maximum=86_400, value=60)
        self._window_spin.setSuffix(" s")
        zoom_rect_button = self._make_icon_button("Zoom rect", "scanner", checkable=True)
        zoom_rect_button.toggled.connect(self._on_zoom_rect_toggled)
        markers_button = self._make_icon_button("Markers", "markers", checkable=True)
        markers_button.toggled.connect(self._toggle_markers)
        reset_button = self._make_icon_button("Reset view", "follow")
        reset_button.clicked.connect(self._reset_view)
        self._clear_button = self._make_icon_button(
            "Clear",
            "clear",
            tip="Clear recorded history and restart the time axis",
        )
        self._clear_button.clicked.connect(self._on_clear)
        self._poll_button = icons.make_button(tr("Start polling and record"), "poll_start")
        self._poll_button.setEnabled(False)  # no bus access until a connection is up
        self._poll_button.clicked.connect(self._on_poll_toggle)

        self._plot = pg.PlotWidget()
        background, foreground = theme.graph_colors()
        pg.setConfigOptions(background=background, foreground=foreground)
        self._plot.setBackground(background)
        self._plot.showGrid(x=True, y=True, alpha=0.3)
        self._plot.setLabel("bottom", tr("time, s (relative)"))
        self._plot.addLegend(offset=(4, 4))
        viewbox = self._plot.getPlotItem().getViewBox()
        viewbox.sigRangeChanged.connect(self._on_range_changed)
        self._viewbox = viewbox

        # hover crosshair: vertical hair + per-series readout at the cursor
        # (hover moves without a pressed button need mouse tracking on the
        # view and its viewport — GraphicsView enables it, we make it explicit)
        self._plot.setMouseTracking(True)
        self._plot.viewport().setMouseTracking(True)
        self._crosshair = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen(theme.crosshair_color(), width=2, style=Qt.PenStyle.DashLine),
        )
        self._crosshair.setVisible(False)
        self._plot.addItem(self._crosshair, ignoreBounds=True)
        self._readout = pg.TextItem(anchor=(1, 0))  # top-right: the legend owns top-left
        self._readout.setVisible(False)
        self._plot.addItem(self._readout, ignoreBounds=True)
        self._crosshair_dots = pg.ScatterPlotItem(
            size=9, symbol="o", pen=pg.mkPen(None)
        )
        self._plot.addItem(self._crosshair_dots, ignoreBounds=True)
        self._mouse_proxy = pg.SignalProxy(
            self._plot.scene().sigMouseMoved, rateLimit=60, slot=self._on_mouse_moved
        )
        self.update_theme()  # axis/crosshair pens follow the current theme

        self._marker_lines: list[pg.InfiniteLine] = []
        self._delta_label = QLabel()
        self._stats_table = QTableWidget(0, 4)
        self._stats_table.setHorizontalHeaderLabels(
            [tr(h) for h in ("Series", "Min", "Max", "Avg")]
        )
        self._stats_table.verticalHeader().setVisible(False)
        self._stats_table.setMaximumHeight(120)
        self._stats_box = QWidget()
        stats_layout = QVBoxLayout(self._stats_box)
        stats_layout.setContentsMargins(0, 0, 0, 0)
        stats_layout.addWidget(self._delta_label)
        stats_layout.addWidget(self._stats_table)
        self._stats_box.hide()

        controls = QHBoxLayout()
        x_scale_label = QLabel()
        self._track(x_scale_label, "X scale:")
        controls.addWidget(x_scale_label)
        controls.addWidget(self._mode_combo)
        controls.addWidget(self._window_spin)
        controls.addWidget(zoom_rect_button)
        controls.addWidget(markers_button)
        controls.addWidget(reset_button)
        controls.addWidget(self._clear_button)
        controls.addWidget(self._poll_button)
        controls.addStretch(1)
        self._help_button = make_help_button(self, "Graph — Help", GRAPH_HELP)
        self._help_button.setIcon(icons.icon("help"))  # replace the "?" glyph
        icons.register(self._help_button, "help")
        controls.addWidget(self._help_button)

        right = QVBoxLayout()
        right.addLayout(controls)
        right.addWidget(self._plot, 1)
        right.addWidget(self._stats_box)

        left = QVBoxLayout()
        series_label = QLabel()
        self._track(series_label, "Series:")
        left.addWidget(series_label)
        left.addWidget(self._rows_list, 1)
        left.addWidget(refresh_button)

        layout = QHBoxLayout(self)
        layout.addLayout(left, 0)
        layout.addLayout(right, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._refresh)

        panel.rowsChanged.connect(self._rebuild_rows)
        panel.pollStateChanged.connect(self._sync_poll_button)
        self._sync_poll_button(panel.is_polling(), panel.is_recording())
        self._rebuild_rows()

    # --- series checklist ------------------------------------------------

    @Slot()
    def _rebuild_rows(self) -> None:
        row_tokens = self._panel.row_tokens()
        # rows opted out of polling are hidden from the graph entirely
        tokens = [t for t in row_tokens if self._panel.row_poll_enabled(t)]
        # expressions are always listed: they have no poll checkbox
        expr_tokens = self._panel.expr_tokens()
        self._expr_tokens = set(expr_tokens)
        all_tokens = row_tokens + expr_tokens
        listed = tokens + expr_tokens
        for token in listed:
            if token not in self._seen:  # first sight: plot new rows by default
                self._checked.add(token)
        self._seen.update(all_tokens)
        self._checked &= set(all_tokens)  # forget deleted rows, keep hidden ones
        self._rows_list.blockSignals(True)
        self._rows_list.clear()
        for token in listed:
            item = QListWidgetItem(self._token_label(token))
            item.setData(Qt.ItemDataRole.UserRole, token)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if token in self._checked
                else Qt.CheckState.Unchecked
            )
            self._rows_list.addItem(item)
        self._rows_list.blockSignals(False)
        visible = set(listed) & self._checked
        for token in list(self._curves):
            if token not in visible:
                self._remove_curve(token)
        for token in visible:
            if token not in self._curves:
                self._add_curve(token)

    def _token_label(self, token: int) -> str:
        if token in self._expr_tokens:
            return f"fx {self._panel.expr_label(token)}"
        return self._panel.row_label(token)

    def _token_series(self, token: int) -> TimeSeries | None:
        if token in self._expr_tokens:
            return self._panel.expr_series(token)
        return self._panel.series(token)

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
        pen = pg.mkPen(_curve_color(len(self._curves)), width=2)
        self._curves[token] = self._plot.plot(
            [], [], pen=pen, name=self._token_label(token)
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
            series = self._token_series(token)
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
        mode = self._mode_combo.currentData()  # English key, display is translated
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
                if (series := self._token_series(token)) is not None
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
        if not self._updating_range and self._mode_combo.currentData() != "Manual":
            self._set_mode("Manual")
        self._pin_readout()

    def _set_mode(self, mode: str) -> None:
        self._mode_combo.setCurrentIndex(self._mode_combo.findData(mode))

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
        self._set_mode("Follow")

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

    def set_bus_enabled(self, ok: bool) -> None:
        self._poll_button.setEnabled(ok)

    def _track(self, widget: QWidget, text: str, tip: str | None = None) -> None:
        widget.setText(tr(text))
        self._translatable.append((widget, text))
        if tip is not None:
            widget.setToolTip(tr(tip))
            self._translatable_tips.append((widget, tip))

    def _make_icon_button(
        self, text: str, icon_name: str, tip: str | None = None, *, checkable: bool = False
    ) -> QToolButton:
        """Иконочная кнопка: текст скрыт (ToolButtonIconOnly), но text() его
        возвращает; тултип — лейбл (или более длинная подсказка tip)."""
        button = icons.make_button(tr(text), icon_name, checkable=checkable)
        self._translatable.append((button, text))
        tip_key = tip if tip is not None else text  # tooltip follows the label
        button.setToolTip(tr(tip_key))
        self._translatable_tips.append((button, tip_key))
        return button

    def retranslate(self) -> None:
        """Переприменить tr() ко всем строкам окна (по смене языка)."""
        self.setWindowTitle(tr("Graph"))
        for widget, text in self._translatable:
            widget.setText(tr(text))
        for widget, tip in self._translatable_tips:
            widget.setToolTip(tr(tip))
        for index in range(self._mode_combo.count()):
            self._mode_combo.setItemText(
                index, tr(self._mode_combo.itemData(index))
            )
        self._stats_table.setHorizontalHeaderLabels(
            [tr(h) for h in ("Series", "Min", "Max", "Avg")]
        )
        self._plot.setLabel("bottom", tr("time, s (relative)"))
        # state-dependent text goes through its sync path, not a stale snapshot
        self._sync_poll_button(self._panel.is_polling(), self._panel.is_recording())

    def update_theme(self) -> None:
        """Перекрасить открытый график под текущую тему (вызывает MainWindow)."""
        background, foreground = theme.graph_colors()
        pg.setConfigOptions(background=background, foreground=foreground)
        self._plot.setBackground(background)
        plot_item = self._plot.getPlotItem()
        for name in ("left", "bottom"):
            axis = plot_item.getAxis(name)
            axis.setPen(pg.mkPen(foreground))
            axis.setTextPen(pg.mkPen(foreground))
        if plot_item.legend is not None:  # label colors do NOT follow the pens
            legend = plot_item.legend
            legend.setLabelTextColor(foreground)
            for _sample, label in legend.items:
                # setAttr only stores the color; the label's HTML (with the
                # color baked in) is regenerated by setText
                label.setText(label.text)
        for index, curve in enumerate(self._curves.values()):
            curve.setPen(pg.mkPen(_curve_color(index), width=2))
        self._crosshair.setPen(
            pg.mkPen(theme.crosshair_color(), width=2, style=Qt.PenStyle.DashLine)
        )

    @Slot()
    def _on_poll_toggle(self) -> None:
        if self._panel.is_polling() and self._panel.is_recording():
            self._panel.stop_polling()
        else:
            # stopped, or polling without recording: (re)start with recording on
            self._panel.start_polling(True)

    @Slot(bool, bool)
    def _sync_poll_button(self, polling: bool, recording: bool) -> None:
        active = polling and recording
        text = tr("Stop polling") if active else tr("Start polling and record")
        icon_name = "poll_stop" if active else "poll_start"
        self._poll_button.setText(text)
        self._poll_button.setToolTip(text)
        self._poll_button.setAccessibleName(text)
        self._poll_button.setIcon(icons.icon(icon_name))
        icons.register(self._poll_button, icon_name)  # refresh_icons uses the name

    # --- markers ----------------------------------------------------------

    def _data_extent(self) -> tuple[float, float] | None:
        """Относительный диапазон данных по всем кривым; None — данных нет."""
        extents = []
        for token in self._curves:
            series = self._token_series(token)
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
            not self._view_ranged and self._mode_combo.currentData() != "Manual"
        )

    @Slot()
    def _update_stats(self) -> None:
        if not self._marker_lines or self._origin is None:
            return
        a = self._marker_lines[0].value() + self._origin
        b = self._marker_lines[1].value() + self._origin
        t0, t1 = min(a, b), max(a, b)
        self._delta_label.setText(tr("Δt = {dt:.4g} s", dt=t1 - t0))
        rows = [
            (token, series.stats(t0, t1))
            for token in self._curves
            if (series := self._token_series(token)) is not None
        ]
        self._stats_table.setRowCount(len(rows))
        for row, (token, stats) in enumerate(rows):
            cells = [self._token_label(token)]
            cells += ["—"] * 3 if stats is None else [f"{v:.4g}" for v in stats]
            for col, text in enumerate(cells):
                self._stats_table.setItem(row, col, QTableWidgetItem(text))

    # --- hover crosshair ----------------------------------------------------

    def _on_mouse_moved(self, args: tuple) -> None:
        pos = args[0]  # SignalProxy delivers the signal's arguments as a tuple
        if self._viewbox.sceneBoundingRect().contains(pos):
            self._update_crosshair(self._viewbox.mapSceneToView(pos).x())
        else:
            self._update_crosshair(None)  # left the plot area: hide

    def _update_crosshair(self, view_x: float | None) -> None:
        if view_x is None:
            self._crosshair.setVisible(False)
            self._readout.setVisible(False)
            self._crosshair_dots.setData([], [])
            return
        self._crosshair.setValue(view_x)
        self._crosshair.setVisible(True)
        header = "#DDDDDD" if theme.is_dark() else "#333333"
        lines = [f'<div style="color: {header}">t = {view_x:.4g} s</div>']
        dot_xs, dot_ys, dot_brushes = [], [], []
        for token, curve in self._curves.items():
            color = curve.opts["pen"].color().name()
            name = html.escape(self._token_label(token))
            text = "—"
            xdata, ydata = curve.getData()
            if xdata is not None and len(xdata) and xdata[0] <= view_x <= xdata[-1]:
                index = self._nearest_index(xdata, view_x)
                text = f"{ydata[index]:.4g}"
                dot_xs.append(xdata[index])
                dot_ys.append(ydata[index])
                dot_brushes.append(pg.mkBrush(color))
            lines.append(f'<span style="color: {color}">{name}: {text}</span>')
        self._readout.setHtml("<br>".join(lines))
        self._readout.setVisible(True)
        self._pin_readout()
        self._crosshair_dots.setData(dot_xs, dot_ys, brush=dot_brushes)

    def _pin_readout(self) -> None:
        # TextItem lives in data coordinates (GraphicsObject has no anchor
        # mixin like the legend's), so re-pin it to the view corner whenever
        # the range changes — otherwise it would drift while panning
        if not self._readout.isVisible():
            return
        (vx0, vx1), (vy0, vy1) = self._viewbox.viewRange()  # top-right corner
        self._readout.setPos(vx1 - (vx1 - vx0) * 0.02, vy1 - (vy1 - vy0) * 0.03)

    @staticmethod
    def _nearest_index(xdata: np.ndarray, view_x: float) -> int:
        """Индекс ближайшего отсчёта (данные с поллинга — без интерполяции)."""
        index = min(int(np.searchsorted(xdata, view_x)), len(xdata) - 1)
        if index > 0 and abs(xdata[index - 1] - view_x) <= abs(xdata[index] - view_x):
            index -= 1
        return index

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
