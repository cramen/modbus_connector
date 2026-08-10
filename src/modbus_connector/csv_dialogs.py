from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from modbus_connector.models import CSV_COLUMNS, guess_column_mapping

EXPORTABLE_COLUMNS = [*CSV_COLUMNS, "value"]
ESSENTIAL_FIELDS = ("name", "kind", "address")
SKIP = "— skip —"


class ExportColumnsDialog(QDialog):
    """Выбор колонок и их порядка для экспорта таблицы регистров в CSV."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export CSV")
        self.setMinimumSize(380, 360)

        self._list = QListWidget()
        for column in EXPORTABLE_COLUMNS:
            item = QListWidgetItem(column)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self._list.addItem(item)
        self._list.setCurrentRow(0)
        self._list.installEventFilter(self)  # keys work before the view eats them

        select_all = QPushButton("Select all")
        select_all.clicked.connect(lambda: self._set_all(Qt.CheckState.Checked))
        select_none = QPushButton("Select none")
        select_none.clicked.connect(lambda: self._set_all(Qt.CheckState.Unchecked))
        move_up = QPushButton("Move up")
        move_up.clicked.connect(lambda: self._move_current(-1))
        move_down = QPushButton("Move down")
        move_down.clicked.connect(lambda: self._move_current(1))

        side = QVBoxLayout()
        side.addWidget(select_all)
        side.addWidget(select_none)
        side.addWidget(move_up)
        side.addWidget(move_down)
        side.addStretch(1)

        center = QHBoxLayout()
        center.addWidget(self._list, 1)
        center.addLayout(side)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Choose columns to export and their order"))
        layout.addLayout(center)
        layout.addWidget(buttons)
        self._list.setFocus()

    def eventFilter(self, obj: QObject | None, event: QEvent | None) -> bool:
        # Space toggles natively; Ctrl+Up/Down reorders; Enter accepts
        if (
            obj is self._list
            and isinstance(event, QKeyEvent)
            and event.type() == QEvent.Type.KeyPress  # not ShortcutOverride/KeyRelease
        ):
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.accept()
                return True
            if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
                if event.key() == Qt.Key.Key_Up:
                    self._move_current(-1)
                    return True
                if event.key() == Qt.Key.Key_Down:
                    self._move_current(1)
                    return True
        return super().eventFilter(obj, event)

    def _set_all(self, state: Qt.CheckState) -> None:
        for row in range(self._list.count()):
            self._list.item(row).setCheckState(state)

    def _move_current(self, delta: int) -> None:
        row = self._list.currentRow()
        target = row + delta
        if row < 0 or not 0 <= target < self._list.count():
            return
        item = self._list.takeItem(row)
        self._list.insertItem(target, item)
        self._list.setCurrentRow(target)

    def columns(self) -> list[str]:
        return [
            self._list.item(row).text()
            for row in range(self._list.count())
            if self._list.item(row).checkState() == Qt.CheckState.Checked
        ]


class ImportMappingDialog(QDialog):
    """Сопоставление колонок CSV-файла полям таблицы регистров."""

    def __init__(self, header: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import CSV")
        self.setMinimumSize(480, 360)

        guessed = guess_column_mapping(header)
        self._table = QTableWidget(len(header), 2)
        self._table.setHorizontalHeaderLabels(["File column", "Maps to"])
        self._table.verticalHeader().setVisible(False)
        for row, column in enumerate(header):
            item = QTableWidgetItem(column)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 0, item)
            combo = QComboBox()
            combo.addItems([SKIP, *CSV_COLUMNS])
            combo.setCurrentText(guessed.get(column, SKIP))
            self._table.setCellWidget(row, 1, combo)
        self._table.resizeColumnsToContents()
        self._table.setCurrentCell(0, 0)
        self._table.installEventFilter(self)  # keys work before the view eats them

        self._warning = QLabel()
        self._warning.setStyleSheet("color: red")
        self._warning.hide()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Match file columns to register fields"))
        layout.addWidget(self._table)
        layout.addWidget(self._warning)
        layout.addWidget(buttons)
        self._table.setFocus()

    def eventFilter(self, obj: QObject | None, event: QEvent | None) -> bool:
        # arrows move natively; Space/F2 opens the combo; Enter validates
        if (
            obj is self._table
            and isinstance(event, QKeyEvent)
            and event.type() == QEvent.Type.KeyPress  # not ShortcutOverride/KeyRelease
        ):
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._validate()
                return True
            if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_F2):
                self._open_current_combo()
                return True
        return super().eventFilter(obj, event)

    def _open_current_combo(self) -> None:
        item = self._table.currentItem()
        if item is None or item.column() != 1:
            return
        combo = self._table.cellWidget(item.row(), 1)
        combo.setFocus()
        combo.showPopup()

    def mapping(self) -> dict[str, str]:
        mapping = {}
        for row in range(self._table.rowCount()):
            combo = self._table.cellWidget(row, 1)
            if combo.currentText() != SKIP:
                mapping[self._table.item(row, 0).text()] = combo.currentText()
        return mapping

    def _validate(self) -> None:
        fields = set(self.mapping().values())
        missing = [field for field in ESSENTIAL_FIELDS if field not in fields]
        if missing:
            self._warning.setText(f"Map the essential fields: {', '.join(missing)}")
            self._warning.show()
            return
        self.accept()
