from datetime import datetime

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget


class LogPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._edit = QPlainTextEdit(readOnly=True, maximumBlockCount=5000)
        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(self._edit.clear)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(clear_button)

        layout = QVBoxLayout(self)
        layout.addLayout(buttons)
        layout.addWidget(self._edit)

    @Slot(str)
    def append(self, line: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._edit.appendPlainText(f"[{timestamp}] {line}")
