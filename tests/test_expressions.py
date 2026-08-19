import itertools
import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Qt needs system libraries (libEGL etc.) that headless Linux CI runners lack;
# skip the whole module there — it still runs on macOS/Windows CI and dev machines.
try:
    from PySide6.QtCore import Qt  # noqa: E402
    from PySide6.QtWidgets import QApplication  # noqa: E402
except ImportError as exc:
    pytest.skip(f"Qt system libraries not available: {exc}", allow_module_level=True)

from PySide6.QtCore import QModelIndex  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QCompleter,
    QDialog,
    QStyleOptionViewItem,
    QWidget,
)

from modbus_connector import theme  # noqa: E402
from modbus_connector.alarms_dialog import COL_VALUE, AlarmsDialog  # noqa: E402
from modbus_connector.models import AlarmRule, alarm_rule_to_json  # noqa: E402
from modbus_connector.registers_panel import (  # noqa: E402
    COL_NAME,
    EXPR_COL_EXPR,
    EXPR_COL_NAME,
    EXPR_COL_VALUE,
    ExpressionDelegate,
    RegistersPanel,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _destroy_panels(qapp: QApplication) -> Iterator[None]:
    yield
    # leaked panels with torn-down/reinserted rows crash a later app-wide
    # stylesheet switch (QTableView.updateEditorGeometries); destroy them
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, RegistersPanel):
            widget.close()
            widget.deleteLater()
    qapp.processEvents()


def _panel() -> RegistersPanel:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_state(
        [{"name": "temp", "kind": "holding_registers", "address": 0, "count": 1}]
    )
    return panel


def _read_row(panel: RegistersPanel, index: int) -> int:
    reads: list[tuple] = []
    panel.readRequested.connect(lambda *args: reads.append(args))
    panel.set_bus_enabled(True)  # a read requires a connection
    panel._read_table_row(index)
    assert len(reads) == 1
    return int(reads[0][0])


def _expr_value(panel: RegistersPanel, index: int) -> str:
    item = panel._expr_table.item(index, EXPR_COL_VALUE)
    return item.text() if item is not None else ""


def _set_expr(panel: RegistersPanel, index: int, text: str) -> None:
    item = panel._expr_table.item(index, EXPR_COL_EXPR)
    assert item is not None
    item.setText(text)  # itemChanged == commit


def test_add_expression_focuses_name_and_emits_rows_changed(
    qapp: QApplication,
) -> None:
    panel = _panel()
    emissions: list[None] = []
    panel.rowsChanged.connect(lambda: emissions.append(None))
    panel._expr_button.setChecked(True)
    assert not panel._expr_widget.isHidden()

    panel._on_add_expression()
    assert panel._expr_table.rowCount() == 1
    assert panel._expr_table.currentColumn() == EXPR_COL_NAME  # focus in Name
    assert emissions  # the graph window rebuilds its checklist


def test_block_visibility_toggles_and_round_trips(qapp: QApplication) -> None:
    panel = _panel()
    assert panel._expr_widget.isHidden()
    assert panel.options_state()["expressions_visible"] is False

    panel._expr_button.setChecked(True)
    assert not panel._expr_widget.isHidden()
    assert panel.options_state()["expressions_visible"] is True

    other = _panel()
    other.set_options({"expressions_visible": True})
    assert not other._expr_widget.isHidden()
    assert other.options_state()["expressions_visible"] is True


def test_expression_recalcs_on_dependency_read_scaled(qapp: QApplication) -> None:
    panel = _panel()
    panel._row_display[panel._token_at(0)].scale = 10.0
    panel._row_display[panel._token_at(0)].offset = 5.0
    panel._add_expression("t2", "[temp] * 2")
    assert _expr_value(panel, 0) == "—"  # temp not read yet

    request_id = _read_row(panel, 0)
    panel.handle_read_finished(request_id, True, [3], "")
    # primary = 3 * 10 + 5 = 35, expression = 70
    assert _expr_value(panel, 0) == "70"


def test_constant_expression_and_formatting(qapp: QApplication) -> None:
    panel = _panel()
    panel._add_expression("c", "1 / 3")
    assert _expr_value(panel, 0) == "0.333333"  # %g: ~6 significant digits


def test_missing_dependency_and_math_error_show_dash(qapp: QApplication) -> None:
    panel = _panel()
    panel._add_expression("missing", "[ghost] + 1")
    panel._add_expression("div0", "1 / 0")
    request_id = _read_row(panel, 0)
    panel.handle_read_finished(request_id, True, [3], "")
    assert _expr_value(panel, 0) == "—"  # KeyError: no such row
    assert _expr_value(panel, 1) == "—"  # nan: division by zero


def test_invalid_expression_shows_warning_with_tooltip(qapp: QApplication) -> None:
    panel = _panel()
    panel._add_expression("bad", "[temp] + 1")
    request_id = _read_row(panel, 0)
    panel.handle_read_finished(request_id, True, [3], "")
    assert _expr_value(panel, 0) == "4"  # was valid

    _set_expr(panel, 0, "[temp] +")  # commit garbage
    item = panel._expr_table.item(0, EXPR_COL_VALUE)
    assert item is not None
    assert item.text() == "⚠"
    assert item.toolTip()  # the parse error text rides in the tooltip
    assert panel._expr_parsed[panel._expr_token_at(0)] is None  # previous valid reset

    _set_expr(panel, 0, "[temp] - 1")  # valid again: recovers
    assert _expr_value(panel, 0) == "2"
    assert item.toolTip() == ""


def test_series_appends_only_while_recording(qapp: QApplication) -> None:
    panel = _panel()
    panel._add_expression("t2", "[temp] * 2")
    token = panel.expr_tokens()[0]

    request_id = _read_row(panel, 0)
    panel.handle_read_finished(request_id, True, [3], "")
    assert len(panel.expr_series(token)) == 0  # not polling: no history

    panel.start_polling(False)  # polling without record
    request_id = _read_row(panel, 0)
    panel.handle_read_finished(request_id, True, [4], "")
    assert len(panel.expr_series(token)) == 0

    panel.start_polling(True)  # poll+record: history on
    request_id = _read_row(panel, 0)
    panel.handle_read_finished(request_id, True, [5], "")
    series = panel.expr_series(token)
    assert len(series) == 1
    assert series.points()[1][-1] == 10.0  # scale 1 → primary 5, expr = 5*2
    panel.stop_polling()


def test_state_round_trip_expressions(qapp: QApplication) -> None:
    panel = _panel()
    panel._add_expression("t2", "[temp] * 2")
    panel._add_expression("broken", "[temp] +")  # invalid, loads back as ⚠
    state = panel.expressions_state()
    assert state == [
        {"name": "t2", "expr": "[temp] * 2", "alarms": []},
        {"name": "broken", "expr": "[temp] +", "alarms": []},
    ]

    other = _panel()
    other.set_expressions_state(state + ["garbage", {"name": 1}, {}])
    # tolerant parse: junk entries are skipped, {"name": 1} loads as name "1"
    assert other.expressions_state() == [
        *state,
        {"name": "1", "expr": "", "alarms": []},
    ]
    assert _expr_value(other, 1) == "⚠"  # invalid expr loads with a warning
    item = other._expr_table.item(1, EXPR_COL_VALUE)
    assert item is not None and item.toolTip()


def test_delete_expression_row(qapp: QApplication) -> None:
    panel = _panel()
    panel._add_expression("a", "1 + 1")
    panel._add_expression("b", "2 + 2")
    token_a = panel.expr_tokens()[0]
    emissions: list[None] = []
    panel.rowsChanged.connect(lambda: emissions.append(None))

    actions = panel._expr_table.cellWidget(0, 4)  # EXPR_COL_ACTIONS
    button = actions.layout().itemAt(0).widget()
    button.click()
    assert panel._expr_table.rowCount() == 1
    assert panel.expr_tokens() == [panel.expr_tokens()[0]]
    assert token_a not in panel.expr_tokens()
    assert panel.expr_series(token_a) is None
    assert emissions


def test_renaming_register_row_updates_expression(qapp: QApplication) -> None:
    panel = _panel()
    panel._add_expression("t2", "[temp] * 2")
    request_id = _read_row(panel, 0)
    panel.handle_read_finished(request_id, True, [3], "")
    assert _expr_value(panel, 0) == "6"

    name_item = panel._table.item(0, COL_NAME)
    assert name_item is not None
    name_item.setText("renamed")  # the [temp] dep disappears
    assert _expr_value(panel, 0) == "—"

    _set_expr(panel, 0, "[renamed] * 2")
    assert _expr_value(panel, 0) == "6"  # the dep reappeared


def test_graph_checklist_lists_expressions(qapp: QApplication) -> None:
    pytest.importorskip("pyqtgraph")
    from modbus_connector.graph_window import GraphWindow

    panel = _panel()
    window = GraphWindow(panel)
    try:
        panel._add_expression("double", "[temp] * 2")
        assert window._rows_list.count() == 2  # register row + expression
        item = window._rows_list.item(1)
        assert item.text() == "fx double"
        expr_token = panel.expr_tokens()[0]
        assert expr_token in window._curves  # plotted on first sight

        # expressions have no poll checkbox: untoggling the register row
        # hides only the register series, the expression stays
        poll_item = panel._table.item(0, 0)  # COL_POLL_ENABLED
        poll_item.setCheckState(Qt.CheckState.Unchecked)
        assert window._rows_list.count() == 1
        assert window._rows_list.item(0).text() == "fx double"
        assert expr_token in window._curves

        actions = panel._expr_table.cellWidget(0, 4)
        actions.layout().itemAt(0).widget().click()
        assert window._rows_list.count() == 0  # live removal via rowsChanged
        assert expr_token not in window._curves
    finally:
        window.close()
        window.deleteLater()


# --- history clearing ---------------------------------------------------------


def test_graph_clear_clears_expression_history(qapp: QApplication) -> None:
    pytest.importorskip("pyqtgraph")
    from modbus_connector.graph_window import GraphWindow

    panel = _panel()
    panel._add_expression("t2", "[temp] * 2")
    expr_token = panel.expr_tokens()[0]
    window = GraphWindow(panel)
    try:
        panel.start_polling(True)  # record mode: history is captured
        panel.handle_read_finished(_read_row(panel, 0), True, [3], "")
        assert len(panel.expr_series(expr_token)) == 1
        assert len(panel.series(panel._token_at(0))) == 1

        window._on_clear()  # Clear of the graph window wipes everything
        assert len(panel.expr_series(expr_token)) == 0
        assert len(panel.series(panel._token_at(0))) == 0
    finally:
        panel.stop_polling()
        window.close()
        window.deleteLater()


def test_register_history_clear_keeps_expression_history(qapp: QApplication) -> None:
    panel = _panel()
    panel._add_expression("t2", "[temp] * 2")
    expr_token = panel.expr_tokens()[0]
    panel.start_polling(True)
    panel.handle_read_finished(_read_row(panel, 0), True, [3], "")

    panel._clear_register_series()  # the table's "Clear history" context action
    assert len(panel.series(panel._token_at(0))) == 0
    assert len(panel.expr_series(expr_token)) == 1  # expressions stay

    panel.clear_series()  # the graph's global Clear wipes expressions too
    assert len(panel.expr_series(expr_token)) == 0
    panel.stop_polling()


# --- help ---------------------------------------------------------------------


def test_expressions_help_button_opens_dialog(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QTextBrowser

    panel = _panel()
    panel._expr_help_button.click()
    dialog = next(
        widget
        for widget in QApplication.topLevelWidgets()
        if isinstance(widget, QDialog) and widget.windowTitle() == "Expressions — Help"
    )
    text = dialog.findChild(QTextBrowser).toPlainText()
    assert "[name]" in text
    assert "clamp" in text
    dialog.close()
    qapp.processEvents()  # WA_DeleteOnClose
    assert all(
        not (
            isinstance(widget, QDialog)
            and widget.windowTitle() == "Expressions — Help"
        )
        for widget in QApplication.topLevelWidgets()
    )


# --- alarms on expressions -----------------------------------------------------


def _expr_panel_with_rule(
    rule: AlarmRule, expr_text: str = "[temp] * 2"
) -> tuple[RegistersPanel, int, list[str]]:
    panel = _panel()
    token = panel._add_expression("t2", expr_text)
    panel._expr_alarms[token] = [rule]
    lines: list[str] = []
    panel.logLine.connect(lines.append)
    return panel, token, lines


def test_alarms_dialog_lists_and_applies_expression_rules(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel = _panel()
    expr_token = panel._add_expression("t2", "[temp] * 2")
    seen_labels: list[str] = []

    def fake_exec(dialog: AlarmsDialog) -> QDialog.DialogCode:
        seen_labels.extend(
            dialog._rows_list.item(i).text() for i in range(dialog._rows_list.count())
        )
        dialog._rows_list.setCurrentRow(1)  # the expression entry
        dialog._add_rule()
        dialog._table.item(0, COL_VALUE).setText("5")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AlarmsDialog, "exec", fake_exec)
    panel._on_alarms()
    assert seen_labels == ["temp", "fx t2"]  # expressions listed with fx prefix
    assert panel._expr_alarms[expr_token] == [AlarmRule("gt", 5)]
    assert panel._row_display[panel._token_at(0)].alarms == []  # registers untouched


def test_expression_alarm_state_round_trip(qapp: QApplication) -> None:
    rules = [
        AlarmRule("gt", 5, color="yellow"),
        AlarmRule("in_range", 1, 2, sound=True),
    ]
    panel = _panel()
    token = panel._add_expression("t2", "[temp] * 2")
    panel._expr_alarms[token] = rules
    state = panel.expressions_state()
    assert state[0]["alarms"] == [alarm_rule_to_json(rule) for rule in rules]

    other = _panel()
    other.set_expressions_state(state)
    assert other._expr_alarms[other.expr_tokens()[0]] == rules

    other.set_expressions_state(  # tolerant: junk rules are skipped
        [{"name": "x", "expr": "1",
          "alarms": [{"condition": "gt", "value": 1}, "junk"]}]
    )
    assert other._expr_alarms[other.expr_tokens()[0]] == [AlarmRule("gt", 1)]


def test_expression_alarm_highlight_and_edge_log(qapp: QApplication) -> None:
    theme.apply_theme("light")  # the alarm color is theme-dependent: pin it
    panel, token, lines = _expr_panel_with_rule(AlarmRule("gt", 5))
    item = panel._expr_table.item(0, EXPR_COL_VALUE)
    assert item is not None

    panel.handle_read_finished(_read_row(panel, 0), True, [3], "")  # expr = 6
    assert item.background().color() == QColor(0xF5, 0xB7, 0xB1)  # light red
    alarm_lines = [line for line in lines if "ALARM" in line]
    assert alarm_lines == ["ALARM fx t2: 6 > 5"]  # logged once on the edge

    panel.handle_read_finished(_read_row(panel, 0), True, [10], "")  # 20: still on
    alarm_lines = [line for line in lines if "ALARM" in line]
    assert alarm_lines == ["ALARM fx t2: 6 > 5"]  # no new line

    panel.handle_read_finished(_read_row(panel, 0), True, [1], "")  # 2: cleared
    assert item.background().style() == Qt.BrushStyle.NoBrush
    assert token not in panel._active_alarms
    alarm_lines = [line for line in lines if "ALARM" in line]
    assert alarm_lines[-1] == "ALARM cleared fx t2"
    theme.apply_theme("system")  # restore the app-global theme


def test_expression_dash_never_alarms(qapp: QApplication) -> None:
    panel, token, lines = _expr_panel_with_rule(AlarmRule("gt", 0), "[ghost] + 1")
    panel.handle_read_finished(_read_row(panel, 0), True, [3], "")
    assert _expr_value(panel, 0) == "—"  # KeyError: no such row
    assert token not in panel._active_alarms
    assert not any("ALARM" in line for line in lines)

    _set_expr(panel, 0, "1 / 0")  # nan: math error
    panel.handle_read_finished(_read_row(panel, 0), True, [4], "")
    assert _expr_value(panel, 0) == "—"
    assert token not in panel._active_alarms
    assert not any("ALARM" in line for line in lines)


def test_expression_warning_clears_active_alarm(qapp: QApplication) -> None:
    panel, token, lines = _expr_panel_with_rule(AlarmRule("gt", 5))
    panel.handle_read_finished(_read_row(panel, 0), True, [3], "")  # 6: alarm on
    assert token in panel._active_alarms

    _set_expr(panel, 0, "[temp] +")  # commit garbage: ⚠ has no number
    item = panel._expr_table.item(0, EXPR_COL_VALUE)
    assert item is not None
    assert item.text() == "⚠"
    assert token not in panel._active_alarms  # cleared per the usual semantics
    assert item.background().color() == theme.alarm_color("red")  # error paint stays
    assert "ALARM cleared fx t2" in lines

    _set_expr(panel, 0, "[temp] - 4")  # valid again, 3 - 4 = -1: below the rule
    assert token not in panel._active_alarms
    panel.handle_read_finished(_read_row(panel, 0), True, [10], "")  # 6: fresh edge
    assert token in panel._active_alarms
    assert lines[-1] == "ALARM fx t2: 6 > 5"


# --- expression cell autocompletion --------------------------------------------


def _expr_editor(panel: RegistersPanel) -> tuple[QWidget, QCompleter]:
    delegate = panel._expr_table.itemDelegateForColumn(EXPR_COL_EXPR)
    assert isinstance(delegate, ExpressionDelegate)
    editor = delegate.createEditor(
        panel._expr_table, QStyleOptionViewItem(), QModelIndex()
    )
    completer = editor.findChild(QCompleter)
    assert completer is not None
    return editor, completer


def test_expression_completer_offers_names_functions_constants(
    qapp: QApplication,
) -> None:
    panel = _panel()
    editor, completer = _expr_editor(panel)
    model = completer.model()

    editor.setText("[t")  # inside a reference: register row names, closed
    assert "temp]" in model.stringList()

    editor.setText("sq")  # a word start: functions with an opening paren
    assert "sqrt(" in model.stringList()

    editor.setText("p")
    words = model.stringList()
    assert "pi" in words  # constants are offered too
    assert "pow(" in words
    assert "e" in words


def test_expression_completer_follows_row_renames(qapp: QApplication) -> None:
    panel = _panel()
    editor, completer = _expr_editor(panel)
    model = completer.model()

    editor.setText("[t")
    assert model.stringList() == ["temp]"]

    name_item = panel._table.item(0, COL_NAME)
    assert name_item is not None
    name_item.setText("renamed")
    editor.setText("[r")
    assert model.stringList() == ["renamed]"]


def test_expression_completer_inserts_completion(qapp: QApplication) -> None:
    from PySide6.QtTest import QTest

    panel = _panel()
    editor, completer = _expr_editor(panel)
    editor.show()  # the popup opens only for a visible editor
    try:
        editor.setText("[te")
        popup = completer.popup()
        assert popup.isVisible()
        popup.setCurrentIndex(completer.completionModel().index(0, 0))
        QTest.keyClick(popup, Qt.Key.Key_Return)  # inserts, does not commit a cell
        assert editor.text() == "[temp]"

        editor.setText("sq")
        assert popup.isVisible()
        popup.setCurrentIndex(completer.completionModel().index(0, 0))
        QTest.keyClick(popup, Qt.Key.Key_Return)
        assert editor.text() == "sqrt("
    finally:
        editor.close()
        editor.deleteLater()
