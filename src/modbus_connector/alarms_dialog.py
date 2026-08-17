"""Модальный диалог правил алармов строк таблицы регистров."""

from typing import cast, get_args

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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

from modbus_connector.i18n import tr
from modbus_connector.models import AlarmColor, AlarmCondition, AlarmRule
from modbus_connector.theme import FitComboBox

CONDITIONS: tuple[AlarmCondition, ...] = get_args(AlarmCondition)
CONDITION_LABELS: dict[AlarmCondition, str] = {
    "gt": ">",
    "lt": "<",
    "ge": ">=",
    "le": "<=",
    "eq": "==",
    "ne": "!=",
    "in_range": "in range",
    "outside_range": "outside range",
}
RANGE_CONDITIONS = ("in_range", "outside_range")
COLORS: tuple[AlarmColor, ...] = get_args(AlarmColor)

(COL_CONDITION, COL_VALUE, COL_VALUE2, COL_COLOR, COL_LOG, COL_SOUND) = range(6)

# A draft is raw cell state (unparsed texts); drafts survive row switches,
# parsing into AlarmRule happens only on OK so half-typed text is not lost.
Draft = tuple[AlarmCondition, str, str, AlarmColor, bool, bool]


def _rule_to_draft(rule: AlarmRule) -> Draft:
    return (
        rule.condition,
        f"{rule.value:g}",
        "" if rule.value2 is None else f"{rule.value2:g}",
        rule.color,
        rule.log,
        rule.sound,
    )


def _draft_to_rule(draft: Draft) -> AlarmRule | None:
    """Собрать правило из черновика; нечисловой value/value2 → None."""
    condition, value_text, value2_text, color, log, sound = draft
    try:
        value = float(value_text)
    except ValueError:
        return None
    value2: float | None = None
    if condition in RANGE_CONDITIONS:
        # a range without value2 collapses to the single point [value..value]
        try:
            value2 = float(value2_text) if value2_text else value
        except ValueError:
            return None
    try:
        return AlarmRule(
            condition=condition, value=value, value2=value2,
            color=color, log=log, sound=sound,
        )
    except ValueError:
        return None


class AlarmsDialog(QDialog):
    """Слева — строки таблицы, справа — правила выбранной строки.

    Приоритет правил = их порядок в таблице (первое совпавшее срабатывает),
    поэтому правила можно двигать вверх/вниз.
    """

    def __init__(
        self,
        rows: list[tuple[int, str, list[AlarmRule]]],  # (token, label, rules)
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Alarm rules"))
        self._current_token: int | None = None
        self._drafts: dict[int, list[Draft]] = {
            token: [_rule_to_draft(rule) for rule in rules]
            for token, _label, rules in rows
        }

        self._rows_list = QListWidget()
        for token, label, _rules in rows:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, token)
            self._rows_list.addItem(item)
        self._rows_list.currentRowChanged.connect(self._on_row_changed)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            [tr(h) for h in ("Condition", "Value", "Value 2", "Color", "Log", "Sound")]
        )
        self._table.setToolTip(
            tr("The first matching rule wins; Value 2 is used by the range conditions")
        )

        add_button = QPushButton(tr("Add"))
        add_button.clicked.connect(self._add_rule)
        remove_button = QPushButton(tr("Remove"))
        remove_button.clicked.connect(self._remove_rule)
        up_button = QPushButton(tr("Up"))
        up_button.clicked.connect(lambda: self._move_rule(-1))
        down_button = QPushButton(tr("Down"))
        down_button.clicked.connect(lambda: self._move_rule(1))
        rule_buttons = QHBoxLayout()
        for button in (add_button, remove_button, up_button, down_button):
            rule_buttons.addWidget(button)
        rule_buttons.addStretch(1)

        self._warning = QLabel()
        self._warning.setStyleSheet("color: red")
        self._warning.hide()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)

        right = QVBoxLayout()
        right.addWidget(self._table, 1)
        right.addLayout(rule_buttons)
        main = QHBoxLayout()
        main.addWidget(self._rows_list, 1)
        main.addLayout(right, 3)
        layout = QVBoxLayout(self)
        layout.addLayout(main)
        layout.addWidget(self._warning)
        layout.addWidget(buttons)
        self.resize(720, 360)

        if self._rows_list.count():
            self._rows_list.setCurrentRow(0)  # loads the row via currentRowChanged

    def _on_row_changed(self, row: int) -> None:
        self._save_current()
        self._current_token = None
        self._table.setRowCount(0)
        item = self._rows_list.item(row)
        if item is None:
            return
        token = int(item.data(Qt.ItemDataRole.UserRole))
        self._current_token = token
        for draft in self._drafts.get(token, []):
            self._append_rule_row(draft)

    def _save_current(self) -> None:
        if self._current_token is not None:
            self._drafts[self._current_token] = self._table_draft()

    def _append_rule_row(self, draft: Draft) -> None:
        condition, value, value2, color, log, sound = draft
        row = self._table.rowCount()
        self._table.insertRow(row)

        condition_combo = FitComboBox()
        for key in CONDITIONS:
            condition_combo.addItem(tr(CONDITION_LABELS[key]), key)
        condition_combo.setCurrentIndex(CONDITIONS.index(condition))
        condition_combo.currentIndexChanged.connect(
            lambda _i, combo=condition_combo: self._on_condition_changed(combo)
        )
        self._table.setCellWidget(row, COL_CONDITION, condition_combo)

        self._table.setItem(row, COL_VALUE, QTableWidgetItem(value))
        self._table.setItem(row, COL_VALUE2, QTableWidgetItem(value2))

        color_combo = FitComboBox()
        for key in COLORS:
            color_combo.addItem(tr(key), key)
        color_combo.setCurrentIndex(COLORS.index(color))
        self._table.setCellWidget(row, COL_COLOR, color_combo)

        for col, checked in ((COL_LOG, log), (COL_SOUND, sound)):
            item = QTableWidgetItem()
            item.setFlags(
                (item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                & ~Qt.ItemFlag.ItemIsEditable
            )
            item.setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )
            self._table.setItem(row, col, item)
        self._sync_value2(row)

    def _on_condition_changed(self, combo: FitComboBox) -> None:
        for row in range(self._table.rowCount()):
            if self._table.cellWidget(row, COL_CONDITION) is combo:
                self._sync_value2(row)
                return

    def _sync_value2(self, row: int) -> None:
        combo = self._table.cellWidget(row, COL_CONDITION)
        item = self._table.item(row, COL_VALUE2)
        if combo is None or item is None:
            return
        ranged = combo.currentData() in RANGE_CONDITIONS
        flags = item.flags()
        item.setFlags(
            (flags | Qt.ItemFlag.ItemIsEditable)
            if ranged
            else (flags & ~Qt.ItemFlag.ItemIsEditable)
        )

    def _table_draft(self) -> list[Draft]:
        drafts = []
        for row in range(self._table.rowCount()):
            condition_combo = self._table.cellWidget(row, COL_CONDITION)
            color_combo = self._table.cellWidget(row, COL_COLOR)
            value_item = self._table.item(row, COL_VALUE)
            value2_item = self._table.item(row, COL_VALUE2)
            log_item = self._table.item(row, COL_LOG)
            sound_item = self._table.item(row, COL_SOUND)
            drafts.append(
                (
                    cast(AlarmCondition, condition_combo.currentData()),
                    value_item.text().strip() if value_item else "",
                    value2_item.text().strip() if value2_item else "",
                    cast(AlarmColor, color_combo.currentData()),
                    log_item is None
                    or log_item.checkState() == Qt.CheckState.Checked,
                    sound_item is not None
                    and sound_item.checkState() == Qt.CheckState.Checked,
                )
            )
        return drafts

    def _load_drafts(self, drafts: list[Draft]) -> None:
        self._table.setRowCount(0)
        for draft in drafts:
            self._append_rule_row(draft)

    def _add_rule(self) -> None:
        if self._current_token is None:
            return
        self._append_rule_row(("gt", "0", "", "red", True, False))
        self._table.setCurrentCell(self._table.rowCount() - 1, COL_VALUE)

    def _remove_rule(self) -> None:
        row = self._table.currentRow()
        if row >= 0:
            self._table.removeRow(row)

    def _move_rule(self, delta: int) -> None:
        row = self._table.currentRow()
        target = row + delta
        if row < 0 or not 0 <= target < self._table.rowCount():
            return
        drafts = self._table_draft()
        drafts[row], drafts[target] = drafts[target], drafts[row]
        self._load_drafts(drafts)
        self._table.setCurrentCell(target, COL_VALUE)

    def _validate(self) -> None:
        if self.rules() is None:
            self._warning.setText(tr("Rule value must be a number"))
            self._warning.show()
            return
        self.accept()

    def rules(self) -> dict[int, list[AlarmRule]] | None:
        """Правила по токенам строк; None — хотя бы одно значение не число."""
        self._save_current()
        result: dict[int, list[AlarmRule]] = {}
        for row in range(self._rows_list.count()):
            item = self._rows_list.item(row)
            if item is None:
                continue
            token = int(item.data(Qt.ItemDataRole.UserRole))
            rules = []
            for draft in self._drafts.get(token, []):
                rule = _draft_to_rule(draft)
                if rule is None:
                    return None
                rules.append(rule)
            result[token] = rules
        return result
