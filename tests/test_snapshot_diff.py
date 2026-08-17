import itertools
import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Qt needs system libraries (libEGL etc.) that headless Linux CI runners lack;
# skip the whole module there — it still runs on macOS/Windows CI and dev machines.
try:
    from PySide6.QtCore import Qt  # noqa: E402
    from PySide6.QtWidgets import QApplication, QToolButton  # noqa: E402
except ImportError as exc:
    pytest.skip(f"Qt system libraries not available: {exc}", allow_module_level=True)

from modbus_connector import theme  # noqa: E402
from modbus_connector.i18n import tr  # noqa: E402
from modbus_connector.registers_panel import (  # noqa: E402
    COL_ACTIONS,
    COL_NAME,
    RegistersPanel,
)
from modbus_connector.snapshot_dialog import SnapshotDiffDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _destroy_panels(qapp: QApplication) -> Iterator[None]:
    yield
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, RegistersPanel):
            widget.close()
            widget.deleteLater()
    qapp.processEvents()


def _make_panel() -> RegistersPanel:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_bus_enabled(True)  # reads require a connection
    return panel


def _read_row(panel: RegistersPanel, index: int, values: list) -> None:
    """Эмулировать успешное чтение строки: запрос + ответ worker'а."""
    reads: list[tuple] = []
    panel.readRequested.connect(lambda *args: reads.append(args))
    panel._read_table_row(index)
    assert reads, "the read was not issued"
    panel.handle_read_finished(reads[-1][0], True, list(values), "")


def test_snapshot_and_diff_buttons(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)  # bus disabled
    assert panel._snapshot_button.isEnabled()  # local control, no bus gate
    assert not panel._diff_button.isEnabled()  # no snapshot yet
    lines: list[str] = []
    panel.logLine.connect(lines.append)
    panel.take_snapshot()
    assert panel._diff_button.isEnabled()
    assert lines[-1] == tr("Snapshot taken: {count} rows", count=1)


def test_diff_highlights_changed_rows(qapp: QApplication) -> None:
    theme.apply_theme("light")  # the diff color is theme-dependent: pin it
    try:
        panel = _make_panel()
        panel._add_row()
        _read_row(panel, 0, [10])
        _read_row(panel, 1, [20])
        panel.take_snapshot()
        _read_row(panel, 1, [21])  # only the second row changed
        panel._on_diff()
        dialog = panel._diff_dialog
        assert isinstance(dialog, SnapshotDiffDialog)
        table = dialog._table
        assert table.rowCount() == 2
        unchanged = table.item(0, 3)
        assert unchanged is not None
        assert unchanged.text() == "10"
        assert unchanged.background().style() == Qt.BrushStyle.NoBrush
        for col in range(table.columnCount()):
            item = table.item(1, col)
            assert item is not None
            assert item.background().color() == theme.diff_color()
        assert table.item(1, 3).text() == "20"  # snapshot column
        assert table.item(1, 4).text() == "21"  # current column
    finally:
        theme.apply_theme("system")


def test_only_differences_filter(qapp: QApplication) -> None:
    panel = _make_panel()
    panel._add_row()
    _read_row(panel, 0, [10])
    _read_row(panel, 1, [20])
    panel.take_snapshot()
    _read_row(panel, 1, [21])
    panel._on_diff()
    dialog = panel._diff_dialog
    assert dialog is not None
    dialog._only_diffs.setChecked(True)
    assert dialog._table.isRowHidden(0)  # unchanged row filtered out
    assert not dialog._table.isRowHidden(1)
    dialog._only_diffs.setChecked(False)
    assert not dialog._table.isRowHidden(0)


def test_rows_without_data(qapp: QApplication) -> None:
    panel = _make_panel()
    panel.take_snapshot()  # nothing read yet: snapshot value is None
    _info, rows = panel.snapshot_diff_data()
    assert len(rows) == 1
    assert not rows[0].changed  # None vs None: no diff
    assert rows[0].snapshot_text == ""
    _read_row(panel, 0, [5])
    _info, rows = panel.snapshot_diff_data()
    assert rows[0].changed  # None vs data: a diff
    assert rows[0].snapshot_text == ""
    assert rows[0].current_text == "5"


def test_refresh_button_pulls_current_values(qapp: QApplication) -> None:
    panel = _make_panel()
    _read_row(panel, 0, [10])
    panel.take_snapshot()
    panel._on_diff()
    dialog = panel._diff_dialog
    assert dialog is not None
    assert dialog._table.item(0, 3).background().style() == Qt.BrushStyle.NoBrush
    _read_row(panel, 0, [11])  # the user did another Read all
    dialog._refresh_button.click()
    assert dialog._table.item(0, 3).background().color() == theme.diff_color()
    assert dialog._table.item(0, 4).text() == "11"


def test_take_new_snapshot_button(qapp: QApplication) -> None:
    panel = _make_panel()
    _read_row(panel, 0, [10])
    panel.take_snapshot()
    _read_row(panel, 0, [11])
    panel._on_diff()
    dialog = panel._diff_dialog
    assert dialog is not None
    assert dialog._table.item(0, 3).background().color() == theme.diff_color()
    dialog._new_snapshot_button.click()  # accept current values as the baseline
    assert dialog._table.item(0, 3).text() == "11"
    assert dialog._table.item(0, 3).background().style() == Qt.BrushStyle.NoBrush
    _info, rows = panel.snapshot_diff_data()
    assert not rows[0].changed


def test_resnapshot_overwrites(qapp: QApplication) -> None:
    panel = _make_panel()
    _read_row(panel, 0, [10])
    panel.take_snapshot()
    _read_row(panel, 0, [11])
    _info, rows = panel.snapshot_diff_data()
    assert rows[0].changed
    panel.take_snapshot()  # a second Snapshot replaces the first one
    _info, rows = panel.snapshot_diff_data()
    assert not rows[0].changed
    assert rows[0].snapshot_text == "11"


def test_removed_row_is_marked(qapp: QApplication) -> None:
    panel = _make_panel()
    panel._add_row()
    name_item = panel._table.item(1, COL_NAME)
    assert name_item is not None
    name_item.setText("temp")
    _read_row(panel, 0, [10])
    _read_row(panel, 1, [20])
    panel.take_snapshot()
    actions = panel._table.cellWidget(1, COL_ACTIONS)  # delete the second row
    delete_button = actions.findChild(QToolButton)
    assert delete_button is not None
    delete_button.click()
    assert panel._table.rowCount() == 1
    _info, rows = panel.snapshot_diff_data()
    assert len(rows) == 2  # the deleted row stays in the diff
    removed = rows[1]
    assert removed.removed
    assert removed.changed
    assert removed.name == "temp"
    assert removed.snapshot_text == "20"
    assert removed.current_text == tr("(removed)")


def test_row_added_after_snapshot(qapp: QApplication) -> None:
    panel = _make_panel()
    _read_row(panel, 0, [10])
    panel.take_snapshot()
    panel._add_row()  # added after the snapshot: no baseline (None)
    _info, rows = panel.snapshot_diff_data()
    assert len(rows) == 2
    assert not rows[0].changed
    assert not rows[1].changed  # None vs None
    _read_row(panel, 1, [7])
    _info, rows = panel.snapshot_diff_data()
    assert rows[1].changed  # no baseline vs data
    assert rows[1].snapshot_text == ""
