import itertools
import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Qt needs system libraries (libEGL etc.) that headless Linux CI runners lack;
# skip the whole module there — it still runs on macOS/Windows CI and dev machines.
try:
    from PySide6.QtCore import Qt  # noqa: E402
    from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402
except ImportError as exc:
    pytest.skip(f"Qt system libraries not available: {exc}", allow_module_level=True)

from PySide6.QtGui import QColor  # noqa: E402

from modbus_connector import theme  # noqa: E402
from modbus_connector.alarms_dialog import (  # noqa: E402
    COL_SOUND,
    COL_VALUE,
    COL_VALUE2,
    AlarmsDialog,
)
from modbus_connector.models import AlarmRule, alarm_rule_to_json  # noqa: E402
from modbus_connector.registers_panel import (  # noqa: E402
    COL_VALUE as PANEL_COL_VALUE,
)
from modbus_connector.registers_panel import (
    RegistersPanel,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _destroy_widgets(qapp: QApplication) -> Iterator[None]:
    yield
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, (RegistersPanel, AlarmsDialog)):
            widget.close()
            widget.deleteLater()
    qapp.processEvents()


def _read_row(panel: RegistersPanel, index: int) -> int:
    reads: list[tuple] = []
    panel.readRequested.connect(lambda *args: reads.append(args))
    panel.set_bus_enabled(True)  # a read requires a connection
    panel._read_table_row(index)
    assert len(reads) == 1
    return int(reads[0][0])


# --- dialog -------------------------------------------------------------------


def test_dialog_shows_rules_per_row(qapp: QApplication) -> None:
    dialog = AlarmsDialog(
        [
            (1, "a @ 0", [AlarmRule("gt", 10, color="yellow", sound=True)]),
            (2, "b @ 1", []),
        ]
    )
    assert dialog._rows_list.count() == 2
    assert dialog._rows_list.item(0).text() == "a @ 0"
    assert dialog._table.rowCount() == 1  # the first row is selected on open

    rules = dialog.rules()
    assert rules is not None
    assert rules[1] == [AlarmRule("gt", 10, color="yellow", sound=True)]
    assert rules[2] == []

    dialog._rows_list.setCurrentRow(1)  # switching shows that row's rules
    assert dialog._table.rowCount() == 0
    dialog._rows_list.setCurrentRow(0)
    assert dialog._table.rowCount() == 1


def test_dialog_add_edit_remove(qapp: QApplication) -> None:
    dialog = AlarmsDialog([(1, "a @ 0", [])])
    dialog._add_rule()
    assert dialog._table.rowCount() == 1
    value_item = dialog._table.item(0, COL_VALUE)
    assert value_item is not None
    value_item.setText("42.5")

    rules = dialog.rules()
    assert rules is not None
    assert rules[1] == [AlarmRule("gt", 42.5)]  # defaults: red, log, no sound

    sound_item = dialog._table.item(0, COL_SOUND)
    assert sound_item is not None
    sound_item.setCheckState(Qt.CheckState.Checked)
    assert dialog.rules()[1][0].sound is True  # type: ignore[index]

    dialog._table.setCurrentCell(0, COL_VALUE)
    dialog._remove_rule()
    assert dialog._table.rowCount() == 0
    assert dialog.rules() == {1: []}


def test_dialog_move_changes_priority(qapp: QApplication) -> None:
    dialog = AlarmsDialog(
        [(1, "a @ 0", [AlarmRule("gt", 10), AlarmRule("lt", 0)])]
    )
    dialog._table.setCurrentCell(1, COL_VALUE)
    dialog._move_rule(-1)
    rules = dialog.rules()
    assert rules is not None
    assert rules[1] == [AlarmRule("lt", 0), AlarmRule("gt", 10)]

    dialog._table.setCurrentCell(0, COL_VALUE)
    dialog._move_rule(-1)  # already first: no-op
    assert dialog.rules()[1] == [AlarmRule("lt", 0), AlarmRule("gt", 10)]  # type: ignore[index]


def test_dialog_drafts_survive_row_switch(qapp: QApplication) -> None:
    dialog = AlarmsDialog([(1, "a @ 0", []), (2, "b @ 1", [])])
    dialog._add_rule()
    value_item = dialog._table.item(0, COL_VALUE)
    assert value_item is not None
    value_item.setText("12.5")  # not yet parsed: stored as raw text

    dialog._rows_list.setCurrentRow(1)
    dialog._rows_list.setCurrentRow(0)
    value_item = dialog._table.item(0, COL_VALUE)
    assert value_item is not None
    assert value_item.text() == "12.5"
    rules = dialog.rules()
    assert rules is not None
    assert rules[1] == [AlarmRule("gt", 12.5)]


def test_dialog_value2_enabled_only_for_ranges(qapp: QApplication) -> None:
    dialog = AlarmsDialog([(1, "a @ 0", [AlarmRule("gt", 5)])])
    value2_item = dialog._table.item(0, COL_VALUE2)
    assert value2_item is not None
    assert not value2_item.flags() & Qt.ItemFlag.ItemIsEditable

    condition_combo = dialog._table.cellWidget(0, 0)
    condition_combo.setCurrentIndex(6)  # "in range" (index: language-independent)
    assert value2_item.flags() & Qt.ItemFlag.ItemIsEditable

    condition_combo.setCurrentIndex(0)  # ">"
    assert not value2_item.flags() & Qt.ItemFlag.ItemIsEditable


def test_dialog_range_without_value2_uses_value(qapp: QApplication) -> None:
    dialog = AlarmsDialog([(1, "a @ 0", [])])
    dialog._add_rule()
    dialog._table.cellWidget(0, 0).setCurrentIndex(6)  # "in range"
    dialog._table.item(0, COL_VALUE).setText("7")
    rules = dialog.rules()
    assert rules is not None
    assert rules[1] == [AlarmRule("in_range", 7, 7)]


def test_dialog_rejects_non_numeric_value(qapp: QApplication) -> None:
    dialog = AlarmsDialog([(1, "a @ 0", [])])
    dialog._add_rule()
    dialog._table.item(0, COL_VALUE).setText("junk")
    assert dialog.rules() is None
    dialog._validate()
    assert not dialog._warning.isHidden()
    assert dialog.result() == 0  # still open, not accepted


# --- panel integration --------------------------------------------------------


def _panel_with_rule(
    rule: AlarmRule, row: dict | None = None
) -> tuple[RegistersPanel, int, list[str]]:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_state([row or {"name": "temp", "kind": "holding_registers",
                             "address": 0, "count": 1}])
    token = panel._token_at(0)
    panel._row_display[token].alarms = [rule]
    lines: list[str] = []
    panel.logLine.connect(lines.append)
    return panel, token, lines


def test_alarm_highlight_and_edge_log(qapp: QApplication) -> None:
    theme.apply_theme("light")  # the alarm color is theme-dependent: pin it
    panel, token, lines = _panel_with_rule(AlarmRule("gt", 5))

    panel.handle_read_finished(_read_row(panel, 0), True, [10], "")
    item = panel._table.item(0, PANEL_COL_VALUE)
    assert item is not None
    assert item.background().color() == QColor(0xF5, 0xB7, 0xB1)  # light red
    alarm_lines = [line for line in lines if "ALARM" in line]
    assert alarm_lines == ["ALARM temp: 10 > 5"]  # logged once on the edge

    panel.handle_read_finished(_read_row(panel, 0), True, [11], "")
    alarm_lines = [line for line in lines if "ALARM" in line]
    assert alarm_lines == ["ALARM temp: 10 > 5"]  # still active: no new line

    panel.handle_read_finished(_read_row(panel, 0), True, [3], "")
    assert item.background().style() == Qt.BrushStyle.NoBrush  # cleared
    alarm_lines = [line for line in lines if "ALARM" in line]
    assert alarm_lines[-1] == "ALARM cleared temp"
    assert token not in panel._active_alarms
    theme.apply_theme("system")  # restore the app-global theme


def test_alarm_outranks_change_flash(qapp: QApplication) -> None:
    theme.apply_theme("light")
    panel, _token, _lines = _panel_with_rule(AlarmRule("gt", 5))
    panel.handle_read_finished(_read_row(panel, 0), True, [10], "")
    panel.handle_read_finished(_read_row(panel, 0), True, [20], "")  # changed
    item = panel._table.item(0, PANEL_COL_VALUE)
    assert item is not None
    assert item.background().color() == QColor(0xF5, 0xB7, 0xB1)  # not the flash
    theme.apply_theme("system")


def test_alarm_evaluates_scaled_value(qapp: QApplication) -> None:
    panel, _token, lines = _panel_with_rule(
        AlarmRule("gt", 5),
        {"name": "t", "kind": "holding_registers", "address": 0, "count": 1,
         "scale": 0.1},
    )
    panel.handle_read_finished(_read_row(panel, 0), True, [30], "")  # 3.0 scaled
    assert not any("ALARM" in line for line in lines)
    panel.handle_read_finished(_read_row(panel, 0), True, [100], "")  # 10.0 scaled
    assert any("ALARM t: 10 > 5" in line for line in lines)


def test_alarm_skips_hex_and_ascii(qapp: QApplication) -> None:
    for fmt in ("hex", "ascii"):
        panel = RegistersPanel(itertools.count(1).__next__)
        panel.set_state(
            [{"name": "h", "kind": "holding_registers", "address": 0, "count": 1,
              "format": fmt}]
        )
        token = panel._token_at(0)
        panel._row_display[token].alarms = [AlarmRule("gt", 0)]
        lines: list[str] = []
        panel.logLine.connect(lines.append)
        panel.handle_read_finished(_read_row(panel, 0), True, [0x41], "")
        assert token not in panel._active_alarms
        assert not any("ALARM" in line for line in lines)


def test_alarm_without_log_flag_is_silent(qapp: QApplication) -> None:
    theme.apply_theme("light")
    panel, _token, lines = _panel_with_rule(AlarmRule("gt", 5, log=False))
    panel.handle_read_finished(_read_row(panel, 0), True, [10], "")
    item = panel._table.item(0, PANEL_COL_VALUE)
    assert item is not None
    assert item.background().color() == QColor(0xF5, 0xB7, 0xB1)  # still painted
    assert not any("ALARM" in line for line in lines)
    panel.handle_read_finished(_read_row(panel, 0), True, [1], "")
    assert not any("ALARM" in line for line in lines)  # clearing is silent too
    theme.apply_theme("system")


def test_alarm_state_roundtrip(qapp: QApplication) -> None:
    rules = [AlarmRule("gt", 5, color="yellow"), AlarmRule("in_range", 1, 2,
                                                           sound=True)]
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_state(
        [{"name": "a", "kind": "holding_registers", "address": 0, "count": 1,
          "alarms": [alarm_rule_to_json(rule) for rule in rules]},
         {"name": "b", "kind": "holding_registers", "address": 1, "count": 1}]
    )
    assert panel._row_display[panel._token_at(0)].alarms == rules
    assert panel._row_display[panel._token_at(1)].alarms == []  # missing key

    state = panel.state()
    assert state[0]["alarms"] == [alarm_rule_to_json(rule) for rule in rules]
    assert state[1]["alarms"] == []

    fresh = RegistersPanel(itertools.count(100).__next__)
    fresh.set_state(state)
    assert fresh._row_display[fresh._token_at(0)].alarms == rules

    fresh.set_state(  # broken entries are tolerated, bad rules skipped
        [{"name": "x", "kind": "holding_registers", "address": 0, "count": 1,
          "alarms": [{"condition": "gt", "value": 1}, "junk", {"no": "rule"}]}]
    )
    assert fresh._row_display[fresh._token_at(0)].alarms == [AlarmRule("gt", 1)]


def test_alarms_button_applies_dialog_rules(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    token = panel._token_at(0)

    def fake_exec(dialog: AlarmsDialog) -> QDialog.DialogCode:
        dialog._add_rule()
        dialog._table.item(0, COL_VALUE).setText("5")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AlarmsDialog, "exec", fake_exec)
    panel._on_alarms()
    assert panel._row_display[token].alarms == [AlarmRule("gt", 5)]

    # a rule raised before the edit fires again with the new edge state
    panel._row_display[token].alarms = []
    panel._re_evaluate_alarm(0)  # no last read yet: just clears, no crash


def test_rule_change_resets_edge_state(qapp: QApplication) -> None:
    panel, token, lines = _panel_with_rule(AlarmRule("gt", 5))
    panel.handle_read_finished(_read_row(panel, 0), True, [10], "")
    assert token in panel._active_alarms

    # removing the rules (as the dialog-apply path does) clears the highlight
    # and resolves the active -> cleared transition
    panel._row_display[token].alarms = []
    panel._re_evaluate_alarm(0)
    item = panel._table.item(0, PANEL_COL_VALUE)
    assert item is not None
    assert item.background().style() == Qt.BrushStyle.NoBrush
    assert token not in panel._active_alarms
    assert lines[-1] == "ALARM cleared temp"

    panel.handle_read_finished(_read_row(panel, 0), True, [10], "")
    assert item.background().style() == Qt.BrushStyle.NoBrush  # no rules, no alarm
    assert token not in panel._active_alarms
