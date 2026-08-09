from collections.abc import Callable
from typing import get_args

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from modbus_connector.models import (
    DisplayFormat,
    RegisterKind,
    RegisterRow,
    format_register_values,
    format_values,
    parse_values,
)

KINDS = list(get_args(RegisterKind))
FORMATS = list(get_args(DisplayFormat))
REGISTER_KINDS = ("holding_registers", "input_registers")

COL_NAME, COL_TYPE, COL_ADDRESS, COL_COUNT, COL_FORMAT, COL_VALUE, COL_NEW_VALUE, COL_ACTIONS = (
    range(8)
)


class RegistersPanel(QWidget):
    readRequested = Signal(int, int, object)
    writeRequested = Signal(int, int, object, list)
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

        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels(
            ["Name", "Type", "Address", "Count", "Format", "Value", "New value", ""]
        )
        self._table.horizontalHeader().setSectionResizeMode(
            COL_NAME, QHeaderView.ResizeMode.Stretch
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setToolTip("Enter in 'New value' = write, Ctrl/Cmd+R = read current row")
        self._table.itemChanged.connect(self._on_item_changed)

        read_shortcut = QShortcut(QKeySequence("Ctrl+R"), self)
        read_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        read_shortcut.activated.connect(self._read_current_row)

        add_button = QPushButton("Add register")
        add_button.clicked.connect(lambda: self._add_row())
        read_all_button = QPushButton("Read all")
        read_all_button.clicked.connect(self.read_all)

        self._poll_interval = QSpinBox(minimum=100, maximum=600_000, value=1000)
        self._poll_interval.setSuffix(" ms")
        self._poll_button = QPushButton("Start polling")
        self._poll_button.clicked.connect(self._toggle_polling)
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self.read_all)

        top = QHBoxLayout()
        top.addWidget(add_button)
        top.addWidget(read_all_button)
        top.addStretch(1)
        top.addWidget(QLabel("Interval:"))
        top.addWidget(self._poll_interval)
        top.addWidget(self._poll_button)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self._table)

        self._add_row()

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
                    "format": format_combo.currentText(),
                }
            )
        return rows

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
                )
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            self._add_row(row)
        if self._table.rowCount() == 0:
            self._add_row()

    def _add_row(self, row: RegisterRow | None = None) -> None:
        row = row or RegisterRow(name="", kind="holding_registers", address=0, count=1)
        index = self._table.rowCount()
        self._table.blockSignals(True)
        self._table.insertRow(index)
        self._row_token_counter += 1

        name_item = QTableWidgetItem(row.name)
        name_item.setData(Qt.ItemDataRole.UserRole, self._row_token_counter)
        self._table.setItem(index, COL_NAME, name_item)

        type_combo = QComboBox()
        type_combo.addItems(KINDS)
        type_combo.setCurrentText(row.kind)
        type_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._table.setCellWidget(index, COL_TYPE, type_combo)

        self._table.setItem(index, COL_ADDRESS, QTableWidgetItem(str(row.address)))
        self._table.setItem(index, COL_COUNT, QTableWidgetItem(str(row.count)))

        format_combo = QComboBox()
        format_combo.addItems(FORMATS)
        format_combo.setCurrentText(row.format)
        format_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        format_combo.setToolTip("Display format (registers only; coils/discrete show 0/1)")
        self._table.setCellWidget(index, COL_FORMAT, format_combo)

        value_item = QTableWidgetItem("")
        value_item.setFlags(value_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._table.setItem(index, COL_VALUE, value_item)
        self._table.setItem(index, COL_NEW_VALUE, QTableWidgetItem(""))

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

        for col, widget in (
            (COL_TYPE, type_combo),
            (COL_FORMAT, format_combo),
            (COL_ACTIONS, actions),
        ):
            width = widget.sizeHint().width() + 8
            if self._table.columnWidth(col) < width:
                self._table.setColumnWidth(col, width)

    def _token_at(self, index: int) -> int:
        item = self._table.item(index, COL_NAME)
        return int(item.data(Qt.ItemDataRole.UserRole)) if item else -1

    def _find_row_by_token(self, token: int) -> int | None:
        for index in range(self._table.rowCount()):
            if self._token_at(index) == token:
                return index
        return None

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
        return RegisterRow(
            name=name_item.text() if name_item else "",
            kind=type_combo.currentText(),
            address=address,
            count=count,
            format=self._table.cellWidget(index, COL_FORMAT).currentText(),
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
            self._table.removeRow(index)

    @Slot(QTableWidgetItem)
    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == COL_NEW_VALUE and item.text().strip():
            self._write_table_row(item.row())

    @Slot()
    def _read_current_row(self) -> None:
        index = self._table.currentRow()
        if index >= 0:
            self._read_table_row(index)

    def _read_table_row(self, index: int) -> None:
        row = self._row_data(index)
        if row is None:
            return
        token = self._token_at(index)
        if token in self._pending_reads.values():
            return  # previous read still unanswered, don't pile up the worker queue
        request_id = self._next_request_id()
        self._pending_reads[request_id] = token
        self.readRequested.emit(request_id, self._unit_id, row)

    def _write_table_row(self, index: int) -> None:
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
        request_id = self._next_request_id()
        self._pending_writes[request_id] = self._token_at(index)
        self.writeRequested.emit(request_id, self._unit_id, row, values)
        if new_value_item is not None:
            # clear so re-entering the same value fires itemChanged again;
            # the resulting empty-text itemChanged is ignored by _on_item_changed
            new_value_item.setText("")

    @Slot()
    def read_all(self) -> None:
        for index in range(self._table.rowCount()):
            self._read_table_row(index)

    def _display_text(self, index: int, values: list) -> str:
        kind = self._table.cellWidget(index, COL_TYPE).currentText()
        if kind not in REGISTER_KINDS:
            return format_values(values)
        fmt = self._table.cellWidget(index, COL_FORMAT).currentText()
        return format_register_values(values, fmt)

    @Slot(int, bool, list, str)
    def handle_read_finished(self, request_id: int, ok: bool, values: list, error: str) -> None:
        token = self._pending_reads.pop(request_id, None)
        if token is None:
            return
        index = self._find_row_by_token(token)
        if index is None:
            return
        item = self._table.item(index, COL_VALUE)
        if item is not None:
            item.setText(self._display_text(index, values) if ok else f"✗ {error}")

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
            self._poll_timer.start(self._poll_interval.value())
            self._poll_button.setText("Stop polling")

    @Slot()
    def stop_polling(self) -> None:
        self._poll_timer.stop()
        self._pending_reads.clear()  # late responses from already-queued reads are ignored
        self._poll_button.setText("Start polling")

    def is_polling(self) -> bool:
        return self._poll_timer.isActive()
