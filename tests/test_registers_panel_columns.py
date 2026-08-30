import itertools
import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Qt needs system libraries (libEGL etc.) that headless Linux CI runners lack;
# skip the whole module there — it still runs on macOS/Windows CI and dev machines.
# A plain try/except is needed: pytest.importorskip re-raises ImportErrors coming
# from a missing shared library inside an otherwise installed package.
try:
    from PySide6.QtCore import QItemSelection, QItemSelectionModel, Qt  # noqa: E402
    from PySide6.QtTest import QTest  # noqa: E402
    from PySide6.QtWidgets import (  # noqa: E402
        QAbstractItemView,
        QApplication,
    )
except ImportError as exc:
    pytest.skip(f"Qt system libraries not available: {exc}", allow_module_level=True)

from PySide6.QtGui import QGuiApplication  # noqa: E402

from modbus_connector.i18n import tr  # noqa: E402
from modbus_connector.models import RegisterRow  # noqa: E402
from modbus_connector.registers_panel import (  # noqa: E402
    COL_ACTIONS,
    COL_FORMAT,
    COL_NAME,
    COL_POLL_ENABLED,
    COL_TREND,
    COL_TYPE,
    COL_UNIT_ID,
    COL_VALUE,
    COLUMN_KEYS,
    DATA_COLUMNS,
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


def _read_row(panel: RegistersPanel, index: int) -> int:
    reads: list[tuple] = []
    panel.readRequested.connect(lambda *args: reads.append(args))
    panel.set_bus_enabled(True)  # a read requires a connection
    panel._read_table_row(index)
    assert len(reads) == 1
    return int(reads[0][0])


def _checked(panel: RegistersPanel, index: int) -> bool:
    item = panel._table.item(index, COL_POLL_ENABLED)
    return item is not None and item.checkState() == Qt.CheckState.Checked


def _five_row_panel() -> RegistersPanel:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_state(
        [{"name": name, "kind": "holding_registers", "address": address, "count": 1}
         for address, name in enumerate("ABCDE")]
    )
    return panel


def _names(panel: RegistersPanel) -> list[str]:
    return [panel._table.item(i, COL_NAME).text() for i in range(panel._table.rowCount())]


def _select_rows(panel: RegistersPanel, rows: list[int]) -> None:
    model = panel._table.model()
    selection = QItemSelection()
    last_col = panel._table.columnCount() - 1
    for row in rows:
        selection.select(model.index(row, 0), model.index(row, last_col))
    panel._table.selectionModel().select(
        selection,
        QItemSelectionModel.SelectionFlag.ClearAndSelect
        | QItemSelectionModel.SelectionFlag.Rows,
    )


def _selected_rows(panel: RegistersPanel) -> list[int]:
    return sorted({index.row() for index in panel._table.selectedIndexes()})


def _bitmask_panel() -> RegistersPanel:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_state(
        [{"name": "flags", "kind": "holding_registers", "address": 0, "count": 1,
          "value_names": {"0": "Running", "2": "Alarm"}, "bitmask": True}]
    )
    return panel


def _grouped_panel() -> RegistersPanel:
    """Панель с тремя holding-строками: @0, @1 (смежные) и @30 (далёкая)."""
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_bus_enabled(True)
    panel._add_row(RegisterRow(name="", kind="holding_registers", address=1, count=1))
    panel._add_row(RegisterRow(name="", kind="holding_registers", address=30, count=1))
    return panel


def test_column_widths_tolerate_garbage(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    header = panel._table.horizontalHeader()
    default_widths = [header.sectionSize(col) for col in range(header.count())]

    panel.set_options({"column_widths": "junk"})  # not a list: ignored
    panel.set_options({"column_widths": [None, "wide", True]})  # non-numbers: skip
    assert [header.sectionSize(col) for col in range(header.count())] == default_widths

    panel.set_options({"column_widths": [5, 10**9, 150]})  # clamped to 30..2000
    assert header.sectionSize(0) == 30
    assert header.sectionSize(1) == 2000
    assert header.sectionSize(2) == 150
    # a short list leaves the remaining columns at their current widths
    assert header.sectionSize(3) == default_widths[3]

    panel.set_options({"order": "ABCD"})  # missing key: widths untouched
    assert header.sectionSize(0) == 30


# --- column layout (movable, hide/show via header menu, persistence) ---------


def test_columns_movable_and_menu_excludes_control_columns(
    qapp: QApplication,
) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    header = panel._table.horizontalHeader()
    assert header.sectionsMovable()

    menu = panel._build_columns_menu()
    texts = [action.text() for action in menu.actions()]
    assert len(menu.actions()) == len(DATA_COLUMNS)
    assert all(texts)  # every listed column has a non-empty label
    assert tr("Name") in texts and tr("Format") in texts and tr("Trend") in texts
    # checkbox and delete-button columns (empty headers) are never listed
    assert "" not in texts


def test_header_menu_hides_and_shows_column(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    header = panel._table.horizontalHeader()

    menu = panel._build_columns_menu()
    action = next(a for a in menu.actions() if a.text() == tr("Format"))
    assert action.isChecked()
    action.trigger()
    assert header.isSectionHidden(COL_FORMAT)

    menu = panel._build_columns_menu()
    action = next(a for a in menu.actions() if a.text() == tr("Format"))
    assert not action.isChecked()
    action.trigger()
    assert not header.isSectionHidden(COL_FORMAT)


def test_last_visible_column_cannot_be_hidden(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    header = panel._table.horizontalHeader()
    for col in DATA_COLUMNS:
        if col != COL_NAME:
            header.setSectionHidden(col, True)

    menu = panel._build_columns_menu()
    checked = [action for action in menu.actions() if action.isChecked()]
    assert [action.text() for action in checked] == [tr("Name")]
    assert not checked[0].isEnabled()  # the last visible data column
    checked[0].trigger()  # a disabled action must not fire
    assert not header.isSectionHidden(COL_NAME)


def test_column_order_and_hidden_round_trip(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    header = panel._table.horizontalHeader()
    header.moveSection(header.visualIndex(COL_VALUE), 1)  # Value to the 2nd slot
    header.setSectionHidden(COL_TREND, True)
    header.setSectionHidden(COL_UNIT_ID, True)

    options = panel.options_state()
    assert options["column_order"][1] == "value"
    assert options["hidden_columns"] == ["unit_id", "trend"]  # in DATA_COLUMNS order

    fresh = RegistersPanel(itertools.count(100).__next__)
    fresh.set_options(options)
    fheader = fresh._table.horizontalHeader()
    assert fheader.logicalIndex(1) == COL_VALUE  # order applied by visual index
    assert fheader.isSectionHidden(COL_TREND)
    assert fheader.isSectionHidden(COL_UNIT_ID)
    assert not fheader.isSectionHidden(COL_NAME)
    # control columns are never hidden, even by state
    assert not fheader.isSectionHidden(COL_POLL_ENABLED)
    assert not fheader.isSectionHidden(COL_ACTIONS)
    fresh.close()
    fresh.deleteLater()


def test_column_layout_tolerates_garbage(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    header = panel._table.horizontalHeader()

    panel.set_options({"column_order": "junk"})  # not a list: ignored
    assert header.logicalIndex(0) == COL_POLL_ENABLED
    # unknown keys, dupes and non-strings are skipped; missing columns are
    # appended in their default order
    panel.set_options({"column_order": ["nope", 42, "value", "value"]})
    assert header.logicalIndex(0) == COL_VALUE
    assert sorted(header.logicalIndex(v) for v in range(header.count())) == list(
        range(header.count())
    )

    panel.set_options({"hidden_columns": "junk"})  # not a list: ignored
    panel.set_options({"hidden_columns": ["nope", "poll_enabled", "actions"]})
    assert not header.isSectionHidden(COL_POLL_ENABLED)
    assert not header.isSectionHidden(COL_ACTIONS)
    # hiding every data column at once keeps one visible
    panel.set_options({"hidden_columns": list(COLUMN_KEYS)})
    assert any(not header.isSectionHidden(col) for col in DATA_COLUMNS)


def test_column_widths_stay_bound_to_logical_columns(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    header = panel._table.horizontalHeader()
    header.resizeSection(COL_VALUE, 321)
    header.moveSection(header.visualIndex(COL_VALUE), 1)  # reorder after sizing

    options = panel.options_state()
    assert options["column_widths"][COL_VALUE] == 321  # stored per logical column

    fresh = RegistersPanel(itertools.count(100).__next__)
    fresh.set_options(options)
    fheader = fresh._table.horizontalHeader()
    assert fheader.logicalIndex(1) == COL_VALUE  # same visual layout
    assert fheader.sectionSize(COL_VALUE) == 321  # width follows the column
    fresh.close()
    fresh.deleteLater()


# --- quick value actions (hotkeys + context menu) ----------------------------


def test_quick_write_constants(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_bus_enabled(True)
    panel.set_state(
        [
            {"name": "c", "kind": "coils", "address": 0, "count": 4},
            {"name": "h", "kind": "holding_registers", "address": 0, "count": 1},
        ]
    )
    writes: list[tuple] = []
    panel.writeRequested.connect(lambda *args: writes.append(args))

    panel._table.setCurrentCell(0, COL_NAME)
    panel._write_constant_to_current_row(1)
    panel._write_constant_to_current_row(0)
    assert [args[3] for args in writes] == [[True], [False]]  # coil bits

    panel._table.setCurrentCell(1, COL_NAME)
    panel._write_constant_to_current_row(1)
    panel._write_constant_to_current_row(0)
    assert [args[3] for args in writes] == [[True], [False], [1], [0]]  # ints


def test_step_uses_last_raw_value(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_bus_enabled(True)
    writes: list[tuple] = []
    lines: list[str] = []
    panel.writeRequested.connect(lambda *args: writes.append(args))
    panel.logLine.connect(lines.append)
    panel._table.setCurrentCell(0, COL_NAME)

    panel._step_current_row(1)  # never read: hint, no write
    assert writes == []
    assert any("read the row" in line for line in lines)

    panel.handle_read_finished(_read_row(panel, 0), True, [5], "")
    panel._step_current_row(1)
    assert writes[-1][3] == [6]
    panel.handle_read_finished(_read_row(panel, 0), True, [6], "")
    panel._step_current_row(-1)
    assert writes[-1][3] == [5]
    panel.handle_read_finished(_read_row(panel, 0), True, [0], "")
    panel._step_current_row(-1)  # clamps at 0
    assert writes[-1][3] == [0]


def test_toggle_flips_bit_and_register(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_bus_enabled(True)
    panel.set_state(
        [
            {"name": "c", "kind": "coils", "address": 0, "count": 4},
            {"name": "h", "kind": "holding_registers", "address": 0, "count": 1},
        ]
    )
    writes: list[tuple] = []
    panel.writeRequested.connect(lambda *args: writes.append(args))

    panel._table.setCurrentCell(0, COL_NAME)
    panel.handle_read_finished(_read_row(panel, 0), True, [True], "")
    panel._toggle_current_row()
    assert writes[-1][3] == [False]
    panel.handle_read_finished(_read_row(panel, 0), True, [False], "")
    panel._toggle_current_row()
    assert writes[-1][3] == [True]

    panel._table.setCurrentCell(1, COL_NAME)
    panel.handle_read_finished(_read_row(panel, 1), True, [0], "")
    panel._toggle_current_row()
    assert writes[-1][3] == [1]  # 0 → 1
    panel.handle_read_finished(_read_row(panel, 1), True, [7], "")
    panel._toggle_current_row()
    assert writes[-1][3] == [0]  # nonzero → 0


def test_copy_value_to_clipboard(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_bus_enabled(True)
    panel.handle_read_finished(_read_row(panel, 0), True, [5], "")
    panel._table.setCurrentCell(0, COL_NAME)
    panel._copy_current_value()
    assert QGuiApplication.clipboard().text() == "5"


def test_actions_on_read_only_area_log_instead(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_bus_enabled(True)
    panel.set_state(
        [{"name": "i", "kind": "input_registers", "address": 0, "count": 1}]
    )
    writes: list[tuple] = []
    lines: list[str] = []
    panel.writeRequested.connect(lambda *args: writes.append(args))
    panel.logLine.connect(lines.append)
    panel._table.setCurrentCell(0, COL_NAME)

    panel._write_constant_to_current_row(1)
    panel._step_current_row(1)
    panel._toggle_current_row()
    assert writes == []
    assert sum("read-only" in line for line in lines) == 3


def test_shortcuts_fire_and_arrows_still_navigate(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_bus_enabled(True)
    panel.set_state(
        [{"name": "h", "kind": "holding_registers", "address": 0, "count": 1},
         {"name": "j", "kind": "holding_registers", "address": 1, "count": 1}]
    )
    writes: list[tuple] = []
    panel.writeRequested.connect(lambda *args: writes.append(args))
    panel.show()
    panel._table.setFocus()
    panel._table.setCurrentCell(0, COL_NAME)
    qapp.processEvents()  # let the focus settle or the shortcut map sees nothing

    panel.handle_read_finished(_read_row(panel, 0), True, [5], "")
    QTest.keyClick(panel._table, Qt.Key.Key_Equal, Qt.KeyboardModifier.ControlModifier)
    assert writes[-1][3] == [6]  # Ctrl+= increments ("Ctrl++" never matches =)

    QTest.keyClick(panel._table, Qt.Key.Key_0, Qt.KeyboardModifier.ControlModifier)
    assert writes[-1][3] == [0]  # Ctrl+0 wrote to the current row

    QTest.keyClick(panel._table, Qt.Key.Key_Down)  # plain arrows: navigation
    assert panel._table.currentRow() == 1
    assert len(writes) == 2  # no stray action fired
    panel.hide()


def test_ctrl_shift_r_reads_all(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_bus_enabled(True)
    panel.set_state(
        [{"name": "h", "kind": "holding_registers", "address": 0, "count": 1},
         {"name": "j", "kind": "holding_registers", "address": 1, "count": 1}]
    )
    reads: list[tuple] = []
    panel.readRequested.connect(lambda *args: reads.append(args))
    panel.show()
    panel._table.setFocus()
    panel._table.setCurrentCell(0, COL_NAME)
    qapp.processEvents()  # let the focus settle or the shortcut map sees nothing

    QTest.keyClick(panel._table, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
    assert len(reads) == 1  # Ctrl+R: only the current row
    # answer it: an unanswered read blocks re-reading that row
    panel.handle_read_finished(reads[0][0], True, [1], "")

    modifiers = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
    QTest.keyClick(panel._table, Qt.Key.Key_R, modifiers)
    assert len(reads) == 3  # Ctrl+Shift+R: every row once
    panel.hide()


def test_context_menu_shortcut_hints_enabled(qapp: QApplication) -> None:
    # macOS defaults AA_DontShowShortcutsInContextMenus to true (Apple HIG);
    # the app turns it off at startup. Menu rendering itself can't be
    # asserted offscreen — the attribute is what gates the hints.
    from modbus_connector.app import configure_qt

    configure_qt()
    assert not QApplication.testAttribute(
        Qt.ApplicationAttribute.AA_DontShowShortcutsInContextMenus
    )


def test_combo_popups_fit_their_items(qapp: QApplication) -> None:
    # stylesheet themes size the popup to the closed combo, clipping long
    # items; FitComboBox.resize the popup container to the longest item
    # (measured by font metrics — delegate size hints lie under stylesheets)
    panel = RegistersPanel(itertools.count(1).__next__)
    combo = panel._table.cellWidget(0, COL_TYPE)
    combo.showPopup()
    try:
        container = combo.view().window()
        fm = combo.view().fontMetrics()
        longest = max(fm.horizontalAdvance(combo.itemText(i)) for i in range(combo.count()))
        assert container.width() >= longest + 20  # the longest item fits
    finally:
        combo.hidePopup()


# --- manual row reordering -----------------------------------------------------


def test_move_single_row_and_boundary(qapp: QApplication) -> None:
    panel = _five_row_panel()
    assert (
        panel._table.selectionMode()
        == QAbstractItemView.SelectionMode.ExtendedSelection
    )
    tokens_before = [panel._token_at(i) for i in range(5)]

    _select_rows(panel, [0])
    panel._move_selected_rows(-1)  # already at the top: no-op
    assert _names(panel) == list("ABCDE")

    panel._move_selected_rows(1)
    assert _names(panel) == list("BACDE")
    assert panel._token_at(1) == tokens_before[0]  # the row kept its token
    assert _selected_rows(panel) == [1]  # the selection follows the row

    panel._move_selected_rows(-1)
    assert _names(panel) == list("ABCDE")
    assert _selected_rows(panel) == [0]


def test_move_batch_preserves_relative_order(qapp: QApplication) -> None:
    panel = _five_row_panel()
    _select_rows(panel, [1, 3])  # B and D
    panel._move_selected_rows(1)
    assert _names(panel) == list("ACBED")
    assert _selected_rows(panel) == [2, 4]  # B and D, still selected

    panel._move_selected_rows(-1)  # back
    assert _names(panel) == list("ABCDE")
    assert _selected_rows(panel) == [1, 3]


def test_move_batch_of_adjacent_rows(qapp: QApplication) -> None:
    panel = _five_row_panel()
    _select_rows(panel, [1, 2])  # B and C as one block
    panel._move_selected_rows(1)
    assert _names(panel) == list("ADBCE")
    panel._move_selected_rows(-1)
    assert _names(panel) == list("ABCDE")


def test_move_hotkey_and_plain_arrows(qapp: QApplication) -> None:
    panel = _five_row_panel()
    panel.show()
    panel._table.setFocus()
    _select_rows(panel, [0])
    qapp.processEvents()  # let the focus settle or the shortcut map sees nothing

    modifiers = Qt.KeyboardModifier.ControlModifier
    QTest.keyClick(panel._table, Qt.Key.Key_Down, modifiers)
    qapp.processEvents()  # the move is deferred out of the key event
    assert _names(panel) == list("BACDE")  # Ctrl+Down moved the row

    QTest.keyClick(panel._table, Qt.Key.Key_Down)  # plain arrow: cursor only
    assert _names(panel) == list("BACDE")
    panel.hide()


def test_sort_still_works_after_moves(qapp: QApplication) -> None:
    panel = _five_row_panel()
    _select_rows(panel, [4])  # E (address 4) to the top
    for _ in range(4):
        panel._move_selected_rows(-1)
    assert _names(panel) == list("EABCD")
    panel._sort_by_address()
    assert _names(panel) == list("ABCDE")


def test_help_button_opens_dialog(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QDialog, QTextBrowser

    panel = RegistersPanel(itertools.count(1).__next__)
    panel._help_button.click()
    dialog = next(
        widget
        for widget in QApplication.topLevelWidgets()
        if isinstance(widget, QDialog) and widget.windowTitle() == "Registers — Help"
    )
    text = dialog.findChild(QTextBrowser).toPlainText()
    assert "Ctrl+Shift+R" in text
    assert "Ctrl+Up" in text
    dialog.close()
    qapp.processEvents()  # WA_DeleteOnClose
    assert all(
        not (isinstance(widget, QDialog) and widget.windowTitle() == "Registers — Help")
        for widget in QApplication.topLevelWidgets()
    )


