import csv
import io
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import get_args

from PySide6.QtCore import QItemSelection, QItemSelectionModel, QPoint, Qt, QTimer, Signal, Slot
from PySide6.QtGui import (
    QBrush,
    QGuiApplication,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QShortcut,
)
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSpinBox,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from modbus_connector import theme
from modbus_connector.csv_dialogs import ExportColumnsDialog, ImportMappingDialog
from modbus_connector.datalogger import (
    LOG_FIELDS,
    DataLogger,
    LogFormat,
    LogSample,
    LogSettings,
)
from modbus_connector.datalogger_dialog import LoggingSettingsDialog
from modbus_connector.models import (
    CSV_COLUMNS,
    ByteOrder,
    DisplayFormat,
    RegisterKind,
    RegisterRow,
    RowDisplaySettings,
    csv_header,
    decode_register_values,
    format_register_values,
    format_scaled_values,
    format_values,
    parse_values,
    row_to_csv_record,
    rows_from_csv,
)
from modbus_connector.timeseries import TimeSeries

KINDS = list(get_args(RegisterKind))
FORMATS = list(get_args(DisplayFormat))
ORDERS = list(get_args(ByteOrder))
REGISTER_KINDS = ("holding_registers", "input_registers")

(
    COL_NAME,
    COL_TYPE,
    COL_ADDRESS,
    COL_COUNT,
    COL_UNIT_ID,
    COL_POLL,
    COL_FORMAT,
    COL_VALUE,
    COL_NEW_VALUE,
    COL_TREND,
    COL_ACTIONS,
) = range(11)


class SparklineWidget(QWidget):
    """Крошечный тренд строки: линия по последним точкам, авто-масштаб Y."""

    MAX_POINTS = 300

    def __init__(self, series: TimeSeries, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._series = series
        self.setFixedSize(110, 28)

    def refresh(self) -> None:
        values = self._series.points()[1][-self.MAX_POINTS :]
        self.setToolTip(
            f"min {min(values):g}  max {max(values):g}  last {values[-1]:g}"
            if values
            else ""
        )
        self.update()  # Qt coalesces repaints per event-loop pass

    def swap_series(self, other: "SparklineWidget") -> None:
        """Обменять ряды данных с соседним спарклайном (перестановка строк)."""
        self._series, other._series = other._series, self._series
        self.refresh()
        other.refresh()

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        values = self._series.points()[1][-self.MAX_POINTS :]
        if len(values) < 2:
            return
        lo, hi = min(values), max(values)
        span = hi - lo or 1.0
        width, height = self.width() - 1, self.height() - 1
        path = QPainterPath()
        for i, value in enumerate(values):
            x = i * width / (len(values) - 1)
            y = height - (value - lo) * height / span
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(theme.sparkline_color(), 1))
        painter.drawPath(path)
        painter.end()


def _entry_float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _parse_unit_id(text: str) -> int | None:
    try:
        unit = int(text) if text else None
    except ValueError:
        unit = None
    return unit if unit is not None and 1 <= unit <= 247 else None


def _parse_poll_ms(text: str) -> int | None:
    try:
        poll = int(text) if text else None
    except ValueError:
        poll = None
    return poll if poll is not None and poll >= 100 else None


class RegistersPanel(QWidget):
    readRequested = Signal(int, int, object)
    writeRequested = Signal(int, int, object, list)
    maskWriteRequested = Signal(int, int, int, int, int)
    readwriteRequested = Signal(int, int, int, int, int, list)
    rowsChanged = Signal()  # a row was added or removed
    pollStateChanged = Signal(bool, bool)  # (polling, recording) after any change
    logLine = Signal(str)

    def __init__(
        self,
        request_id_provider: Callable[[], int],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._next_request_id = request_id_provider
        self._unit_id = 1
        self._row_token_counter = 0
        self._pending_reads: dict[int, int] = {}
        self._pending_writes: dict[int, int] = {}
        self._pending_mask_writes: dict[int, int] = {}  # request_id -> address
        self._pending_readwrites: dict[int, int] = {}  # request_id -> write address
        self._row_timers: dict[int, QTimer] = {}  # row token -> per-row poll timer
        self._flash_generations: dict[int, int] = {}  # token -> latest flash generation
        self._row_display: dict[int, RowDisplaySettings] = {}  # token -> display settings
        self._display_dialog: QDialog | None = None
        self._series: dict[int, TimeSeries] = {}  # token -> value history (runtime only)
        self._sparklines: dict[int, SparklineWidget] = {}
        self._last_values: dict[int, list] = {}  # token -> last raw read values
        self._bus_enabled = False  # no bus access until a connection is up

        self._table = QTableWidget(0, 11)
        self._table.setHorizontalHeaderLabels(
            [
                "Name",
                "Type",
                "Address",
                "Count",
                "Unit ID",
                "Poll, ms",
                "Format",
                "Value",
                "New value",
                "Trend",
                "",
            ]
        )
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        for col, width in (
            (COL_NAME, 160),
            (COL_TYPE, 140),
            (COL_FORMAT, 90),
            (COL_VALUE, 140),
            (COL_NEW_VALUE, 120),
            (COL_TREND, 118),
        ):
            self._table.setColumnWidth(col, width)
        self._table.verticalHeader().setVisible(False)
        self._table.setToolTip(
            "Enter in 'New value' = write raw values (no scale/offset applied), "
            "Ctrl/Cmd+R = read current row, Ctrl/Cmd+Shift+R = read all rows"
        )
        self._table.itemChanged.connect(self._on_item_changed)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)

        read_shortcut = QShortcut(QKeySequence("Ctrl+R"), self)
        read_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        read_shortcut.activated.connect(self._read_current_row)
        read_all_shortcut = QShortcut(QKeySequence("Ctrl+Shift+R"), self)
        read_all_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        read_all_shortcut.activated.connect(self.read_all)
        move_up_shortcut = QShortcut(QKeySequence("Ctrl+Up"), self)
        move_up_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        move_up_shortcut.activated.connect(lambda: self._defer_move(-1))
        move_down_shortcut = QShortcut(QKeySequence("Ctrl+Down"), self)
        move_down_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        move_down_shortcut.activated.connect(lambda: self._defer_move(1))
        # quick value actions on the current row (same table context menu)
        for keys, slot in (
            (("Ctrl+C",), self._copy_current_value),
            (("Ctrl+0",), lambda: self._write_constant_to_current_row(0)),
            (("Ctrl+1",), lambda: self._write_constant_to_current_row(1)),
            # "+" needs Shift on the main keyboard, where "Ctrl++" never
            # matches; "Ctrl+=" is what people press, "Ctrl++" is the numpad
            (("Ctrl+=", "Ctrl++"), lambda: self._step_current_row(1)),
            (("Ctrl+-",), lambda: self._step_current_row(-1)),
            (("Ctrl+T",), self._toggle_current_row),
        ):
            shortcut = QShortcut(self)
            shortcut.setKeys([QKeySequence(key) for key in keys])
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)

        add_button = QPushButton("Add register")
        add_button.clicked.connect(lambda: self._add_row())
        self._read_all_button = QPushButton("Read all")
        self._read_all_button.setToolTip("Read every row once (Ctrl/Cmd+Shift+R)")
        self._read_all_button.clicked.connect(self.read_all)
        sort_button = QPushButton("Sort by address")
        sort_button.clicked.connect(self._sort_by_address)
        self._mask_write_button = QPushButton("Mask write (0x16)…")
        self._mask_write_button.setToolTip(
            "Set or clear individual bits of a holding register without touching "
            "others: result = (value AND and-mask) OR (or-mask AND NOT and-mask). "
            "Typical use: bit fields in PLC configuration registers."
        )
        self._mask_write_button.clicked.connect(self._on_mask_write)
        self._readwrite_button = QPushButton("Read/Write (0x17)…")
        self._readwrite_button.setToolTip(
            "Atomic transaction: write holding registers and read others in a "
            "single Modbus exchange (function 0x17). Used when a device requires "
            "read-modify-write without a race window."
        )
        self._readwrite_button.clicked.connect(self._on_readwrite)

        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Filter…")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.textChanged.connect(self._apply_filter)

        display_button = QPushButton("Display…")
        display_button.setToolTip("Per-row Scale/Offset/Unit and byte order settings")
        display_button.clicked.connect(self._on_display_settings)
        csv_button = QToolButton()
        csv_button.setText("CSV")
        csv_button.setToolTip("Import/export the register table as CSV")
        csv_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        csv_menu = QMenu(csv_button)
        csv_menu.addAction("Import table…", self._on_csv_import)
        csv_menu.addAction("Export…", self._on_csv_export)
        csv_button.setMenu(csv_menu)

        self._log_settings = LogSettings()
        self._logger = DataLogger()
        self._log_flush_timer = QTimer(self)
        self._log_flush_timer.setInterval(1000)
        self._log_flush_timer.timeout.connect(self._logger.flush)
        self._log_button = QPushButton("Log to file")
        self._log_button.setCheckable(True)
        self._log_button.clicked.connect(self._toggle_logging)
        self._log_settings_button = QToolButton()
        self._log_settings_button.setText("⚙")
        self._log_settings_button.setFixedSize(28, 28)
        self._log_settings_button.setToolTip("Logging settings…")
        self._log_settings_button.clicked.connect(self._on_logging_settings)
        self._sync_logging_ui()

        self._global_order_combo = theme.FitComboBox()
        self._global_order_combo.addItems(ORDERS)
        self._global_order_combo.setToolTip(
            "Default byte order for 32/64-bit formats "
            "(rows without an explicit order inherit it)"
        )

        self._poll_interval = QSpinBox(minimum=100, maximum=600_000, value=1000)
        self._poll_interval.setSuffix(" ms")
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_global_rows)
        self._record_mode = True  # last chosen start mode: poll+record by default
        self._recording = False  # capture runs only while polling with record
        self._poll_button = QToolButton()
        self._poll_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self._poll_button.setToolTip(
            "Poll all rows with the Interval period; the dropdown chooses whether "
            "value history is recorded for sparklines and the graph window "
            "(bounded buffer, ~10k samples per row)"
        )
        poll_menu = QMenu(self._poll_button)
        self._start_poll_action = poll_menu.addAction("Start polling")
        self._start_record_action = poll_menu.addAction("Start polling and record")
        self._start_poll_action.triggered.connect(lambda: self.start_polling(False))
        self._start_record_action.triggered.connect(lambda: self.start_polling(True))
        self._poll_button.setMenu(poll_menu)
        self._poll_button.clicked.connect(self._toggle_polling)
        self.pollStateChanged.connect(self._sync_poll_button)
        self._poll_button.setText("Start polling and record")

        top = QHBoxLayout()
        top.addWidget(add_button)
        top.addWidget(self._read_all_button)
        top.addWidget(sort_button)
        top.addWidget(self._mask_write_button)
        top.addWidget(self._readwrite_button)
        top.addWidget(self._filter_edit)
        top.addWidget(display_button)
        top.addWidget(csv_button)
        top.addWidget(self._log_button)
        top.addWidget(self._log_settings_button)
        top.addStretch(1)
        top.addWidget(QLabel("Order:"))
        top.addWidget(self._global_order_combo)
        top.addWidget(QLabel("Interval:"))
        top.addWidget(self._poll_interval)
        top.addWidget(self._poll_button)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self._table)

        self._add_row()
        self.set_bus_enabled(False)  # the app starts disconnected

    def set_bus_enabled(self, ok: bool) -> None:
        """Включить/выключить контролы, ходящие на шину (по connectionChanged)."""
        self._bus_enabled = ok
        for button in (
            self._read_all_button,
            self._mask_write_button,
            self._readwrite_button,
            self._poll_button,
            self._log_button,
        ):
            button.setEnabled(ok)

    def set_unit_id(self, unit: int) -> None:
        self._unit_id = unit

    def state(self) -> list[dict]:
        rows = []
        for index in range(self._table.rowCount()):
            name_item = self._table.item(index, COL_NAME)
            address_item = self._table.item(index, COL_ADDRESS)
            count_item = self._table.item(index, COL_COUNT)
            type_combo = self._table.cellWidget(index, COL_TYPE)
            format_combo = self._table.cellWidget(index, COL_FORMAT)
            settings = self._row_display.get(self._token_at(index), RowDisplaySettings())
            try:
                address = int(address_item.text().strip(), 0)
                count = int(count_item.text().strip(), 0)
            except (ValueError, AttributeError):
                continue
            rows.append(
                {
                    "name": name_item.text() if name_item else "",
                    "kind": type_combo.currentText(),
                    "address": address,
                    "count": count,
                    "unit_id": self._text_at(index, COL_UNIT_ID),
                    "poll_ms": self._text_at(index, COL_POLL),
                    "format": format_combo.currentText(),
                    "order": settings.order or "",  # "" = inherit the global order
                    "scale": settings.scale,
                    "offset": settings.offset,
                    "unit": settings.unit,
                    "log": settings.log,
                }
            )
        return rows

    def options_state(self) -> dict:
        header = self._table.horizontalHeader()
        return {
            "order": self._global_order_combo.currentText(),
            "column_widths": [
                header.sectionSize(col) for col in range(header.count())
            ],
        }

    def set_options(self, options: dict) -> None:
        if not isinstance(options, dict):
            return
        if options.get("order") in ORDERS:
            self._global_order_combo.setCurrentText(str(options["order"]))
        widths = options.get("column_widths")
        if isinstance(widths, list):
            header = self._table.horizontalHeader()
            for col, width in enumerate(widths[: header.count()]):
                # clamp so a corrupted file cannot hide or explode a column
                if isinstance(width, int | float) and not isinstance(width, bool):
                    header.resizeSection(col, int(min(2000, max(30, width))))

    def logging_state(self) -> dict:
        # settings persist; the on/off state is runtime-only
        return {
            "path": self._log_settings.path,
            "format": self._log_settings.format,
            "fields": [f for f in LOG_FIELDS if f in self._log_settings.fields],
            "append": self._log_settings.append,
        }

    def set_logging_state(self, state: dict) -> None:
        if not isinstance(state, dict):
            return
        settings = self._log_settings
        if state.get("format") in get_args(LogFormat):
            settings.format = state["format"]
        if "path" in state:
            settings.path = str(state["path"])
        if isinstance(state.get("fields"), list):
            settings.fields = frozenset(
                f for f in state["fields"] if f in LOG_FIELDS
            )
        if isinstance(state.get("append"), bool):
            settings.append = state["append"]

    def set_state(self, rows: list) -> None:
        while self._table.rowCount():
            self._table.removeRow(0)
        for entry in rows or []:
            try:
                row = RegisterRow(
                    name=str(entry.get("name", "")),
                    kind=entry.get("kind") if entry.get("kind") in KINDS else "holding_registers",
                    address=int(entry["address"]),
                    count=int(entry["count"]),
                    format=(
                        entry.get("format") if entry.get("format") in FORMATS else "dec"
                    ),
                    order=(
                        entry.get("order") if entry.get("order") in ORDERS else None
                    ),
                    scale=_entry_float(entry.get("scale"), 1.0),
                    offset=_entry_float(entry.get("offset"), 0.0),
                    unit=str(entry.get("unit") or ""),
                    unit_id=_parse_unit_id(str(entry.get("unit_id", "") or "").strip()),
                    poll_ms=_parse_poll_ms(str(entry.get("poll_ms", "") or "").strip()),
                )
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            self._add_row(row)
            # the log flag lives outside RegisterRow: apply it to the new token
            self._row_display[self._row_token_counter].log = bool(
                entry.get("log", True)
            )
        if self._table.rowCount() == 0:
            self._add_row()

    def add_rows(self, rows: list) -> None:
        """Добавить строки из сканера адресов (сигнал rowsAddRequested).

        Дубли (тот же kind+address, независимо от unit id) пропускаются
        со строкой в лог."""
        existing = set()
        for index in range(self._table.rowCount()):
            try:
                address = int(self._text_at(index, COL_ADDRESS), 0)
            except ValueError:
                continue
            existing.add(
                (self._table.cellWidget(index, COL_TYPE).currentText(), address)
            )
        added = skipped = 0
        for entry in rows:
            try:
                kind = entry["kind"] if entry["kind"] in KINDS else "holding_registers"
                address = int(entry["address"])
                count = int(entry.get("count", 1))
                unit = entry.get("unit_id")
                unit_id = int(unit) if unit is not None else None
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            if (
                not 0 <= address <= 0xFFFF
                or not 1 <= count <= 125
                or (unit_id is not None and not 1 <= unit_id <= 247)
            ):
                continue
            key = (kind, address)
            if key in existing:
                skipped += 1
                continue
            existing.add(key)
            self._add_row(
                RegisterRow(name="", kind=kind, address=address, count=count,
                            unit_id=unit_id)
            )
            added += 1
        if added or skipped:
            self.logLine.emit(
                f"← scanner: added {added} rows to the table"
                + (f", skipped {skipped} duplicates" if skipped else "")
            )

    def _add_row(self, row: RegisterRow | None = None) -> None:
        row = row or RegisterRow(name="", kind="holding_registers", address=0, count=1)
        index = self._table.rowCount()
        self._table.blockSignals(True)
        self._table.insertRow(index)
        self._row_token_counter += 1

        name_item = QTableWidgetItem(row.name)
        name_item.setData(Qt.ItemDataRole.UserRole, self._row_token_counter)
        self._table.setItem(index, COL_NAME, name_item)

        type_combo = theme.FitComboBox()
        type_combo.addItems(KINDS)
        type_combo.setCurrentText(row.kind)
        type_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._table.setCellWidget(index, COL_TYPE, type_combo)

        self._table.setItem(index, COL_ADDRESS, QTableWidgetItem(str(row.address)))
        self._table.setItem(index, COL_COUNT, QTableWidgetItem(str(row.count)))

        unit_id_item = QTableWidgetItem("" if row.unit_id is None else str(row.unit_id))
        unit_id_item.setToolTip("Modbus unit 1..247, empty = unit from the connection panel")
        self._table.setItem(index, COL_UNIT_ID, unit_id_item)

        poll_item = QTableWidgetItem("" if row.poll_ms is None else str(row.poll_ms))
        poll_item.setToolTip(
            "Per-row poll interval in ms, empty = global interval; "
            "a row with its own interval is polled by a dedicated timer"
        )
        self._table.setItem(index, COL_POLL, poll_item)

        format_combo = theme.FitComboBox()
        format_combo.addItems(FORMATS)
        format_combo.setCurrentText(row.format)
        format_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        format_combo.setToolTip("Display format (registers only; coils/discrete show 0/1)")
        self._table.setCellWidget(index, COL_FORMAT, format_combo)

        self._row_display[self._row_token_counter] = RowDisplaySettings(
            scale=row.scale, offset=row.offset, unit=row.unit, order=row.order
        )

        value_item = QTableWidgetItem("")
        value_item.setFlags(value_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._table.setItem(index, COL_VALUE, value_item)
        self._table.setItem(index, COL_NEW_VALUE, QTableWidgetItem(""))

        series = TimeSeries()
        sparkline = SparklineWidget(series)
        self._series[self._row_token_counter] = series
        self._sparklines[self._row_token_counter] = sparkline
        self._table.setCellWidget(index, COL_TREND, sparkline)

        delete_button = QToolButton()
        delete_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarCloseButton)
        )
        delete_button.setFixedSize(26, 26)
        delete_button.setToolTip("Delete row")
        delete_button.clicked.connect(self._on_delete_clicked)
        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(2, 2, 2, 2)
        actions_layout.addWidget(delete_button)
        self._table.setCellWidget(index, COL_ACTIONS, actions)
        self._table.blockSignals(False)

        self._apply_filter_to_row(index)
        self._sync_row_timer(index)  # no-op unless polling is active
        self.rowsChanged.emit()

    @Slot()
    def _apply_filter(self) -> None:
        for index in range(self._table.rowCount()):
            self._apply_filter_to_row(index)

    def _apply_filter_to_row(self, index: int) -> None:
        needle = self._filter_edit.text().strip().lower()
        visible = True
        if needle:
            haystack = " ".join(
                (
                    self._text_at(index, COL_NAME),
                    self._table.cellWidget(index, COL_TYPE).currentText(),
                    self._text_at(index, COL_ADDRESS),
                    self._text_at(index, COL_UNIT_ID),
                    self._text_at(index, COL_POLL),
                )
            ).lower()
            visible = needle in haystack
        self._table.setRowHidden(index, not visible)

    @Slot()
    def _sort_by_address(self) -> None:
        def address_key(index: int) -> tuple[int, int]:
            try:
                address = int(self._text_at(index, COL_ADDRESS), 0)
            except ValueError:
                address = -1  # unparseable addresses sort first but still move
            return (address, index)

        for index in range(self._table.rowCount()):
            best = min(range(index, self._table.rowCount()), key=address_key)
            for row in range(best, index, -1):  # bubble up: stable
                self._swap_rows(row - 1, row)
        self._apply_filter()

    def _swap_rows(self, a: int, b: int) -> None:
        """Поменять строки местами. Виджеты (комбо/спарклайн/кнопка) НЕ
        отсоединяются от ячеек: removeCellWidget при перестройке строк
        оставляет в представлении битые ссылки, и следующая смена stylesheet
        падает в QTableView.updateEditorGeometries — поэтому пункты таблицы
        клонируются, а виджеты меняются только состоянием."""
        table = self._table
        table.blockSignals(True)
        for col in range(table.columnCount()):
            item_a = table.item(a, col)
            item_b = table.item(b, col)
            if item_a is None or item_b is None:
                continue  # widget-only columns are identical on every row
            # clone BEFORE setItem: setItem deletes the item it replaces
            clone_a, clone_b = item_a.clone(), item_b.clone()
            table.setItem(a, col, clone_b)
            table.setItem(b, col, clone_a)
        for col in (COL_TYPE, COL_FORMAT):
            combo_a = table.cellWidget(a, col)
            combo_b = table.cellWidget(b, col)
            text_a, text_b = combo_a.currentText(), combo_b.currentText()
            combo_a.setCurrentText(text_b)
            combo_b.setCurrentText(text_a)
        sparkline_a = table.cellWidget(a, COL_TREND)
        sparkline_b = table.cellWidget(b, COL_TREND)
        sparkline_a.swap_series(sparkline_b)
        # the ✕ button finds its row dynamically — nothing to swap
        table.blockSignals(False)

    def _defer_move(self, delta: int) -> None:
        # the hotkey fires mid key-event; rebuilding rows synchronously inside
        # the event corrupts the view — move after the event returns
        QTimer.singleShot(0, lambda: self._move_selected_rows(delta))

    def _move_selected_rows(self, delta: int) -> None:
        """Сдвинуть выбранные строки блоком на одну позицию (Ctrl+Up/Down)."""
        count = self._table.rowCount()
        marked = [False] * count
        for index in self._table.selectedIndexes():
            marked[index.row()] = True
        if not any(marked):
            return
        if delta < 0:  # up: ascending, swap with the row above when it's free
            for i in range(1, count):
                if marked[i] and not marked[i - 1]:
                    self._swap_rows(i - 1, i)
                    marked[i - 1], marked[i] = True, False
        else:  # down: descending mirror
            for i in range(count - 2, -1, -1):
                if marked[i] and not marked[i + 1]:
                    self._swap_rows(i, i + 1)
                    marked[i], marked[i + 1] = False, True
        # keep the selection (and the cursor) on the moved rows
        model = self._table.model()
        last_col = self._table.columnCount() - 1
        selection = QItemSelection()
        for row, mark in enumerate(marked):
            if mark:
                selection.select(model.index(row, 0), model.index(row, last_col))
        self._table.selectionModel().select(
            selection,
            QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QItemSelectionModel.SelectionFlag.Rows,
        )
        self._table.selectionModel().setCurrentIndex(
            model.index(marked.index(True), COL_NAME),
            QItemSelectionModel.SelectionFlag.NoUpdate,  # don't collapse the selection
        )

    @Slot()
    def _on_csv_import(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Import registers from CSV", str(Path.home()), "CSV (*.csv)"
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            header = csv_header(path.read_text(encoding="utf-8-sig"))
        except OSError as exc:
            self.logLine.emit(f"✗ failed to read {path}: {exc}")
            return
        if not header:  # no header row at all: let the parser report it in the log
            self.import_csv(path)
            return
        dialog = ImportMappingDialog(header, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.import_csv(path, dialog.mapping())

    @Slot()
    def _on_csv_export(self) -> None:
        dialog = ExportColumnsDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        columns = dialog.columns()
        if not columns:
            self.logLine.emit("✗ export: no columns selected")
            return
        path_str, _ = QFileDialog.getSaveFileName(
            self, "Export registers to CSV", str(Path.home() / "registers.csv"),
            "CSV (*.csv)",
        )
        if path_str:
            self.export_csv(Path(path_str), columns)

    def import_csv(self, path: Path, mapping: dict[str, str] | None = None) -> None:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            self.logLine.emit(f"✗ failed to read {path}: {exc}")
            return
        try:
            parsed = rows_from_csv(text, mapping)
        except ValueError as exc:
            self.logLine.emit(f"✗ failed to import {path}: {exc}")
            return
        self.set_state(  # import replaces the whole table
            [
                {
                    "name": row.name,
                    "kind": row.kind,
                    "address": row.address,
                    "count": row.count,
                    "unit_id": "" if row.unit_id is None else str(row.unit_id),
                    "poll_ms": "" if row.poll_ms is None else str(row.poll_ms),
                    "format": row.format,
                    "order": display.order or "",
                    "scale": display.scale,
                    "offset": display.offset,
                    "unit": display.unit,
                }
                for row, display in parsed
            ]
        )
        self.logLine.emit(f"← imported {len(parsed)} rows from {path}")

    def export_csv(self, path: Path, columns: list[str] | None = None) -> None:
        # full snapshot format by default: table columns plus the Value cell
        # as displayed (the value column is ignored when importing back)
        columns = columns or [*CSV_COLUMNS, "value"]
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(columns)
        count = 0
        for index in range(self._table.rowCount()):
            row = self._row_data(index)
            if row is None:
                continue
            settings = self._row_display.get(self._token_at(index), RowDisplaySettings())
            record = row_to_csv_record(row, settings)
            record["value"] = self._text_at(index, COL_VALUE)
            writer.writerow([record.get(column, "") for column in columns])
            count += 1
        try:
            path.write_text(buffer.getvalue(), encoding="utf-8-sig")
        except OSError as exc:
            self.logLine.emit(f"✗ failed to write {path}: {exc}")
            return
        self.logLine.emit(f"→ exported {count} rows to {path}")

    @Slot()
    def _on_display_settings(self) -> None:
        if self._display_dialog is not None:  # already open
            self._display_dialog.raise_()
            self._display_dialog.activateWindow()
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Per-row display settings")
        table = QTableWidget(0, 6)
        table.setHorizontalHeaderLabels(
            ["Name", "Address", "Scale", "Offset", "Unit", "Order"]
        )
        table.blockSignals(True)
        for index in range(self._table.rowCount()):
            token = self._token_at(index)
            settings = self._row_display.setdefault(token, RowDisplaySettings())
            row_index = table.rowCount()
            table.insertRow(row_index)
            name_item = QTableWidgetItem(self._text_at(index, COL_NAME))
            name_item.setData(Qt.ItemDataRole.UserRole, token)
            address_item = QTableWidgetItem(self._text_at(index, COL_ADDRESS))
            for item in (name_item, address_item):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row_index, 0, name_item)
            table.setItem(row_index, 1, address_item)
            table.setItem(row_index, 2, QTableWidgetItem(f"{settings.scale:g}"))
            table.setItem(row_index, 3, QTableWidgetItem(f"{settings.offset:g}"))
            table.setItem(row_index, 4, QTableWidgetItem(settings.unit))
            order_combo = theme.FitComboBox()
            order_combo.addItems(["default", *ORDERS])
            order_combo.setCurrentText(settings.order or "default")
            order_combo.currentTextChanged.connect(
                lambda text, t=token: self._set_row_order(t, text)
            )
            table.setCellWidget(row_index, 5, order_combo)
        table.blockSignals(False)
        table.itemChanged.connect(
            lambda item: self._on_display_item_changed(table, item)
        )
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Rows added or deleted while this dialog is open"
                                " appear after reopening it."))
        layout.addWidget(table)
        layout.addWidget(buttons)
        dialog.resize(620, 350)
        # non-modal; rows deleted meanwhile are ignored via the token lookup
        dialog.finished.connect(self._on_display_dialog_closed)
        self._display_dialog = dialog
        dialog.show()

    def _on_display_dialog_closed(self, _result: int) -> None:
        self._display_dialog = None

    def _set_row_order(self, token: int, text: str) -> None:
        settings = self._row_display.get(token)
        if settings is not None:
            settings.order = text if text in ORDERS else None  # "default" = inherit

    def _on_display_item_changed(
        self, table: QTableWidget, item: QTableWidgetItem
    ) -> None:
        name_item = table.item(item.row(), 0)
        if name_item is None:
            return
        settings = self._row_display.get(int(name_item.data(Qt.ItemDataRole.UserRole)))
        if settings is None:
            return
        text = item.text().strip()
        if item.column() == 2:
            settings.scale = _entry_float(text, 1.0)
        elif item.column() == 3:
            settings.offset = _entry_float(text, 0.0)
        elif item.column() == 4:
            settings.unit = text

    @Slot()
    def _on_mask_write(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Mask write (function 0x16)")
        unit_edit = QLineEdit()
        unit_edit.setPlaceholderText("empty = global unit")
        address_edit = QLineEdit()
        and_edit = QLineEdit("0xFFFF")
        or_edit = QLineEdit("0x0000")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form = QFormLayout(dialog)
        form.addRow(
            QLabel(
                "Set/clear bits of one holding register:\n"
                "result = (value AND and-mask) OR (or-mask AND NOT and-mask).\n"
                "Masks accept decimal or hex (e.g. 0xFF0F)."
            )
        )
        form.addRow("Unit:", unit_edit)
        form.addRow("Address:", address_edit)
        form.addRow("AND mask:", and_edit)
        form.addRow("OR mask:", or_edit)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            address = int(address_edit.text().strip(), 0)
            and_mask = int(and_edit.text().strip(), 0)
            or_mask = int(or_edit.text().strip(), 0)
        except ValueError:
            self.logLine.emit("✗ mask write: invalid address/mask (dec or 0x… hex)")
            return
        if not 0 <= address <= 0xFFFF or not 0 <= and_mask <= 0xFFFF or not (
            0 <= or_mask <= 0xFFFF
        ):
            self.logLine.emit("✗ mask write: address/mask out of range 0..0xFFFF")
            return
        unit = _parse_unit_id(unit_edit.text().strip())
        request_id = self._next_request_id()
        self._pending_mask_writes[request_id] = address
        self.maskWriteRequested.emit(
            request_id, unit if unit is not None else self._unit_id, address,
            and_mask, or_mask,
        )

    @Slot(int, bool, str)
    def handle_mask_write_finished(self, request_id: int, ok: bool, error: str) -> None:
        address = self._pending_mask_writes.pop(request_id, None)
        if address is None:
            return
        if not ok:
            return  # the worker already logged the failure
        # re-read rows that cover the masked address so the effect is visible
        for index in range(self._table.rowCount()):
            row = self._row_data(index)
            if row is None or row.kind != "holding_registers":
                continue
            if row.address <= address < row.address + row.count:
                self._read_table_row(index)

    @Slot()
    def _on_readwrite(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Read/Write multiple registers (function 0x17)")
        unit_edit = QLineEdit()
        unit_edit.setPlaceholderText("empty = global unit")
        write_address_edit = QLineEdit()
        values_edit = QLineEdit()
        values_edit.setPlaceholderText("comma/space separated, hex ok")
        read_address_edit = QLineEdit()
        read_count_edit = QSpinBox(minimum=1, maximum=125, value=1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form = QFormLayout(dialog)
        form.addRow(
            QLabel(
                "One atomic exchange: write Values at Write address, then read\n"
                "Read count registers from Read address; read values go to the log.\n"
                "Addresses accept decimal or hex (e.g. 0x10)."
            )
        )
        form.addRow("Unit:", unit_edit)
        form.addRow("Write address:", write_address_edit)
        form.addRow("Values:", values_edit)
        form.addRow("Read address:", read_address_edit)
        form.addRow("Read count:", read_count_edit)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            write_address = int(write_address_edit.text().strip(), 0)
            read_address = int(read_address_edit.text().strip(), 0)
        except ValueError:
            self.logLine.emit("✗ read/write: invalid address (dec or 0x… hex)")
            return
        if not 0 <= write_address <= 0xFFFF or not 0 <= read_address <= 0xFFFF:
            self.logLine.emit("✗ read/write: address out of range 0..0xFFFF")
            return
        try:
            values = parse_values("holding_registers", values_edit.text())
        except ValueError as exc:
            self.logLine.emit(f"✗ parse error: {exc}")
            return
        unit = _parse_unit_id(unit_edit.text().strip())
        request_id = self._next_request_id()
        self._pending_readwrites[request_id] = write_address
        self.readwriteRequested.emit(
            request_id,
            unit if unit is not None else self._unit_id,
            read_address,
            read_count_edit.value(),
            write_address,
            [int(value) for value in values],
        )

    @Slot(int, bool, list, str)
    def handle_readwrite_finished(
        self, request_id: int, ok: bool, values: list, error: str
    ) -> None:
        write_address = self._pending_readwrites.pop(request_id, None)
        if write_address is None:
            return
        if not ok:
            return  # the worker already logged the failure
        self.logLine.emit(f"← read/write read values: {format_values(values)}")
        # re-read rows that cover the written address so the effect is visible
        for index in range(self._table.rowCount()):
            row = self._row_data(index)
            if row is None or row.kind != "holding_registers":
                continue
            if row.address <= write_address < row.address + row.count:
                self._read_table_row(index)

    def _token_at(self, index: int) -> int:
        item = self._table.item(index, COL_NAME)
        return int(item.data(Qt.ItemDataRole.UserRole)) if item else -1

    def _find_row_by_token(self, token: int) -> int | None:
        for index in range(self._table.rowCount()):
            if self._token_at(index) == token:
                return index
        return None

    def row_tokens(self) -> list[int]:
        return [self._token_at(index) for index in range(self._table.rowCount())]

    def row_label(self, token: int) -> str:
        index = self._find_row_by_token(token)
        if index is None:
            return "?"
        name = self._text_at(index, COL_NAME)
        if name:
            return name
        kind = self._table.cellWidget(index, COL_TYPE).currentText()
        return f"{kind}@{self._text_at(index, COL_ADDRESS)}"

    def series(self, token: int) -> TimeSeries | None:
        return self._series.get(token)

    def clear_series(self) -> None:
        for series in self._series.values():
            series.clear()
        for sparkline in self._sparklines.values():
            sparkline.refresh()

    def _on_table_context_menu(self, pos: QPoint) -> None:
        row = self._table.rowAt(pos.y())
        if row < 0:
            return
        # act on the clicked row — but a right-click on an already-selected
        # row keeps a multi-selection so batch actions apply to it
        if row not in {index.row() for index in self._table.selectedIndexes()}:
            self._table.setCurrentCell(row, max(self._table.columnAt(pos.x()), 0))
        menu = QMenu(self)
        for text, key, slot in (
            ("Move up", "Ctrl+Up", lambda: self._move_selected_rows(-1)),
            ("Move down", "Ctrl+Down", lambda: self._move_selected_rows(1)),
            ("Copy value", "Ctrl+C", self._copy_current_value),
            ("Write 0", "Ctrl+0", lambda: self._write_constant_to_current_row(0)),
            ("Write 1", "Ctrl+1", lambda: self._write_constant_to_current_row(1)),
            ("Increment", "Ctrl+=", lambda: self._step_current_row(1)),
            ("Decrement", "Ctrl+-", lambda: self._step_current_row(-1)),
            ("Toggle", "Ctrl+T", self._toggle_current_row),
        ):
            action = menu.addAction(text, slot)
            action.setShortcut(QKeySequence(key))  # shown next to the item
        if self._table.columnAt(pos.x()) == COL_TREND:
            menu.addSeparator()
            menu.addAction("Clear history", self.clear_series)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _text_at(self, index: int, col: int) -> str:
        item = self._table.item(index, col)
        return item.text().strip() if item else ""

    def _row_data(self, index: int) -> RegisterRow | None:
        type_combo = self._table.cellWidget(index, COL_TYPE)
        name_item = self._table.item(index, COL_NAME)
        address_item = self._table.item(index, COL_ADDRESS)
        count_item = self._table.item(index, COL_COUNT)
        try:
            address = int(address_item.text().strip(), 0) if address_item else -1
            count = int(count_item.text().strip(), 0) if count_item else -1
        except ValueError:
            address = count = -1
        if not 0 <= address <= 65535 or not 1 <= count <= 125:
            self.logLine.emit(f"✗ row {index + 1}: invalid address/count")
            return None
        settings = self._row_display.get(self._token_at(index), RowDisplaySettings())
        return RegisterRow(
            name=name_item.text() if name_item else "",
            kind=type_combo.currentText(),
            address=address,
            count=count,
            format=self._table.cellWidget(index, COL_FORMAT).currentText(),
            order=settings.order,
            scale=settings.scale,
            offset=settings.offset,
            unit=settings.unit,
            unit_id=_parse_unit_id(self._text_at(index, COL_UNIT_ID)),
            poll_ms=_parse_poll_ms(self._text_at(index, COL_POLL)),
        )

    def _row_of_sender(self) -> int | None:
        button = self.sender()
        if button is None:
            return None
        actions = button.parentWidget()
        for index in range(self._table.rowCount()):
            if self._table.cellWidget(index, COL_ACTIONS) is actions:
                return index
        return None

    @Slot()
    def _on_delete_clicked(self) -> None:
        index = self._row_of_sender()
        if index is not None:
            token = self._token_at(index)
            self._flash_generations.pop(token, None)
            self._row_display.pop(token, None)
            self._series.pop(token, None)
            self._last_values.pop(token, None)
            self._sparklines.pop(token, None)
            timer = self._row_timers.pop(token, None)
            if timer is not None:
                timer.stop()
                timer.deleteLater()
            self._table.removeRow(index)
            self.rowsChanged.emit()

    @Slot(QTableWidgetItem)
    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == COL_NEW_VALUE and item.text().strip():
            self._write_table_row(item.row())
        elif item.column() == COL_POLL:
            self._sync_row_timer(item.row())

    @Slot()
    def _read_current_row(self) -> None:
        index = self._table.currentRow()
        if index >= 0:
            self._read_table_row(index)

    def _read_table_row(self, index: int) -> None:
        if not self._bus_enabled:
            return  # no connection: bus controls are disabled, stay silent
        row = self._row_data(index)
        if row is None:
            return
        token = self._token_at(index)
        if token in self._pending_reads.values():
            return  # previous read still unanswered, don't pile up the worker queue
        request_id = self._next_request_id()
        self._pending_reads[request_id] = token
        unit = row.unit_id if row.unit_id is not None else self._unit_id
        self.readRequested.emit(request_id, unit, row)

    def _write_table_row(self, index: int) -> None:
        if not self._bus_enabled:
            return  # no connection: bus controls are disabled, stay silent
        row = self._row_data(index)
        if row is None:
            return
        new_value_item = self._table.item(index, COL_NEW_VALUE)
        text = new_value_item.text().strip() if new_value_item else ""
        try:
            values = parse_values(row.kind, text)
        except ValueError as exc:
            self.logLine.emit(f"✗ parse error: {exc}")
            return
        self._emit_write(index, row, values)
        if new_value_item is not None:
            # clear so re-entering the same value fires itemChanged again;
            # the resulting empty-text itemChanged is ignored by _on_item_changed
            new_value_item.setText("")

    def _emit_write(self, index: int, row: RegisterRow, values: list) -> None:
        request_id = self._next_request_id()
        self._pending_writes[request_id] = self._token_at(index)
        unit = row.unit_id if row.unit_id is not None else self._unit_id
        self.writeRequested.emit(request_id, unit, row, values)

    # --- quick value actions (hotkeys + context menu) ----------------------

    def _action_row(self) -> tuple[int, RegisterRow] | None:
        """Текущая строка для быстрой записи; None — действие не выполняется."""
        if not self._bus_enabled:
            return None  # silent, consistent with the bus gate
        index = self._table.currentRow()
        if index < 0:
            return None
        row = self._row_data(index)
        if row is None:
            return None
        if row.kind not in ("coils", "holding_registers"):
            self.logLine.emit(f"✗ row {index + 1}: {row.kind} is a read-only area")
            return None
        return index, row

    def _copy_current_value(self) -> None:
        index = self._table.currentRow()
        if index < 0:
            return
        text = self._text_at(index, COL_VALUE)
        if text:
            QGuiApplication.clipboard().setText(text)

    def _write_constant_to_current_row(self, value: int) -> None:
        found = self._action_row()
        if found is None:
            return
        index, row = found
        self._emit_write(index, row, [bool(value)] if row.kind == "coils" else [value])

    def _step_current_row(self, delta: int) -> None:
        found = self._action_row()
        if found is None:
            return
        index, row = found
        last = self._last_values.get(self._token_at(index))
        if not last:
            self.logLine.emit(f"✗ row {index + 1}: read the row before +/-")
            return
        lo, hi = (0, 1) if row.kind == "coils" else (0, 0xFFFF)
        value = min(hi, max(lo, int(last[0]) + delta))  # silent clamp
        self._emit_write(index, row, [bool(value)] if row.kind == "coils" else [value])

    def _toggle_current_row(self) -> None:
        found = self._action_row()
        if found is None:
            return
        index, row = found
        last = self._last_values.get(self._token_at(index))
        if not last:
            self.logLine.emit(f"✗ row {index + 1}: read the row before toggling")
            return
        if row.kind == "coils":
            self._emit_write(index, row, [not last[0]])
        else:
            self._emit_write(index, row, [1 if int(last[0]) == 0 else 0])

    @Slot()
    def read_all(self) -> None:
        for index in range(self._table.rowCount()):
            self._read_table_row(index)

    @Slot()
    def _poll_global_rows(self) -> None:
        # rows with a per-row interval have their own timer in _row_timers
        for index in range(self._table.rowCount()):
            if _parse_poll_ms(self._text_at(index, COL_POLL)) is None:
                self._read_table_row(index)

    def _sync_row_timer(self, index: int) -> None:
        token = self._token_at(index)
        poll_ms = _parse_poll_ms(self._text_at(index, COL_POLL))
        timer = self._row_timers.get(token)
        if poll_ms is None or not self._poll_timer.isActive():
            if timer is not None:
                timer.stop()
                timer.deleteLater()
                del self._row_timers[token]
            return
        if timer is None:
            timer = QTimer(self)
            timer.timeout.connect(lambda token=token: self._read_row_by_token(token))
            self._row_timers[token] = timer
        timer.start(poll_ms)

    def _read_row_by_token(self, token: int) -> None:
        index = self._find_row_by_token(token)
        if index is None:  # the row was deleted
            timer = self._row_timers.pop(token, None)
            if timer is not None:
                timer.stop()
                timer.deleteLater()
            return
        self._read_table_row(index)

    def _display_text(self, index: int, values: list) -> str:
        kind = self._table.cellWidget(index, COL_TYPE).currentText()
        if kind not in REGISTER_KINDS:
            return format_values(values)
        fmt = self._table.cellWidget(index, COL_FORMAT).currentText()
        # hex and ascii show raw data — scaling them is meaningless
        if fmt in ("hex", "ascii"):
            return format_register_values(values, fmt)
        settings = self._row_display.get(self._token_at(index), RowDisplaySettings())
        order = settings.order or self._global_order_combo.currentText()
        decoded = decode_register_values(values, fmt, order)
        if settings.scale != 1.0 or settings.offset != 0.0 or settings.unit:
            return format_scaled_values(
                decoded, settings.scale, settings.offset, settings.unit
            )
        return format_register_values(values, fmt, order)

    def _primary_value(self, index: int, values: list) -> float | None:
        """Первое декодированное число строки (со scale/offset) для графика."""
        if not values:
            return None
        kind = self._table.cellWidget(index, COL_TYPE).currentText()
        if kind not in REGISTER_KINDS:
            return float(int(values[0]))  # coil/discrete bit as 0.0/1.0
        fmt = self._table.cellWidget(index, COL_FORMAT).currentText()
        if fmt in ("hex", "ascii"):
            return None  # not numeric
        settings = self._row_display.get(self._token_at(index), RowDisplaySettings())
        order = settings.order or self._global_order_combo.currentText()
        decoded = decode_register_values(values, fmt, order)
        if not decoded:
            return None
        return decoded[0] * settings.scale + settings.offset

    def _log_value(self, index: int, values: list) -> str:
        """Машиночитаемое значение строки для лога: числа со scale/offset, но
        без единиц измерения; несколько значений — через «;» в одной ячейке."""
        kind = self._table.cellWidget(index, COL_TYPE).currentText()
        if kind not in REGISTER_KINDS:
            return ";".join("1" if value else "0" for value in values)  # bits
        fmt = self._table.cellWidget(index, COL_FORMAT).currentText()
        if fmt in ("hex", "ascii"):
            return format_register_values(values, fmt)  # raw string as-is
        settings = self._row_display.get(self._token_at(index), RowDisplaySettings())
        order = settings.order or self._global_order_combo.currentText()
        decoded = decode_register_values(values, fmt, order)
        return ";".join(
            f"{value * settings.scale + settings.offset:g}" for value in decoded
        )

    def _log_read(self, index: int, values: list) -> None:
        token = self._token_at(index)
        if not self._row_display.get(token, RowDisplaySettings()).log:
            return  # the row is excluded in the logging settings
        try:
            address = int(self._text_at(index, COL_ADDRESS), 0)
        except ValueError:
            address = -1
        self._logger.write(
            LogSample(
                timestamp=datetime.now().isoformat(timespec="milliseconds"),
                name=self._text_at(index, COL_NAME),
                address=address,
                kind=self._table.cellWidget(index, COL_TYPE).currentText(),
                value=self._log_value(index, values),
            )
        )

    @Slot(int, bool, list, str)
    def handle_read_finished(self, request_id: int, ok: bool, values: list, error: str) -> None:
        token = self._pending_reads.pop(request_id, None)
        if token is None:
            return
        index = self._find_row_by_token(token)
        if index is None:
            return
        if ok:
            self._last_values[token] = list(values)  # raw, for +/-/toggle
        if ok and self._logger.is_open:
            self._log_read(index, values)
        if ok and self._recording:
            primary = self._primary_value(index, values)
            if primary is not None:
                self._series[token].append(time.monotonic(), primary)
                self._sparklines[token].refresh()
        item = self._table.item(index, COL_VALUE)
        if item is not None:
            text = self._display_text(index, values) if ok else f"✗ {error}"
            if text != item.text():
                self._flash_value_cell(token, item)
            item.setText(text)

    def _flash_value_cell(self, token: int, item: QTableWidgetItem) -> None:
        item.setBackground(theme.flash_color())
        generation = self._flash_generations.get(token, 0) + 1
        self._flash_generations[token] = generation
        QTimer.singleShot(2000, lambda: self._clear_flash(token, generation))

    def _clear_flash(self, token: int, generation: int) -> None:
        if self._flash_generations.get(token) != generation:
            return  # superseded by a newer flash, its timer will do the clearing
        self._flash_generations.pop(token, None)
        index = self._find_row_by_token(token)  # the row may have been deleted or moved
        if index is None:
            return
        item = self._table.item(index, COL_VALUE)
        if item is not None:
            item.setBackground(QBrush())

    @Slot(int, bool, str)
    def handle_write_finished(self, request_id: int, ok: bool, error: str) -> None:
        token = self._pending_writes.pop(request_id, None)
        if token is None or not ok:
            return
        index = self._find_row_by_token(token)
        if index is not None:
            self._read_table_row(index)

    @Slot()
    def _toggle_polling(self) -> None:
        if self._poll_timer.isActive():
            self.stop_polling()
        else:
            self.start_polling(self._record_mode)

    def start_polling(self, record: bool) -> None:
        # choosing a mode while polling runs flips recording
        # without restarting the timers
        self._record_mode = record
        self._recording = record
        if not self._poll_timer.isActive():
            self._poll_timer.start(self._poll_interval.value())
            for index in range(self._table.rowCount()):
                self._sync_row_timer(index)
        self.pollStateChanged.emit(True, record)

    @Slot()
    def stop_polling(self) -> None:
        self._poll_timer.stop()
        self._pending_reads.clear()  # late responses from already-queued reads are ignored
        for timer in self._row_timers.values():
            timer.stop()
            timer.deleteLater()
        self._row_timers.clear()
        self._recording = False
        self.pollStateChanged.emit(False, False)

    def is_polling(self) -> bool:
        return self._poll_timer.isActive()

    def is_recording(self) -> bool:
        return self._recording

    @Slot(bool, bool)
    def _sync_poll_button(self, polling: bool, recording: bool) -> None:
        del recording
        if polling:
            self._poll_button.setText("Stop polling")
        else:
            self._poll_button.setText(
                "Start polling and record" if self._record_mode else "Start polling"
            )

    # --- logging to file --------------------------------------------------

    def is_logging(self) -> bool:
        return self._logger.is_open

    def start_logging(self) -> None:
        if self._logger.is_open:
            return
        if not self._log_settings.path and not self._edit_logging_settings():
            self._sync_logging_ui()  # a cancelled dialog must not leave the button on
            return
        try:
            self._logger.open(self._log_settings)
        except OSError as exc:
            self.logLine.emit(f"✗ logging: cannot open {self._log_settings.path}: {exc}")
            self._sync_logging_ui()
            return
        self._log_flush_timer.start()
        if not self.is_polling():  # logging needs reads: start polling
            self.start_polling(self._record_mode)  # records per the split mode
        self.logLine.emit(
            f"→ logging values to {self._log_settings.path} "
            f"({self._log_settings.format})"
        )
        self._sync_logging_ui()

    def stop_logging(self) -> None:
        if not self._logger.is_open:
            return
        self._log_flush_timer.stop()
        rows, path = self._logger.rows_written, self._log_settings.path
        self._logger.close()
        self.logLine.emit(f"← logging stopped: {rows} rows written to {path}")
        self._sync_logging_ui()  # polling keeps running on purpose

    @Slot()
    def _toggle_logging(self) -> None:
        if self._logger.is_open:
            self.stop_logging()
        else:
            self.start_logging()

    def _edit_logging_settings(self) -> bool:
        dialog = LoggingSettingsDialog(self._log_settings, self._log_row_entries(), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        self._log_settings = dialog.settings()
        self._apply_log_row_flags(dialog.row_flags())
        return True

    def _log_row_entries(self) -> list[tuple[int, str, bool]]:
        """(token, label, log) per table row for the logging settings dialog."""
        entries = []
        for index in range(self._table.rowCount()):
            token = self._token_at(index)
            name = self._text_at(index, COL_NAME)
            kind = self._table.cellWidget(index, COL_TYPE).currentText()
            address = self._text_at(index, COL_ADDRESS)
            label = f"{name} @ {address}" if name else f"{kind}@{address}"
            unit = self._text_at(index, COL_UNIT_ID)
            if unit:
                label += f" (unit {unit})"
            entries.append(
                (token, label, self._row_display.get(token, RowDisplaySettings()).log)
            )
        return entries

    def _apply_log_row_flags(self, flags: dict[int, bool]) -> None:
        for token, log in flags.items():
            self._row_display.setdefault(token, RowDisplaySettings()).log = log

    @Slot()
    def _on_logging_settings(self) -> None:
        self._edit_logging_settings()  # applies to the next logging run

    def _sync_logging_ui(self) -> None:
        is_open = self._logger.is_open
        self._log_button.setChecked(is_open)
        self._log_button.setToolTip(
            f"Logging to {self._log_settings.path} — click to stop"
            if is_open
            else "Log read values to a file (CSV or JSON Lines)"
        )
        self._log_settings_button.setEnabled(not is_open)
