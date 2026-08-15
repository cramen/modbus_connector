"""Диалог настроек логирования значений в файл."""

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from modbus_connector.datalogger import LOG_FIELDS, LogFormat, LogSettings
from modbus_connector.theme import FitComboBox

FORMAT_LABELS: dict[LogFormat, str] = {"csv": "CSV", "jsonl": "JSON Lines"}
FORMAT_EXTENSIONS: dict[LogFormat, str] = {"csv": ".csv", "jsonl": ".jsonl"}
FIELD_LABELS = {
    "timestamp": "Timestamp",
    "name": "Row name",
    "address": "Address",
    "kind": "Register type",
}


def suggested_path(fmt: LogFormat) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return str(Path.home() / f"modbus_log_{stamp}{FORMAT_EXTENSIONS[fmt]}")


class LoggingSettingsDialog(QDialog):
    """Файл, формат, набор полей и логируемые строки таблицы."""

    def __init__(
        self,
        settings: LogSettings,
        rows: list[tuple[int, str, bool]] | None = None,  # (token, label, log)
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Logging settings")

        self._path_edit = QLineEdit(settings.path or suggested_path(settings.format))
        self._path_edit.setPlaceholderText("Log file path")
        browse_button = QPushButton("Browse…")
        browse_button.clicked.connect(self._on_browse)
        path_row = QHBoxLayout()
        path_row.addWidget(self._path_edit, 1)
        path_row.addWidget(browse_button)

        self._format_combo = FitComboBox()
        self._format_combo.addItems(list(FORMAT_LABELS.values()))
        self._format_combo.setCurrentText(FORMAT_LABELS[settings.format])
        self._format_combo.currentTextChanged.connect(self._on_format_changed)

        self._field_checks = {
            log_field: QCheckBox(FIELD_LABELS[log_field]) for log_field in LOG_FIELDS
        }
        for log_field, check in self._field_checks.items():
            check.setChecked(log_field in settings.fields)

        self._append_check = QCheckBox("Append to the file if it exists")
        self._append_check.setChecked(settings.append)

        self._rows_list = QListWidget()
        for token, label, checked in rows or []:
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )
            item.setData(Qt.ItemDataRole.UserRole, token)
            self._rows_list.addItem(item)
        if self._rows_list.count():
            self._rows_list.setCurrentRow(0)
        self._rows_list.installEventFilter(self)  # keys work before the view eats them
        select_all = QPushButton("Select all")
        select_all.clicked.connect(lambda: self._set_all_rows(Qt.CheckState.Checked))
        select_none = QPushButton("Select none")
        select_none.clicked.connect(lambda: self._set_all_rows(Qt.CheckState.Unchecked))
        rows_buttons = QHBoxLayout()
        rows_buttons.addWidget(select_all)
        rows_buttons.addWidget(select_none)
        rows_buttons.addStretch(1)
        rows_box = QGroupBox("Rows to log")
        rows_layout = QVBoxLayout(rows_box)
        rows_layout.addWidget(self._rows_list)
        rows_layout.addLayout(rows_buttons)

        self._warning = QLabel()
        self._warning.setStyleSheet("color: red")
        self._warning.hide()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)

        form = QFormLayout(self)
        form.addRow("File:", path_row)
        form.addRow("Format:", self._format_combo)
        fields_row = QHBoxLayout()
        for check in self._field_checks.values():
            fields_row.addWidget(check)
        form.addRow("Fields:", fields_row)
        form.addRow("", self._append_check)
        form.addRow(rows_box)
        form.addRow(self._warning)
        form.addRow(buttons)

    def eventFilter(self, obj: QObject | None, event: QEvent | None) -> bool:
        # Space toggles natively; arrows navigate; Enter validates
        if (
            obj is self._rows_list
            and isinstance(event, QKeyEvent)
            and event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        ):
            self._validate()
            return True
        return super().eventFilter(obj, event)

    def _set_all_rows(self, state: Qt.CheckState) -> None:
        for row in range(self._rows_list.count()):
            self._rows_list.item(row).setCheckState(state)

    def row_flags(self) -> dict[int, bool]:
        return {
            int(item.data(Qt.ItemDataRole.UserRole)): (
                item.checkState() == Qt.CheckState.Checked
            )
            for row in range(self._rows_list.count())
            if (item := self._rows_list.item(row)) is not None
        }

    def _format(self) -> LogFormat:
        for fmt, label in FORMAT_LABELS.items():
            if label == self._format_combo.currentText():
                return fmt
        return "csv"

    def _on_format_changed(self) -> None:
        # keep the extension in sync with the format when it was a known one
        path = self._path_edit.text()
        for fmt, extension in FORMAT_EXTENSIONS.items():
            if fmt != self._format() and path.endswith(extension):
                self._path_edit.setText(path[: -len(extension)] + FORMAT_EXTENSIONS[self._format()])
                return

    def _on_browse(self) -> None:
        fmt = self._format()
        path_str, _ = QFileDialog.getSaveFileName(
            self, "Log values to file", self._path_edit.text(),
            f"{FORMAT_LABELS[fmt]} (*{FORMAT_EXTENSIONS[fmt]})",
        )
        if path_str:
            self._path_edit.setText(path_str)

    def _validate(self) -> None:
        if not self._path_edit.text().strip():
            self._warning.setText("Choose a log file")
            self._warning.show()
            return
        self.accept()

    def settings(self) -> LogSettings:
        return LogSettings(
            path=self._path_edit.text().strip(),
            format=self._format(),
            fields=frozenset(
                log_field
                for log_field, check in self._field_checks.items()
                if check.isChecked()
            ),
            append=self._append_check.isChecked(),
        )
