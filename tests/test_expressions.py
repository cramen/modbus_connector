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

from modbus_connector.registers_panel import (  # noqa: E402
    COL_NAME,
    EXPR_COL_EXPR,
    EXPR_COL_NAME,
    EXPR_COL_VALUE,
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
        {"name": "t2", "expr": "[temp] * 2"},
        {"name": "broken", "expr": "[temp] +"},
    ]

    other = _panel()
    other.set_expressions_state(state + ["garbage", {"name": 1}, {}])
    # tolerant parse: junk entries are skipped, {"name": 1} loads as name "1"
    assert other.expressions_state() == [*state, {"name": "1", "expr": ""}]
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
