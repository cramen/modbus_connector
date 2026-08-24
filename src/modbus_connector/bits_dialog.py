"""Диалог правки u16-значения как 16 именованных битов (bitmask-режим)."""

from collections.abc import Mapping

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QVBoxLayout,
    QWidget,
)

from modbus_connector.i18n import tr
from modbus_connector.models import BIT_COUNT, bits_to_value


class BitsDialog(QDialog):
    """Модальный список из 16 чекбоксов в один столбец: подпись — имя бита
    или «bN».

    Чекнутость инициализируется из переданного значения; итог — value()
    (сборка через models.bits_to_value). Общий для master (New value) и
    slave (Value ручной строки) — поэтому живёт отдельным файлом."""

    def __init__(
        self, value: int, names: Mapping[int, str], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Edit bits"))
        self._boxes: list[QCheckBox] = []
        layout = QVBoxLayout(self)
        for bit in range(BIT_COUNT):
            box = QCheckBox(names.get(bit) or f"b{bit}")
            box.setChecked(bool(value & (1 << bit)))
            layout.addWidget(box)
            self._boxes.append(box)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self) -> int:
        """u16-значение из отмеченных битов."""
        return bits_to_value(
            bit for bit, box in enumerate(self._boxes) if box.isChecked()
        )
