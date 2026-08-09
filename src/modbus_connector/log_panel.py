from datetime import datetime

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

MAX_LINES_PER_KIND = 5000


class LogPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # (is_raw, timestamped text) — source of truth for filtering/re-render
        self._entries: list[tuple[bool, str]] = []
        self._counts = [0, 0]  # normal, raw
        self._edit = QPlainTextEdit(readOnly=True, maximumBlockCount=2 * MAX_LINES_PER_KIND)
        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(self._clear)
        self._raw_checkbox = QCheckBox("Raw")  # unchecked: raw frames are noisy
        self._raw_checkbox.toggled.connect(self._render)

        buttons = QHBoxLayout()
        buttons.addWidget(self._raw_checkbox)
        buttons.addStretch(1)
        buttons.addWidget(clear_button)

        layout = QVBoxLayout(self)
        layout.addLayout(buttons)
        layout.addWidget(self._edit)

    @Slot(str)
    def append(self, line: str) -> None:
        self._append(False, line)

    @Slot(str)
    def append_raw(self, line: str) -> None:
        self._append(True, line)

    def _append(self, is_raw: bool, line: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        text = f"[{timestamp}] {line}"
        self._entries.append((is_raw, text))
        self._counts[is_raw] += 1
        if self._counts[is_raw] > MAX_LINES_PER_KIND:
            index = next(i for i, (raw, _) in enumerate(self._entries) if raw == is_raw)
            del self._entries[index]
            self._counts[is_raw] -= 1
        if not is_raw or self._raw_checkbox.isChecked():
            self._edit.appendPlainText(text)

    @Slot()
    def _render(self) -> None:
        show_raw = self._raw_checkbox.isChecked()
        self._edit.setPlainText(
            "\n".join(text for is_raw, text in self._entries if show_raw or not is_raw)
        )

    @Slot()
    def _clear(self) -> None:
        self._entries.clear()
        self._counts = [0, 0]
        self._edit.clear()
