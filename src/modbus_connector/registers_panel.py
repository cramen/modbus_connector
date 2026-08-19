import csv
import io
import math
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import get_args

from PySide6.QtCore import (
    QItemSelection,
    QItemSelectionModel,
    QModelIndex,
    QPoint,
    QSize,
    QStringListModel,
    Qt,
    QTimer,
    Signal,
    Slot,
)
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
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QSizePolicy,
    QSpinBox,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from modbus_connector import icons, theme
from modbus_connector.alarm_sound import AlarmSound
from modbus_connector.alarms_dialog import AlarmsDialog
from modbus_connector.csv_dialogs import ExportColumnsDialog, ImportMappingDialog
from modbus_connector.datalogger import (
    LOG_FIELDS,
    DataLogger,
    LogFormat,
    LogSample,
    LogSettings,
)
from modbus_connector.datalogger_dialog import LoggingSettingsDialog
from modbus_connector.help_dialog import (
    EXPRESSIONS_HELP,
    REGISTERS_HELP,
    make_help_button,
)
from modbus_connector.i18n import tr
from modbus_connector.models import (
    CSV_COLUMNS,
    EXPRESSION_CONSTANTS,
    EXPRESSION_FUNCTIONS,
    AlarmRule,
    ByteOrder,
    DisplayFormat,
    Expression,
    RegisterKind,
    RegisterRow,
    RowDisplaySettings,
    alarm_rule_to_json,
    alarm_rules_from_json,
    csv_header,
    decode_register_values,
    diff_snapshots,
    evaluate_alarm,
    format_register_values,
    format_scaled_values,
    format_values,
    parse_expression,
    parse_values,
    row_to_csv_record,
    rows_from_csv,
)
from modbus_connector.snapshot_dialog import DiffRow, SnapshotDiffDialog
from modbus_connector.timeseries import TimeSeries

KINDS = list(get_args(RegisterKind))
FORMATS = list(get_args(DisplayFormat))
ORDERS = list(get_args(ByteOrder))
REGISTER_KINDS = ("holding_registers", "input_registers")

# English keys for the table header; display text is translated from them
HEADER_LABELS = (
    "", "Name", "Type", "Address", "Count", "Unit ID", "Poll, ms",
    "Format", "Value", "New value", "Trend", "",
)
POLL_ENABLED_TIP = "Poll this row"  # checkbox column: header and cell tooltip
TABLE_TOOLTIP = (
    "Enter in 'New value' = write raw values (no scale/offset applied), "
    "Ctrl/Cmd+R = read current row, Ctrl/Cmd+Shift+R = read all rows"
)
POLL_BUTTON_TIP = (
    "Poll all rows with the Interval period; the dropdown chooses whether "
    "value history is recorded for sparklines and the graph window "
    "(bounded buffer, ~10k samples per row)"
)

(
    COL_POLL_ENABLED,
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
) = range(12)

# Stable string keys per column (aligned with COL_*): session state stores
# column order/hidden sets by key so future column insertions stay compatible
COLUMN_KEYS = (
    "poll_enabled", "name", "type", "address", "count", "unit_id",
    "poll_ms", "format", "value", "new_value", "trend", "actions",
)
KEY_TO_COL = {key: col for col, key in enumerate(COLUMN_KEYS)}
# data columns the user may hide via the header context menu; the two control
# columns (poll checkbox, delete button) always stay visible
DATA_COLUMNS = tuple(range(COL_NAME, COL_TREND + 1))

# Expressions block: computed rows over register values, under the main table
EXPR_HEADER_LABELS = ("Name", "Expression", "Value", "Trend", "")
(
    EXPR_COL_NAME,
    EXPR_COL_EXPR,
    EXPR_COL_VALUE,
    EXPR_COL_TREND,
    EXPR_COL_ACTIONS,
) = range(5)


class SparklineWidget(QWidget):
    """Крошечный тренд строки: линия по последним точкам, авто-масштаб Y."""

    MAX_POINTS = 300

    def __init__(self, series: TimeSeries, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._series = series
        # растягивается вслед за шириной колонки Trend (paint рисует по width())
        self.setMinimumSize(60, 20)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

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


class ExpressionDelegate(QStyledItemDelegate):
    """Редактор ячейки Expression с автодополнением (QCompleter в popup).

    Два режима по позиции курсора: внутри ссылки [… — имена строк регистров
    (кандидаты с закрывающей скобкой: "temp]"), снаружи — функции (со
    скобкой: "sqrt(") и константы pi/e. Модель пересобирается на каждое
    изменение текста из names_provider, поэтому переименование строки
    подхватывается сразу. Enter в попапе вставляет кандидата, не коммитя
    ячейку, Esc закрывает попап (стандарт QCompleter). Вставку по activated
    делает сам делегат: QCompleter только эмитит сигнал, текст в QLineEdit
    он не подставляет (см. qcompleter.cpp). extra_functions/extra_names —
    дополнительные кандидаты поверх EXPRESSION_FUNCTIONS/EXPRESSION_CONSTANTS
    (правила симулятора: rand(/randint( и t/prev).
    """

    def __init__(
        self,
        names_provider: Callable[[], list[str]],
        parent: QWidget | None = None,
        *,
        extra_functions: Iterable[str] = (),
        extra_names: Iterable[str] = (),
    ) -> None:
        super().__init__(parent)
        self._names_provider = names_provider
        self._extra_functions = tuple(extra_functions)
        self._extra_names = tuple(extra_names)

    def createEditor(
        self,
        parent: QWidget | None,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> QWidget:
        del option, index
        editor = QLineEdit(parent)
        model = QStringListModel(editor)
        completer = QCompleter(model, editor)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setWidget(editor)
        editor.textChanged.connect(
            lambda _text, e=editor, c=completer, m=model:
                self._refresh_completion(e, c, m)
        )
        completer.activated[str].connect(
            lambda completion, e=editor, c=completer:
                self._insert_completion(e, c, completion)
        )
        return editor

    @staticmethod
    def _insert_completion(
        editor: QLineEdit, completer: QCompleter, completion: str
    ) -> None:
        """Вставить выбранный кандидат вместо префикса перед курсором."""
        prefix = completer.completionPrefix()
        pos = editor.cursorPosition()
        text = editor.text()
        editor.setText(text[: pos - len(prefix)] + completion + text[pos:])
        editor.setCursorPosition(pos - len(prefix) + len(completion))

    def _refresh_completion(
        self, editor: QLineEdit, completer: QCompleter, model: QStringListModel
    ) -> None:
        prefix, words = self._completions(editor.text(), editor.cursorPosition())
        matches = [w for w in words if w.lower().startswith(prefix.lower())]
        # ровно одно точное совпадение = текст только что вставлен completer'ом:
        # без этого фильтра попап переоткрывался бы после каждой вставки
        if not matches or (len(matches) == 1 and matches[0].lower() == prefix.lower()):
            completer.popup().hide()
            return
        model.setStringList(words)
        completer.setCompletionPrefix(prefix)
        completer.complete()

    def _completions(self, text: str, pos: int) -> tuple[str, list[str]]:
        """(префикс перед курсором, кандидаты); пустой список — попап не нужен."""
        before = text[:pos]
        open_at = before.rfind("[")
        if open_at > before.rfind("]"):  # внутри ссылки: имена строк
            return before[open_at + 1 :], [
                f"{name}]" for name in self._names_provider()
            ]
        match = re.search(r"[A-Za-z_][A-Za-z0-9_]*$", before)
        if match is None:
            return "", []
        words = [f"{name}(" for name in EXPRESSION_FUNCTIONS]
        words += [f"{name}(" for name in self._extra_functions]
        words += list(EXPRESSION_CONSTANTS)
        words += list(self._extra_names)
        return match.group(0), words


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


_CONDITION_SYMBOLS = {"gt": ">", "lt": "<", "ge": ">=", "le": "<=", "eq": "==", "ne": "!="}


@dataclass(frozen=True)
class _SnapshotEntry:
    """Снимок одной строки: raw-значения (None — строку ещё не читали) и
    подпись на момент снапшота (живёт, даже если строку потом удалили)."""

    name: str
    kind: str
    address: str
    values: list | None


def _describe_rule(rule: AlarmRule) -> str:
    """Короткое условие правила для лог-строки аларма ("> 20", "in 10..30")."""
    if rule.condition in _CONDITION_SYMBOLS:
        return f"{_CONDITION_SYMBOLS[rule.condition]} {rule.value:g}"
    verb = "in" if rule.condition == "in_range" else "outside"
    return f"{verb} {rule.value:g}..{rule.value2:g}"


class RegistersPanel(QWidget):
    readRequested = Signal(int, int, object)
    writeRequested = Signal(int, int, object, list)
    maskWriteRequested = Signal(int, int, int, int, int)
    readwriteRequested = Signal(int, int, int, int, int, list)
    rowsChanged = Signal()  # a row was added/removed or its poll-enabled flag toggled
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
        self._active_alarms: dict[int, AlarmRule] = {}  # token -> rule currently firing
        self._alarm_sound = AlarmSound()  # звук фронта аларма (monkeypatch в тестах)
        self._snapshot: dict[int, _SnapshotEntry] | None = None  # in-memory, не persist
        self._snapshot_at = ""  # время снятия снапшота (подпись в окне diff)
        self._diff_dialog: SnapshotDiffDialog | None = None
        self._bus_enabled = False  # no bus access until a connection is up
        self._expr_token_counter = 0
        self._expr_parsed: dict[int, Expression | None] = {}  # None = невалидное
        self._expr_series: dict[int, TimeSeries] = {}  # expr token -> history
        self._expr_sparklines: dict[int, SparklineWidget] = {}
        self._expr_alarms: dict[int, list[AlarmRule]] = {}  # expr token -> правила
        self._expr_last: dict[int, float | None] = {}  # expr token -> last value
        self._translatable: list[tuple[QWidget, str]] = []  # (widget, English key)
        self._translatable_tips: list[tuple[QWidget, str]] = []

        self._table = QTableWidget(0, 12)
        self._sync_header()
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionsMovable(True)  # items/cellWidgets stay in logical cols
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._on_header_context_menu)
        for col, width in (
            (COL_POLL_ENABLED, 34),
            (COL_NAME, 160),
            (COL_TYPE, 140),
            (COL_FORMAT, 90),
            (COL_VALUE, 140),
            (COL_NEW_VALUE, 120),
            (COL_TREND, 118),
        ):
            self._table.setColumnWidth(col, width)
        self._table.verticalHeader().setVisible(False)
        self._table.setToolTip(tr(TABLE_TOOLTIP))
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

        add_button = icons.make_button(tr("Add register"), "add")
        self._track(add_button, "Add register")
        add_button.clicked.connect(lambda: self._add_row())
        self._read_all_button = icons.make_button(tr("Read all"), "read_all")
        self._track(
            self._read_all_button, "Read all", "Read every row once (Ctrl/Cmd+Shift+R)"
        )
        self._read_all_button.clicked.connect(self.read_all)
        sort_button = icons.make_button(tr("Sort by address"), "sort")
        self._track(sort_button, "Sort by address")
        sort_button.clicked.connect(self._sort_by_address)
        self._mask_write_button = icons.make_button(tr("Mask write (0x16)…"), "mask_write")
        self._track(
            self._mask_write_button,
            "Mask write (0x16)…",
            "Set or clear individual bits of a holding register without touching "
            "others: result = (value AND and-mask) OR (or-mask AND NOT and-mask). "
            "Typical use: bit fields in PLC configuration registers.",
        )
        self._mask_write_button.clicked.connect(self._on_mask_write)
        self._readwrite_button = icons.make_button(tr("Read/Write (0x17)…"), "readwrite")
        self._track(
            self._readwrite_button,
            "Read/Write (0x17)…",
            "Atomic transaction: write holding registers and read others in a "
            "single Modbus exchange (function 0x17). Used when a device requires "
            "read-modify-write without a race window.",
        )
        self._readwrite_button.clicked.connect(self._on_readwrite)

        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText(tr("Filter…"))
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.textChanged.connect(self._apply_filter)

        display_button = icons.make_button(tr("Display…"), "display")
        self._track(
            display_button,
            "Display…",
            "Per-row Scale/Offset/Unit and byte order settings",
        )
        display_button.clicked.connect(self._on_display_settings)
        alarms_button = icons.make_button(tr("Alarms…"), "alarm")
        self._track(
            alarms_button,
            "Alarms…",
            "Per-row alarm rules: highlight, log and beep when the scaled "
            "value matches a condition",
        )
        alarms_button.clicked.connect(self._on_alarms)
        self._expr_button = icons.make_button(tr("Expressions"), "expression",
                                              checkable=True)
        self._track(
            self._expr_button,
            "Expressions",
            "Computed rows over register values ([name] references), "
            "with their own sparklines and graph series",
        )
        self._expr_button.toggled.connect(self._on_expressions_toggled)
        csv_button = QToolButton()  # menu button: icon registered manually
        csv_button.setIcon(icons.icon("csv_export"))
        csv_button.setIconSize(QSize(icons.ICON_SIZE, icons.ICON_SIZE))
        csv_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        icons.register(csv_button, "csv_export")
        self._track(csv_button, "CSV", "Import/export the register table as CSV")
        csv_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        csv_menu = QMenu(csv_button)
        self._csv_import_action = csv_menu.addAction(
            tr("Import table…"), self._on_csv_import
        )
        self._csv_export_action = csv_menu.addAction(tr("Export…"), self._on_csv_export)
        csv_button.setMenu(csv_menu)

        self._snapshot_button = icons.make_button(tr("Snapshot"), "snapshot")
        self._track(
            self._snapshot_button,
            "Snapshot",
            "Remember current values of all rows for later comparison",
        )
        self._snapshot_button.clicked.connect(self.take_snapshot)
        self._diff_button = icons.make_button(tr("Diff…"), "diff")
        self._track(
            self._diff_button,
            "Diff…",
            "Compare the snapshot with the current values",
        )
        self._diff_button.setEnabled(False)  # активируется после первого снапшота
        self._diff_button.clicked.connect(self._on_diff)

        self._log_settings = LogSettings()
        self._logger = DataLogger()
        self._log_flush_timer = QTimer(self)
        self._log_flush_timer.setInterval(1000)
        self._log_flush_timer.timeout.connect(self._logger.flush)
        self._log_button = icons.make_button(  # text never changes
            tr("Log to file"), "log", checkable=True
        )
        self._log_button.clicked.connect(self._toggle_logging)
        self._log_settings_button = icons.make_button(tr("Logging settings…"), "settings")
        self._translatable.append((self._log_settings_button, "Logging settings…"))
        self._log_settings_button.clicked.connect(self._on_logging_settings)
        self._sync_logging_ui()

        self._global_order_combo = theme.FitComboBox()
        self._global_order_combo.addItems(ORDERS)
        self._translatable_tips.append(
            (
                self._global_order_combo,
                "Default byte order for 32/64-bit formats "
                "(rows without an explicit order inherit it)",
            )
        )
        self._global_order_combo.setToolTip(tr(self._translatable_tips[-1][1]))

        self._poll_interval = QSpinBox(minimum=100, maximum=600_000, value=1000)
        self._poll_interval.setSuffix(" ms")
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_global_rows)
        self._record_mode = True  # last chosen start mode: poll+record by default
        self._recording = False  # capture runs only while polling with record
        self._poll_button = QToolButton()  # menu button: icon registered manually
        self._poll_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self._poll_button.setIconSize(QSize(icons.ICON_SIZE, icons.ICON_SIZE))
        self._poll_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._translatable_tips.append((self._poll_button, POLL_BUTTON_TIP))
        self._poll_button.setToolTip(tr(POLL_BUTTON_TIP))
        poll_menu = QMenu(self._poll_button)
        self._start_poll_action = poll_menu.addAction(tr("Start polling"))
        self._start_record_action = poll_menu.addAction(tr("Start polling and record"))
        self._start_poll_action.triggered.connect(lambda: self.start_polling(False))
        self._start_record_action.triggered.connect(lambda: self.start_polling(True))
        self._poll_button.setMenu(poll_menu)
        self._poll_button.clicked.connect(self._toggle_polling)
        self.pollStateChanged.connect(self._sync_poll_button)
        self._sync_poll_button(False, False)  # sets icon + text + tooltip

        top = QHBoxLayout()
        top.addWidget(add_button)
        top.addWidget(self._read_all_button)
        top.addWidget(sort_button)
        top.addWidget(self._mask_write_button)
        top.addWidget(self._readwrite_button)
        top.addWidget(self._filter_edit)
        top.addWidget(display_button)
        top.addWidget(alarms_button)
        top.addWidget(self._expr_button)
        top.addWidget(csv_button)
        top.addWidget(self._snapshot_button)
        top.addWidget(self._diff_button)
        top.addWidget(self._log_button)
        top.addWidget(self._log_settings_button)
        top.addStretch(1)
        order_label = QLabel()
        self._track(order_label, "Order:")
        top.addWidget(order_label)
        top.addWidget(self._global_order_combo)
        interval_label = QLabel()
        self._track(interval_label, "Interval:")
        top.addWidget(interval_label)
        top.addWidget(self._poll_interval)
        top.addWidget(self._poll_button)
        self._help_button = make_help_button(
            self, "Registers — Help", REGISTERS_HELP
        )
        top.addWidget(self._help_button)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self._table, 1)
        layout.addWidget(self._expressions_widget())

        self._add_row()
        self.set_bus_enabled(False)  # the app starts disconnected

    def _expressions_widget(self) -> QWidget:
        """Скрываемый блок выражений под таблицей: тулбар (+) и таблица
        Name/Expression/Value/Trend/✕. Видимость — чекабельной кнопкой
        Expressions в основном тулбаре (state "expressions_visible")."""
        widget = QWidget()
        expr_bar = QHBoxLayout()
        expr_bar.setContentsMargins(0, 0, 0, 0)
        expr_label = QLabel()
        self._track(expr_label, "Expressions")
        expr_bar.addWidget(expr_label)
        expr_add_button = icons.make_button(tr("Add expression"), "add")
        self._track(
            expr_add_button,
            "Add expression",
            "Add a computed row; [name] references a register row's "
            "scaled value, e.g. ([temp] + [flow]) / 2",
        )
        expr_add_button.clicked.connect(self._on_add_expression)
        expr_bar.addWidget(expr_add_button)
        expr_bar.addStretch(1)
        self._expr_help_button = make_help_button(
            self, "Expressions — Help", EXPRESSIONS_HELP
        )
        expr_bar.addWidget(self._expr_help_button)

        self._expr_table = QTableWidget(0, 5)
        self._sync_expr_header()
        header = self._expr_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for col, width in (
            (EXPR_COL_NAME, 160),
            (EXPR_COL_EXPR, 260),
            (EXPR_COL_VALUE, 120),
            (EXPR_COL_TREND, 118),
        ):
            self._expr_table.setColumnWidth(col, width)
        self._expr_table.verticalHeader().setVisible(False)
        self._expr_table.setToolTip(
            tr(
                "Expressions compute over scaled row values: [name] is a row "
                "reference, functions abs/sqrt/sin/… and pi/e are available"
            )
        )
        self._expr_table.itemChanged.connect(self._on_expr_item_changed)
        # автодополнение в ячейке Expression: [ — имена строк, слово — функции
        self._expr_table.setItemDelegateForColumn(
            EXPR_COL_EXPR,
            ExpressionDelegate(self._expression_row_names, self._expr_table),
        )

        box = QVBoxLayout(widget)
        box.setContentsMargins(0, 0, 0, 0)
        box.addLayout(expr_bar)
        box.addWidget(self._expr_table)
        widget.hide()  # скрыт по умолчанию; открывает кнопка Expressions
        self._expr_widget = widget
        return widget

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

    def _track(self, widget: QWidget, text: str, tip: str | None = None) -> None:
        widget.setText(tr(text))
        self._translatable.append((widget, text))
        if tip is not None:
            widget.setToolTip(tr(tip))
            self._translatable_tips.append((widget, tip))

    def _sync_header(self) -> None:
        self._table.setHorizontalHeaderLabels([tr(text) for text in HEADER_LABELS])
        header_item = self._table.horizontalHeaderItem(COL_POLL_ENABLED)
        if header_item is not None:  # the checkbox column has no text, only a tip
            header_item.setToolTip(tr(POLL_ENABLED_TIP))

    def _sync_expr_header(self) -> None:
        self._expr_table.setHorizontalHeaderLabels(
            [tr(text) for text in EXPR_HEADER_LABELS]
        )

    def retranslate(self) -> None:
        """Переприменить tr() ко всем строкам панели (по смене языка)."""
        for widget, text in self._translatable:
            widget.setText(tr(text))
            # icon-only buttons: the tooltip doubles as the (hidden) label
            if isinstance(widget, QToolButton):
                widget.setToolTip(tr(text))
                widget.setAccessibleName(tr(text))
        for widget, tip in self._translatable_tips:
            widget.setToolTip(tr(tip))
        self._sync_header()
        self._sync_expr_header()
        self._table.setToolTip(tr(TABLE_TOOLTIP))
        self._expr_table.setToolTip(
            tr(
                "Expressions compute over scaled row values: [name] is a row "
                "reference, functions abs/sqrt/sin/… and pi/e are available"
            )
        )
        self._filter_edit.setPlaceholderText(tr("Filter…"))
        self._csv_import_action.setText(tr("Import table…"))
        self._csv_export_action.setText(tr("Export…"))
        self._log_button.setText(tr("Log to file"))
        self._start_poll_action.setText(tr("Start polling"))
        self._start_record_action.setText(tr("Start polling and record"))
        # state-dependent texts go through their sync paths, not stale snapshots
        self._sync_poll_button(self.is_polling(), self._recording)
        self._sync_logging_ui()

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
                    "poll_enabled": self._poll_enabled_at(index),
                    "format": format_combo.currentText(),
                    "order": settings.order or "",  # "" = inherit the global order
                    "scale": settings.scale,
                    "offset": settings.offset,
                    "unit": settings.unit,
                    "log": settings.log,
                    "alarms": [alarm_rule_to_json(rule) for rule in settings.alarms],
                }
            )
        return rows

    def options_state(self) -> dict:
        header = self._table.horizontalHeader()
        return {
            "order": self._global_order_combo.currentText(),
            # sectionSize/resizeSection take logical indexes, so widths stay
            # bound to columns across drag'n'drop reordering
            "column_widths": [
                header.sectionSize(col) for col in range(header.count())
            ],
            # visual order left to right as logical keys
            "column_order": [
                COLUMN_KEYS[header.logicalIndex(visual)]
                for visual in range(header.count())
            ],
            "hidden_columns": [
                COLUMN_KEYS[col] for col in DATA_COLUMNS if header.isSectionHidden(col)
            ],
            "expressions_visible": self._expr_button.isChecked(),
        }

    def set_options(self, options: dict) -> None:
        if not isinstance(options, dict):
            return
        if options.get("order") in ORDERS:
            self._global_order_combo.setCurrentText(str(options["order"]))
        visible = options.get("expressions_visible")
        if isinstance(visible, bool):
            self._expr_button.setChecked(visible)  # toggled → setVisible
        header = self._table.horizontalHeader()
        order = options.get("column_order")
        if isinstance(order, list):
            cols: list[int] = []  # desired visual order as logical indexes
            for key in order:
                col = KEY_TO_COL.get(key)
                if col is not None and col not in cols:
                    cols.append(col)
            cols.extend(col for col in range(header.count()) if col not in cols)
            for visual, logical in enumerate(cols):
                header.moveSection(header.visualIndex(logical), visual)
        hidden = options.get("hidden_columns")
        if isinstance(hidden, list):
            hide = {
                KEY_TO_COL[key]
                for key in hidden
                if isinstance(key, str) and KEY_TO_COL.get(key) in DATA_COLUMNS
            }
            if not any(col not in hide for col in DATA_COLUMNS):
                hide.discard(COL_NAME)  # at least one data column stays visible
            for col in DATA_COLUMNS:
                header.setSectionHidden(col, col in hide)
        widths = options.get("column_widths")
        if isinstance(widths, list):
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
            # the log flag and alarm rules live outside RegisterRow:
            # apply them to the new token
            self._row_display[self._row_token_counter].log = bool(
                entry.get("log", True)
            )
            self._row_display[self._row_token_counter].alarms = alarm_rules_from_json(
                entry.get("alarms")
            )
            # missing key (older settings files) defaults to polling enabled
            if not bool(entry.get("poll_enabled", True)):
                item = self._table.item(self._table.rowCount() - 1, COL_POLL_ENABLED)
                if item is not None:
                    item.setCheckState(Qt.CheckState.Unchecked)
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
                tr("← scanner: added {added} rows to the table", added=added)
                + (
                    tr(", skipped {skipped} duplicates", skipped=skipped)
                    if skipped
                    else ""
                )
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

        poll_enabled_item = QTableWidgetItem()
        poll_enabled_item.setFlags(
            (poll_enabled_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            & ~Qt.ItemFlag.ItemIsEditable
        )
        poll_enabled_item.setCheckState(Qt.CheckState.Checked)
        poll_enabled_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        poll_enabled_item.setToolTip(tr(POLL_ENABLED_TIP))
        self._table.setItem(index, COL_POLL_ENABLED, poll_enabled_item)

        type_combo = theme.FitComboBox()
        type_combo.addItems(KINDS)
        type_combo.setCurrentText(row.kind)
        type_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._table.setCellWidget(index, COL_TYPE, type_combo)

        self._table.setItem(index, COL_ADDRESS, QTableWidgetItem(str(row.address)))
        self._table.setItem(index, COL_COUNT, QTableWidgetItem(str(row.count)))

        unit_id_item = QTableWidgetItem("" if row.unit_id is None else str(row.unit_id))
        unit_id_item.setToolTip(
            tr("Modbus unit 1..247, empty = unit from the connection panel")
        )
        self._table.setItem(index, COL_UNIT_ID, unit_id_item)

        poll_item = QTableWidgetItem("" if row.poll_ms is None else str(row.poll_ms))
        poll_item.setToolTip(
            tr(
                "Per-row poll interval in ms, empty = global interval; "
                "a row with its own interval is polled by a dedicated timer"
            )
        )
        self._table.setItem(index, COL_POLL, poll_item)

        format_combo = theme.FitComboBox()
        format_combo.addItems(FORMATS)
        format_combo.setCurrentText(row.format)
        format_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        format_combo.setToolTip(
            tr("Display format (registers only; coils/discrete show 0/1)")
        )
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
        delete_button.setIcon(icons.icon("close_tab"))
        delete_button.setIconSize(QSize(icons.ICON_SIZE, icons.ICON_SIZE))
        icons.register(delete_button, "close_tab")
        delete_button.setFixedSize(26, 26)
        delete_button.setToolTip(tr("Delete row"))
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
        # the event corrupts the view — move after the event returns;
        # parented timer, not QTimer.singleShot(lambda) (see _flash_value_cell)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._move_selected_rows(delta))
        timer.start(0)

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
            self, tr("Import registers from CSV"), str(Path.home()), "CSV (*.csv)"
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            header = csv_header(path.read_text(encoding="utf-8-sig"))
        except OSError as exc:
            self.logLine.emit(tr("✗ failed to read {path}: {exc}", path=path, exc=exc))
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
            self.logLine.emit(tr("✗ export: no columns selected"))
            return
        path_str, _ = QFileDialog.getSaveFileName(
            self, tr("Export registers to CSV"), str(Path.home() / "registers.csv"),
            "CSV (*.csv)",
        )
        if path_str:
            self.export_csv(Path(path_str), columns)

    def import_csv(self, path: Path, mapping: dict[str, str] | None = None) -> None:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            self.logLine.emit(tr("✗ failed to read {path}: {exc}", path=path, exc=exc))
            return
        try:
            parsed = rows_from_csv(text, mapping)
        except ValueError as exc:
            self.logLine.emit(
                tr("✗ failed to import {path}: {exc}", path=path, exc=exc)
            )
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
        self.logLine.emit(
            tr("← imported {count} rows from {path}", count=len(parsed), path=path)
        )

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
            self.logLine.emit(tr("✗ failed to write {path}: {exc}", path=path, exc=exc))
            return
        self.logLine.emit(
            tr("→ exported {count} rows to {path}", count=count, path=path)
        )

    @Slot()
    def _on_display_settings(self) -> None:
        if self._display_dialog is not None:  # already open
            self._display_dialog.raise_()
            self._display_dialog.activateWindow()
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("Per-row display settings"))
        table = QTableWidget(0, 6)
        table.setHorizontalHeaderLabels(
            [tr(h) for h in ("Name", "Address", "Scale", "Offset", "Unit", "Order")]
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
            # anything outside ORDERS means "inherit" — display text is free
            order_combo.addItem(tr("default"), "")
            order_combo.addItems(ORDERS)
            order_combo.setCurrentText(
                settings.order if settings.order else tr("default")
            )
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
        layout.addWidget(QLabel(tr("Rows added or deleted while this dialog is open"
                                   " appear after reopening it.")))
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

    # --- alarms (conditional row highlight) -----------------------------------

    @Slot()
    def _on_alarms(self) -> None:
        dialog = AlarmsDialog(self._alarm_row_entries(), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        rules_by_token = dialog.rules()
        if rules_by_token is None:  # the dialog validates before accepting
            return
        expr_tokens = set(self.expr_tokens())
        for token, rules in rules_by_token.items():
            if token in expr_tokens:
                self._expr_alarms[token] = rules
            else:
                self._row_display.setdefault(token, RowDisplaySettings()).alarms = rules
        # re-evaluate against the last reads: _update_alarm resolves the
        # transitions (cleared highlight, new color) from the kept edge state
        for index in range(self._table.rowCount()):
            self._re_evaluate_alarm(index)
        for index in range(self._expr_table.rowCount()):
            self._re_evaluate_expr_alarm(index)

    def _alarm_row_entries(self) -> list[tuple[int, str, list[AlarmRule]]]:
        """(token, label, rules) per table row and expression for the dialog."""
        entries = []
        for index in range(self._table.rowCount()):
            token = self._token_at(index)
            entries.append(
                (
                    token,
                    self.row_label(token),
                    list(self._row_display.get(token, RowDisplaySettings()).alarms),
                )
            )
        for token in self.expr_tokens():
            entries.append(
                (
                    token,
                    self._expr_alarm_label(token),
                    list(self._expr_alarms.get(token, [])),
                )
            )
        return entries

    def _expr_alarm_label(self, token: int) -> str:
        """Подпись выражения для диалога алармов и лога: «fx имя»."""
        return f"fx {self.expr_label(token)}"

    def _re_evaluate_alarm(self, index: int) -> None:
        """Переоценить аларм строки по последнему прочитанному значению."""
        token = self._token_at(index)
        values = self._last_values.get(token)
        if values is None:  # never read: nothing to match, clear any highlight
            self._active_alarms.pop(token, None)
            item = self._table.item(index, COL_VALUE)
            if item is not None:
                item.setBackground(QBrush())
            return
        # silent: правки в диалоге обновляют edge-состояние без лога/звука
        self._update_alarm(index, values, silent=True)

    def _update_alarm(self, index: int, values: list, *, silent: bool = False) -> None:
        """Оценить правила строки по свежему чтению: подсветка, edge-лог, звук."""
        token = self._token_at(index)
        rules = self._row_display.get(token, RowDisplaySettings()).alarms
        previous = self._active_alarms.get(token)
        primary = self._primary_value(index, values) if rules else None
        # hex/ascii/non-numeric rows never match
        matched = evaluate_alarm(primary, rules) if primary is not None else None
        if matched is None and previous is None:
            return
        item = self._table.item(index, COL_VALUE)
        if matched is None:  # active -> cleared edge
            cleared = self._active_alarms.pop(token)
            if item is not None:
                item.setBackground(QBrush())
            if cleared.log:
                self.logLine.emit(
                    tr("ALARM cleared {label}", label=self.row_label(token))
                )
            return
        self._active_alarms[token] = matched
        if item is not None:
            item.setBackground(theme.alarm_color(matched.color))
        if previous != matched and not silent:  # новый фронт: None->rule или смена правила
            if matched.log:
                self.logLine.emit(
                    tr(
                        "ALARM {label}: {value} {condition}",
                        label=self.row_label(token),
                        value=f"{primary:g}",
                        condition=_describe_rule(matched),
                    )
                )
            if matched.sound:
                self._alarm_sound.play()

    def _re_evaluate_expr_alarm(self, index: int) -> None:
        """Переоценить аларм выражения по последнему вычисленному значению."""
        token = self._expr_token_at(index)
        # silent: правки в диалоге обновляют edge-состояние без лога/звука
        self._update_expr_alarm(index, self._expr_last.get(token), silent=True)

    def _update_expr_alarm(
        self, index: int, result: float | None, *, silent: bool = False
    ) -> None:
        """Оценить правила выражения: подсветка Value, edge-лог, звук.
        result=None («—»/«⚠» — числа нет) никогда не матчит и снимает
        активный аларм по обычной семантике снятия."""
        token = self._expr_token_at(index)
        rules = self._expr_alarms.get(token, [])
        previous = self._active_alarms.get(token)
        matched = evaluate_alarm(result, rules) if result is not None and rules else None
        if matched is None and previous is None:
            return
        item = self._expr_table.item(index, EXPR_COL_VALUE)
        if matched is None:  # active -> cleared edge
            cleared = self._active_alarms.pop(token)
            if item is not None:
                item.setBackground(QBrush())
            if cleared.log:
                self.logLine.emit(
                    tr("ALARM cleared {label}", label=self._expr_alarm_label(token))
                )
            return
        self._active_alarms[token] = matched
        if item is not None:
            item.setBackground(theme.alarm_color(matched.color))
        if previous != matched and not silent:  # новый фронт: None->rule или смена правила
            if matched.log:
                self.logLine.emit(
                    tr(
                        "ALARM {label}: {value} {condition}",
                        label=self._expr_alarm_label(token),
                        value=f"{result:g}",
                        condition=_describe_rule(matched),
                    )
                )
            if matched.sound:
                self._alarm_sound.play()

    # --- snapshot diff (сравнение «до/после» по raw-значениям) --------------

    def take_snapshot(self) -> None:
        """Запомнить текущие raw-значения всех строк; повторный вызов
        перезаписывает снапшот. Локальное действие — шина не нужна."""
        self._snapshot = {}
        for index in range(self._table.rowCount()):
            token = self._token_at(index)
            values = self._last_values.get(token)
            self._snapshot[token] = _SnapshotEntry(
                name=self._text_at(index, COL_NAME),
                kind=self._table.cellWidget(index, COL_TYPE).currentText(),
                address=self._text_at(index, COL_ADDRESS),
                values=list(values) if values is not None else None,
            )
        self._snapshot_at = datetime.now().strftime("%H:%M:%S")
        self._diff_button.setEnabled(True)
        self.logLine.emit(
            tr("Snapshot taken: {count} rows", count=len(self._snapshot))
        )

    def snapshot_diff_data(self) -> tuple[str, list[DiffRow]]:
        """(подпись, строки) для окна diff: снапшот против текущих _last_values.
        Значения форматируются текущим форматом строки; строки, удалённые
        после снапшота, идут в конец с пометкой "(removed)"."""
        if self._snapshot is None:
            return "", []
        rows: list[DiffRow] = []
        seen: set[int] = set()
        for index in range(self._table.rowCount()):
            token = self._token_at(index)
            seen.add(token)
            entry = self._snapshot.get(token)  # None — строка добавлена позже
            old = entry.values if entry is not None else None
            new = self._last_values.get(token)
            rows.append(
                DiffRow(
                    name=self._text_at(index, COL_NAME),
                    kind=self._table.cellWidget(index, COL_TYPE).currentText(),
                    address=self._text_at(index, COL_ADDRESS),
                    snapshot_text=(
                        self._display_text(index, old) if old is not None else ""
                    ),
                    current_text=(
                        self._display_text(index, new) if new is not None else ""
                    ),
                    changed=diff_snapshots(old, new),
                )
            )
        for token, entry in self._snapshot.items():
            if token in seen:
                continue
            rows.append(  # строка удалена после снапшота: raw как есть
                DiffRow(
                    name=entry.name,
                    kind=entry.kind,
                    address=entry.address,
                    snapshot_text=(
                        format_values(entry.values) if entry.values is not None else ""
                    ),
                    current_text=tr("(removed)"),
                    changed=True,
                    removed=True,
                )
            )
        return tr("Snapshot taken at {time}", time=self._snapshot_at), rows

    @Slot()
    def _on_diff(self) -> None:
        if self._diff_dialog is not None:  # окно одно: поднять и обновить
            self._diff_dialog.refresh()
            self._diff_dialog.raise_()
            self._diff_dialog.activateWindow()
            return
        dialog = SnapshotDiffDialog(self.snapshot_diff_data, self.take_snapshot, self)
        dialog.finished.connect(self._on_diff_dialog_closed)
        self._diff_dialog = dialog
        dialog.show()

    def _on_diff_dialog_closed(self, _result: int) -> None:
        self._diff_dialog = None

    @Slot()
    def _on_mask_write(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("Mask write (function 0x16)"))
        unit_edit = QLineEdit()
        unit_edit.setPlaceholderText(tr("empty = global unit"))
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
                tr(
                    "Set/clear bits of one holding register:\n"
                    "result = (value AND and-mask) OR (or-mask AND NOT and-mask).\n"
                    "Masks accept decimal or hex (e.g. 0xFF0F)."
                )
            )
        )
        form.addRow(tr("Unit:"), unit_edit)
        form.addRow(tr("Address:"), address_edit)
        form.addRow(tr("AND mask:"), and_edit)
        form.addRow(tr("OR mask:"), or_edit)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            address = int(address_edit.text().strip(), 0)
            and_mask = int(and_edit.text().strip(), 0)
            or_mask = int(or_edit.text().strip(), 0)
        except ValueError:
            self.logLine.emit(
                tr("✗ mask write: invalid address/mask (dec or 0x… hex)")
            )
            return
        if not 0 <= address <= 0xFFFF or not 0 <= and_mask <= 0xFFFF or not (
            0 <= or_mask <= 0xFFFF
        ):
            self.logLine.emit(tr("✗ mask write: address/mask out of range 0..0xFFFF"))
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
        dialog.setWindowTitle(tr("Read/Write multiple registers (function 0x17)"))
        unit_edit = QLineEdit()
        unit_edit.setPlaceholderText(tr("empty = global unit"))
        write_address_edit = QLineEdit()
        values_edit = QLineEdit()
        values_edit.setPlaceholderText(tr("comma/space separated, hex ok"))
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
                tr(
                    "One atomic exchange: write Values at Write address, then read\n"
                    "Read count registers from Read address; read values go to the log.\n"
                    "Addresses accept decimal or hex (e.g. 0x10)."
                )
            )
        )
        form.addRow(tr("Unit:"), unit_edit)
        form.addRow(tr("Write address:"), write_address_edit)
        form.addRow(tr("Values:"), values_edit)
        form.addRow(tr("Read address:"), read_address_edit)
        form.addRow(tr("Read count:"), read_count_edit)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            write_address = int(write_address_edit.text().strip(), 0)
            read_address = int(read_address_edit.text().strip(), 0)
        except ValueError:
            self.logLine.emit(
                tr("✗ read/write: invalid address (dec or 0x… hex)")
            )
            return
        if not 0 <= write_address <= 0xFFFF or not 0 <= read_address <= 0xFFFF:
            self.logLine.emit(tr("✗ read/write: address out of range 0..0xFFFF"))
            return
        try:
            values = parse_values("holding_registers", values_edit.text())
        except ValueError as exc:
            self.logLine.emit(tr("✗ parse error: {exc}", exc=exc))
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
        self.logLine.emit(
            tr("← read/write read values: {values}", values=format_values(values))
        )
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

    def row_poll_enabled(self, token: int) -> bool:
        index = self._find_row_by_token(token)
        return index is not None and self._poll_enabled_at(index)

    def series(self, token: int) -> TimeSeries | None:
        return self._series.get(token)

    def clear_series(self) -> None:
        """Сбросить историю значений и регистров, и выражений (Clear графика)."""
        self._clear_register_series()
        for series in self._expr_series.values():
            series.clear()
        for sparkline in self._expr_sparklines.values():
            sparkline.refresh()

    def _clear_register_series(self) -> None:
        """Сбросить историю только строк регистров (контекстное меню таблицы)."""
        for series in self._series.values():
            series.clear()
        for sparkline in self._sparklines.values():
            sparkline.refresh()

    # --- expressions (вычисляемые строки над значениями регистров) ----------

    @Slot(bool)
    def _on_expressions_toggled(self, on: bool) -> None:
        self._expr_widget.setVisible(on)

    def expressions_state(self) -> list[dict]:
        return [
            {
                "name": self._expr_text_at(index, EXPR_COL_NAME),
                "expr": self._expr_text_at(index, EXPR_COL_EXPR),
                "alarms": [
                    alarm_rule_to_json(rule)
                    for rule in self._expr_alarms.get(self._expr_token_at(index), [])
                ],
            }
            for index in range(self._expr_table.rowCount())
        ]

    def set_expressions_state(self, entries: list) -> None:
        """Загрузить выражения из session state; толерантный разбор, невалидные
        выражения показываются как «⚠», а не отбрасываются."""
        while self._expr_table.rowCount():
            token = self._expr_token_at(0)
            self._expr_parsed.pop(token, None)
            self._expr_series.pop(token, None)
            self._expr_sparklines.pop(token, None)
            self._expr_alarms.pop(token, None)
            self._expr_last.pop(token, None)
            self._active_alarms.pop(token, None)
            self._expr_table.removeRow(0)
        if not isinstance(entries, list):
            return
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "")
            expr_text = str(entry.get("expr") or "")
            if not name and not expr_text:
                continue
            token = self._add_expression(name, expr_text)
            self._expr_alarms[token] = alarm_rules_from_json(entry.get("alarms"))

    def expr_tokens(self) -> list[int]:
        return [self._expr_token_at(i) for i in range(self._expr_table.rowCount())]

    def expr_label(self, token: int) -> str:
        index = self._find_expr_row(token)
        if index is None:
            return "?"
        return (
            self._expr_text_at(index, EXPR_COL_NAME)
            or self._expr_text_at(index, EXPR_COL_EXPR)
            or "?"
        )

    def expr_series(self, token: int) -> TimeSeries | None:
        return self._expr_series.get(token)

    def _expr_token_at(self, index: int) -> int:
        item = self._expr_table.item(index, EXPR_COL_NAME)
        return int(item.data(Qt.ItemDataRole.UserRole)) if item else -1

    def _find_expr_row(self, token: int) -> int | None:
        for index in range(self._expr_table.rowCount()):
            if self._expr_token_at(index) == token:
                return index
        return None

    def _expr_text_at(self, index: int, col: int) -> str:
        item = self._expr_table.item(index, col)
        return item.text().strip() if item else ""

    @Slot()
    def _on_add_expression(self) -> None:
        self._add_expression()
        item = self._expr_table.item(self._expr_table.rowCount() - 1, EXPR_COL_NAME)
        if item is not None:  # новая строка сразу в редактировании имени
            self._expr_table.setCurrentItem(item)
            self._expr_table.editItem(item)

    def _add_expression(self, name: str = "", expr_text: str = "") -> int:
        """Добавить строку выражения; возвращает её токен."""
        index = self._expr_table.rowCount()
        self._expr_table.blockSignals(True)
        self._expr_table.insertRow(index)
        # общий со строками регистров счётчик: токены уникальны в обеих
        # таблицах, окну графика не нужно различать источники
        self._row_token_counter += 1
        token = self._row_token_counter

        name_item = QTableWidgetItem(name)
        name_item.setData(Qt.ItemDataRole.UserRole, token)
        self._expr_table.setItem(index, EXPR_COL_NAME, name_item)
        self._expr_table.setItem(index, EXPR_COL_EXPR, QTableWidgetItem(expr_text))

        value_item = QTableWidgetItem("")
        value_item.setFlags(value_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._expr_table.setItem(index, EXPR_COL_VALUE, value_item)

        series = TimeSeries()
        sparkline = SparklineWidget(series)
        self._expr_series[token] = series
        self._expr_sparklines[token] = sparkline
        self._expr_table.setCellWidget(index, EXPR_COL_TREND, sparkline)

        delete_button = QToolButton()
        delete_button.setIcon(icons.icon("close_tab"))
        delete_button.setIconSize(QSize(icons.ICON_SIZE, icons.ICON_SIZE))
        icons.register(delete_button, "close_tab")
        delete_button.setFixedSize(26, 26)
        delete_button.setToolTip(tr("Delete expression"))
        delete_button.clicked.connect(self._on_expr_delete_clicked)
        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(2, 2, 2, 2)
        actions_layout.addWidget(delete_button)
        self._expr_table.setCellWidget(index, EXPR_COL_ACTIONS, actions)
        self._expr_table.blockSignals(False)

        if expr_text:  # текст из state: разобрать (невалидное → «⚠»)
            self._parse_expression_row(index)
        self.rowsChanged.emit()  # окно графика перечитывает чек-лист
        return token

    def _on_expr_delete_clicked(self) -> None:
        button = self.sender()
        if button is None:
            return
        actions = button.parentWidget()
        for index in range(self._expr_table.rowCount()):
            if self._expr_table.cellWidget(index, EXPR_COL_ACTIONS) is actions:
                token = self._expr_token_at(index)
                self._expr_parsed.pop(token, None)
                self._expr_series.pop(token, None)
                self._expr_sparklines.pop(token, None)
                self._expr_alarms.pop(token, None)
                self._expr_last.pop(token, None)
                self._active_alarms.pop(token, None)
                self._expr_table.removeRow(index)
                self.rowsChanged.emit()
                return

    @Slot(QTableWidgetItem)
    def _on_expr_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == EXPR_COL_EXPR:
            self._parse_expression_row(item.row())  # commit: разбор + пересчёт
        elif item.column() == EXPR_COL_NAME:
            self.rowsChanged.emit()  # подпись в чек-листе графика

    def _parse_expression_row(self, index: int) -> None:
        """Разобрать текст ячейки Expression; невалидное — «⚠» + тултип
        с ошибкой, предыдущее валидное выражение сбрасывается."""
        token = self._expr_token_at(index)
        value_item = self._expr_table.item(index, EXPR_COL_VALUE)
        text = self._expr_text_at(index, EXPR_COL_EXPR)
        if not text:
            self._expr_parsed[token] = None
            self._expr_last[token] = None
            self._update_expr_alarm(index, None)  # нет числа: аларм снимается
            if value_item is not None:
                value_item.setText("")
                value_item.setToolTip("")
                value_item.setBackground(QBrush())
            return
        try:
            self._expr_parsed[token] = parse_expression(text)
        except ValueError as exc:
            self._expr_parsed[token] = None
            self._expr_last[token] = None
            self._update_expr_alarm(index, None)  # ⚠ не алармит, активный снимается
            if value_item is not None:
                value_item.setText("⚠")
                value_item.setToolTip(str(exc))
                value_item.setBackground(theme.alarm_color("red"))
            return
        if value_item is not None:
            value_item.setToolTip("")
            value_item.setBackground(QBrush())
        self._recalc_expression(index)

    def _expression_row_names(self) -> list[str]:
        """Имена строк регистров для автодополнения ссылок [name]."""
        names = []
        for index in range(self._table.rowCount()):
            name = self._text_at(index, COL_NAME)
            if name:
                names.append(name)
        return names

    def _row_values_by_name(self) -> dict[str, float]:
        """Имя строки → масштабированное primary-значение (для ссылок [name])."""
        values: dict[str, float] = {}
        for index in range(self._table.rowCount()):
            name = self._text_at(index, COL_NAME)
            if not name:
                continue
            row_values = self._last_values.get(self._token_at(index))
            if row_values is None:
                continue
            primary = self._primary_value(index, row_values)
            if primary is not None:
                values[name] = primary
        return values

    def _recalc_expressions(self) -> None:
        # выражений мало: пересчитываем все при любом чтении/переименовании —
        # dep мог появиться или исчезнуть вместе с именем строки
        for index in range(self._expr_table.rowCount()):
            self._recalc_expression(index)

    def _recalc_expression(self, index: int) -> None:
        token = self._expr_token_at(index)
        expr = self._expr_parsed.get(token)
        value_item = self._expr_table.item(index, EXPR_COL_VALUE)
        if expr is None or value_item is None:
            return  # невалидное выражение: ячейка уже показывает «⚠»
        try:
            result = expr.evaluate(self._row_values_by_name())
        except KeyError:
            result = float("nan")  # строка-зависимость отсутствует/не читалась
        if math.isnan(result):
            self._expr_last[token] = None
            value_item.setText("—")
            self._update_expr_alarm(index, None)  # «—» не алармит
            return
        self._expr_last[token] = result
        value_item.setText(f"{result:g}")  # ~6 значащих цифр, как у primary
        self._update_expr_alarm(index, result)
        if self._recording:  # история — только в режиме poll+record
            self._expr_series[token].append(time.monotonic(), result)
            self._expr_sparklines[token].refresh()

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
            action = menu.addAction(tr(text), slot)
            action.setShortcut(QKeySequence(key))  # shown next to the item
        if self._table.columnAt(pos.x()) == COL_TREND:
            menu.addSeparator()
            # история выражений сбрасывается только глобальным Clear графика
            menu.addAction(tr("Clear history"), self._clear_register_series)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _on_header_context_menu(self, pos: QPoint) -> None:
        header = self._table.horizontalHeader()
        self._build_columns_menu().exec(header.mapToGlobal(pos))

    def _build_columns_menu(self) -> QMenu:
        """Чек-лист видимых колонок (правый клик по заголовку таблицы).
        Контрольные колонки (галочка поллинга, кнопка удаления) в меню не
        показываются; последнюю видимую колонку данных скрыть нельзя."""
        header = self._table.horizontalHeader()
        visible_data = [col for col in DATA_COLUMNS if not header.isSectionHidden(col)]
        menu = QMenu(self)
        for col in DATA_COLUMNS:
            action = menu.addAction(tr(HEADER_LABELS[col]))
            action.setCheckable(True)
            action.setChecked(not header.isSectionHidden(col))
            if action.isChecked() and len(visible_data) == 1:
                action.setEnabled(False)  # the last visible data column
            else:
                action.triggered.connect(
                    lambda checked, c=col: header.setSectionHidden(c, not checked)
                )
        return menu

    def _text_at(self, index: int, col: int) -> str:
        item = self._table.item(index, col)
        return item.text().strip() if item else ""

    def _poll_enabled_at(self, index: int) -> bool:
        item = self._table.item(index, COL_POLL_ENABLED)
        return item is None or item.checkState() == Qt.CheckState.Checked

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
            self.logLine.emit(
                tr("✗ row {row}: invalid address/count", row=index + 1)
            )
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
            self._active_alarms.pop(token, None)
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
        elif item.column() in (COL_POLL, COL_POLL_ENABLED):
            self._sync_row_timer(item.row())
            if item.column() == COL_POLL_ENABLED:
                self.rowsChanged.emit()  # the graph window hides/shows the row
        if item.column() == COL_NAME:
            # имя — ключ ссылок [name]: dep мог появиться или исчезнуть
            self._recalc_expressions()

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
            self.logLine.emit(tr("✗ parse error: {exc}", exc=exc))
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
            self.logLine.emit(
                tr(
                    "✗ row {row}: {kind} is a read-only area",
                    row=index + 1,
                    kind=row.kind,
                )
            )
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
            self.logLine.emit(tr("✗ row {row}: read the row before +/-", row=index + 1))
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
            self.logLine.emit(
                tr("✗ row {row}: read the row before toggling", row=index + 1)
            )
            return
        if row.kind == "coils":
            self._emit_write(index, row, [not last[0]])
        else:
            self._emit_write(index, row, [1 if int(last[0]) == 0 else 0])

    @Slot()
    def read_all(self) -> None:
        for index in range(self._table.rowCount()):
            if self._poll_enabled_at(index):  # unchecked rows are opted out
                self._read_table_row(index)

    @Slot()
    def _poll_global_rows(self) -> None:
        # rows with a per-row interval have their own timer in _row_timers
        for index in range(self._table.rowCount()):
            if not self._poll_enabled_at(index):
                continue
            if _parse_poll_ms(self._text_at(index, COL_POLL)) is None:
                self._read_table_row(index)

    def _sync_row_timer(self, index: int) -> None:
        token = self._token_at(index)
        poll_ms = _parse_poll_ms(self._text_at(index, COL_POLL))
        timer = self._row_timers.get(token)
        if poll_ms is None or not self._poll_enabled_at(index) or (
            not self._poll_timer.isActive()
        ):
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
        if ok:
            self._update_alarm(index, values)
            self._recalc_expressions()

    def _flash_value_cell(self, token: int, item: QTableWidgetItem) -> None:
        if token in self._active_alarms:
            return  # an active alarm keeps its color: it outranks the change flash
        item.setBackground(theme.flash_color())
        generation = self._flash_generations.get(token, 0) + 1
        self._flash_generations[token] = generation
        # parented QTimer instead of QTimer.singleShot(lambda): the static
        # singleShot-with-callable path crashes intermittently on Windows
        # (access violation in shiboken wrapping); an owned timer also dies
        # with the panel instead of firing on a deleted widget.
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._clear_flash(token, generation))
        timer.start(2000)

    def _clear_flash(self, token: int, generation: int) -> None:
        if self._flash_generations.get(token) != generation:
            return  # superseded by a newer flash, its timer will do the clearing
        self._flash_generations.pop(token, None)
        index = self._find_row_by_token(token)  # the row may have been deleted or moved
        if index is None:
            return
        item = self._table.item(index, COL_VALUE)
        if item is not None:
            # an alarm raised after the flash was scheduled keeps its color
            active = self._active_alarms.get(token)
            item.setBackground(
                theme.alarm_color(active.color) if active is not None else QBrush()
            )

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
            text, icon_name = tr("Stop polling"), "poll_stop"
        else:
            text = (
                tr("Start polling and record") if self._record_mode
                else tr("Start polling")
            )
            icon_name = "poll_start"
        self._poll_button.setIcon(icons.icon(icon_name))
        icons.register(self._poll_button, icon_name)  # theme refresh follows the state
        self._poll_button.setText(text)
        self._poll_button.setToolTip(f"{text}\n{tr(POLL_BUTTON_TIP)}")

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
            self.logLine.emit(
                tr(
                    "✗ logging: cannot open {path}: {exc}",
                    path=self._log_settings.path,
                    exc=exc,
                )
            )
            self._sync_logging_ui()
            return
        self._log_flush_timer.start()
        if not self.is_polling():  # logging needs reads: start polling
            self.start_polling(self._record_mode)  # records per the split mode
        self.logLine.emit(
            tr(
                "→ logging values to {path} ({format})",
                path=self._log_settings.path,
                format=self._log_settings.format,
            )
        )
        self._sync_logging_ui()

    def stop_logging(self) -> None:
        if not self._logger.is_open:
            return
        self._log_flush_timer.stop()
        rows, path = self._logger.rows_written, self._log_settings.path
        self._logger.close()
        self.logLine.emit(
            tr("← logging stopped: {rows} rows written to {path}",
               rows=rows, path=path)
        )
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
            tr(
                "Logging to {path} — click to stop", path=self._log_settings.path
            )
            if is_open
            else tr("Log read values to a file (CSV or JSON Lines)")
        )
        self._log_settings_button.setEnabled(not is_open)
