"""Диалог настроек логирования значений в файл."""

from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from modbus_connector.datalogger import LOG_FIELDS, LogFormat, LogSettings

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
    """Файл, формат и набор полей для логирования значений."""

    def __init__(self, settings: LogSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Logging settings")

        self._path_edit = QLineEdit(settings.path or suggested_path(settings.format))
        self._path_edit.setPlaceholderText("Log file path")
        browse_button = QPushButton("Browse…")
        browse_button.clicked.connect(self._on_browse)
        path_row = QHBoxLayout()
        path_row.addWidget(self._path_edit, 1)
        path_row.addWidget(browse_button)

        self._format_combo = QComboBox()
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
        form.addRow(self._warning)
        form.addRow(buttons)

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
