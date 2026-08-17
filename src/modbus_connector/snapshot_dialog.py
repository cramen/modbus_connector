"""Немодальное окно сравнения снапшота значений с текущими чтениями.

Окно одно на панель регистров (повторное открытие поднимает и обновляет
существующее). Данные тянет из провайдера панели — само окно Qt-логики
панели не знает; «Take new snapshot» переснимает снапшот колбэком и
пересчитывает таблицу. Язык читается при открытии, как у прочих диалогов.
"""

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtGui import QBrush
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from modbus_connector import theme
from modbus_connector.i18n import tr

HEADERS = ("Name", "Type", "Address", "Snapshot", "Current")


@dataclass(frozen=True)
class DiffRow:
    """Строка сравнения: значения снапшота и текущего чтения, уже
    отформатированные панелью текущим форматом строки; removed — строка
    удалена из таблицы после снятия снапшота."""

    name: str
    kind: str
    address: str
    snapshot_text: str
    current_text: str
    changed: bool
    removed: bool = False


class SnapshotDiffDialog(QDialog):
    """Таблица Name/Type/Address/Snapshot/Current по всем строкам; строки,
    где raw-значения различаются (или одна из сторон без данных), подсвечены
    theme.diff_color(). «Only differences» фильтрует таблицу, «Refresh»
    перечитывает текущие значения у панели."""

    def __init__(
        self,
        data_provider: Callable[[], tuple[str, list[DiffRow]]],
        take_snapshot: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._data_provider = data_provider
        self._take_snapshot = take_snapshot
        self._changed: list[bool] = []  # per table row, for the filter
        self.setWindowTitle(tr("Snapshot diff"))

        self._info_label = QLabel()
        self._only_diffs = QCheckBox(tr("Only differences"))
        self._only_diffs.toggled.connect(self._apply_filter)

        self._table = QTableWidget(0, len(HEADERS))
        self._table.setHorizontalHeaderLabels([tr(h) for h in HEADERS])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )

        self._refresh_button = QPushButton(tr("Refresh"))
        self._refresh_button.clicked.connect(self.refresh)
        self._new_snapshot_button = QPushButton(tr("Take new snapshot"))
        self._new_snapshot_button.clicked.connect(self._on_take_snapshot)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        top = QHBoxLayout()
        top.addWidget(self._info_label, 1)
        top.addWidget(self._only_diffs)
        bottom = QHBoxLayout()
        bottom.addWidget(self._refresh_button)
        bottom.addWidget(self._new_snapshot_button)
        bottom.addStretch(1)
        bottom.addWidget(buttons)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self._table)
        layout.addLayout(bottom)
        self.resize(660, 420)
        self.refresh()

    def refresh(self) -> None:
        """Перечитать данные у провайдера и перестроить таблицу."""
        info, rows = self._data_provider()
        self._info_label.setText(info)
        self._changed = [row.changed for row in rows]
        brush = QBrush(theme.diff_color())
        self._table.setRowCount(len(rows))
        for index, entry in enumerate(rows):
            cells = (
                entry.name,
                entry.kind,
                entry.address,
                entry.snapshot_text,
                entry.current_text,
            )
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if entry.changed:
                    item.setBackground(brush)
                self._table.setItem(index, col, item)
        self._table.resizeColumnsToContents()
        self._apply_filter()

    def _apply_filter(self) -> None:
        only = self._only_diffs.isChecked()
        for index in range(self._table.rowCount()):
            self._table.setRowHidden(index, only and not self._changed[index])

    def _on_take_snapshot(self) -> None:
        self._take_snapshot()  # панель переснимает снапшот и пишет строку в лог
        self.refresh()
