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
    from PySide6.QtWidgets import (  # noqa: E402
        QApplication,
        QCheckBox,
        QComboBox,
        QDialog,
        QPlainTextEdit,
        QToolButton,
    )
except ImportError as exc:
    pytest.skip(f"Qt system libraries not available: {exc}", allow_module_level=True)


from modbus_connector.bits_dialog import BitsDialog  # noqa: E402
from modbus_connector.models import RegisterRow  # noqa: E402
from modbus_connector.registers_panel import (  # noqa: E402
    COL_NAME,
    COL_NEW_VALUE,
    COL_POLL,
    COL_POLL_ENABLED,
    COL_VALUE,
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


def test_write_accepts_display_format_numbers(qapp: QApplication) -> None:
    # ввод "0.1" в строку f32 кодируется в пару регистров, а не parse error
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_bus_enabled(True)
    panel.set_state(
        [{"name": "h", "kind": "holding_registers", "address": 5,
          "count": 2, "format": "f32"}]
    )
    writes: list[tuple] = []
    panel.writeRequested.connect(lambda *args: writes.append(args))
    panel._table.item(0, COL_NEW_VALUE).setText("0.1")
    assert writes, "write must be emitted"
    values = writes[-1][3]
    assert len(values) == 2
    from modbus_connector.models import decode_register_values

    assert abs(decode_register_values(values, "f32")[0] - 0.1) < 1e-6
    # dec-строка по-прежнему требует сырые целые
    panel.set_state([{"name": "d", "kind": "holding_registers", "address": 0, "count": 1}])
    lines: list[str] = []
    panel.logLine.connect(lines.append)
    writes.clear()
    panel._table.item(0, COL_NEW_VALUE).setText("0.1")
    assert writes == []
    assert any("parse error" in line for line in lines)


def test_write_accepts_text_for_ascii(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_bus_enabled(True)
    panel.set_state(
        [{"name": "dev", "kind": "holding_registers", "address": 16,
          "count": 4, "format": "ascii"}]
    )
    writes: list[tuple] = []
    panel.writeRequested.connect(lambda *args: writes.append(args))
    panel._table.item(0, COL_NEW_VALUE).setText("qwe")
    assert writes, "write must be emitted"
    assert writes[-1][3] == [0x7177, 0x6500, 0, 0]


def test_write_accepts_text_for_ascii1(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_bus_enabled(True)
    panel.set_state(
        [{"name": "model", "kind": "holding_registers", "address": 200,
          "count": 20, "format": "ascii1"}]
    )
    writes: list[tuple] = []
    panel.writeRequested.connect(lambda *args: writes.append(args))
    panel._table.item(0, COL_NEW_VALUE).setText("WBMSW4")
    assert writes, "write must be emitted"
    assert writes[-1][3] == [ord(c) for c in "WBMSW4"] + [0] * 14


def test_value_names_state_roundtrip(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_state(
        [
            {"name": "pump", "kind": "holding_registers", "address": 0, "count": 1,
             "value_names": {"0": "Stopped", "2": "Pump running"}},
            {"name": "plain", "kind": "holding_registers", "address": 1, "count": 1},
            {"kind": "junk", "address": 2, "count": 1, "value_names": {"x": 1}},  # tolerant
        ]
    )
    state = panel.state()
    assert state[0]["value_names"] == {"0": "Stopped", "2": "Pump running"}
    assert state[1]["value_names"] == {}
    assert state[2]["value_names"] == {}


def test_value_names_display_and_combo_write(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_bus_enabled(True)
    panel.set_state(
        [{"name": "pump", "kind": "holding_registers", "address": 0, "count": 1,
          "value_names": {"0": "Stopped", "2": "Pump running"}}]
    )
    combo = panel._table.cellWidget(0, COL_NEW_VALUE)
    assert combo is not None and not combo.isHidden()  # names → комбо
    assert [combo.itemText(i) for i in range(combo.count())] == [
        "0 = Stopped", "2 = Pump running",
    ]
    assert combo.currentIndex() == -1  # placeholder

    request_id = _read_row(panel, 0)
    panel.handle_read_finished(request_id, True, [2], "")
    assert panel._table.item(0, COL_VALUE).text() == "Pump running (2)"
    request_id = _read_row(panel, 0)
    panel.handle_read_finished(request_id, True, [5], "")
    assert panel._table.item(0, COL_VALUE).text() == "5"  # вне names — как раньше

    writes: list[tuple] = []
    panel.writeRequested.connect(lambda *args: writes.append(args))
    combo.activated[int].emit(combo.findData(2))
    assert [w[3] for w in writes] == [[2]]
    assert combo.currentIndex() == -1  # сброс после записи…
    combo.activated[int].emit(combo.findData(2))  # …повторный выбор пишет снова
    assert [w[3] for w in writes] == [[2], [2]]


def test_value_names_hex_display_and_combo_write(qapp: QApplication) -> None:
    # enum работает и для hex-строк: имя с десятичным числом, комбо пишет число
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_bus_enabled(True)
    panel.set_state(
        [{"name": "mode", "kind": "holding_registers", "address": 0, "count": 1,
          "format": "hex", "value_names": {"0": "Stopped", "2": "Pump running"}}]
    )
    combo = panel._table.cellWidget(0, COL_NEW_VALUE)
    assert combo is not None and not combo.isHidden()  # names → комбо и для hex

    request_id = _read_row(panel, 0)
    panel.handle_read_finished(request_id, True, [2], "")
    assert panel._table.item(0, COL_VALUE).text() == "Pump running (2)"
    request_id = _read_row(panel, 0)
    panel.handle_read_finished(request_id, True, [5], "")
    assert panel._table.item(0, COL_VALUE).text() == "0x0005"  # вне names — как раньше

    writes: list[tuple] = []
    panel.writeRequested.connect(lambda *args: writes.append(args))
    combo.activated[int].emit(combo.findData(2))
    assert [w[3] for w in writes] == [[2]]
    assert combo.currentIndex() == -1


def test_value_names_combo_silent_without_bus(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)  # шина выключена
    panel.set_state(
        [{"kind": "holding_registers", "address": 0, "count": 1,
          "value_names": {"1": "On"}}]
    )
    writes: list[tuple] = []
    panel.writeRequested.connect(lambda *args: writes.append(args))
    combo = panel._table.cellWidget(0, COL_NEW_VALUE)
    combo.activated[int].emit(combo.findData(1))
    assert writes == []  # молча, как текстовое New value без подключения
    assert combo.currentIndex() == -1


def test_value_names_removed_returns_text_cell(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_bus_enabled(True)
    panel.set_state(
        [{"kind": "holding_registers", "address": 0, "count": 1,
          "value_names": {"1": "On"}}]
    )
    token = panel._token_at(0)
    request_id = _read_row(panel, 0)
    panel.handle_read_finished(request_id, True, [1], "")
    assert panel._table.item(0, COL_VALUE).text() == "On (1)"

    panel._row_display[token].value_names = {}
    panel._sync_value_names_combo(token)
    combo = panel._table.cellWidget(0, COL_NEW_VALUE)
    assert combo is not None and combo.isHidden()  # комбо скрыто, ячейка текстовая
    assert panel._table.item(0, COL_VALUE).text() == "1"  # отображение без имени
    writes: list[tuple] = []
    panel.writeRequested.connect(lambda *args: writes.append(args))
    panel._table.item(0, COL_NEW_VALUE).setText("5")
    assert [w[3] for w in writes] == [[5]]  # обычный путь записи работает


def test_value_names_coils(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_bus_enabled(True)
    panel.set_state(
        [{"kind": "coils", "address": 0, "count": 1,
          "value_names": {"0": "Off", "1": "On"}}]
    )
    request_id = _read_row(panel, 0)
    panel.handle_read_finished(request_id, True, [True], "")
    assert panel._table.item(0, COL_VALUE).text() == "On (1)"
    writes: list[tuple] = []
    panel.writeRequested.connect(lambda *args: writes.append(args))
    combo = panel._table.cellWidget(0, COL_NEW_VALUE)
    combo.activated[int].emit(combo.findData(0))
    assert [w[3] for w in writes] == [[False]]  # coils пишутся bool


def test_value_names_display_dialog_editor(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel._on_display_settings()
    dialog = panel._display_dialog
    assert dialog is not None
    edit = dialog.findChild(QPlainTextEdit)
    assert edit is not None
    edit.setPlainText("3=Hi\njunk line")  # применяется на лету, мусор пропускается
    token = panel._token_at(0)
    assert panel._row_display[token].value_names == {3: "Hi"}
    assert panel._table.cellWidget(0, COL_NEW_VALUE) is not None  # комбо появилось
    edit.setPlainText("")
    assert panel._row_display[token].value_names == {}
    assert panel._table.cellWidget(0, COL_NEW_VALUE).isHidden()  # и скрылось
    dialog.close()


def test_bitmask_state_roundtrip(qapp: QApplication) -> None:
    panel = _bitmask_panel()
    panel.set_state(
        panel.state()
        + [{"name": "plain", "kind": "holding_registers", "address": 1, "count": 1}]
    )
    state = panel.state()
    assert state[0]["bitmask"] is True
    assert state[0]["value_names"] == {"0": "Running", "2": "Alarm"}
    assert state[1]["bitmask"] is False  # ключ отсутствовал — default False


def test_bitmask_display_and_button(qapp: QApplication) -> None:
    panel = _bitmask_panel()
    button = panel._table.cellWidget(0, COL_NEW_VALUE)
    assert isinstance(button, QToolButton) and not button.isHidden()  # кнопка, не комбо
    request_id = _read_row(panel, 0)
    panel.handle_read_finished(request_id, True, [0x0005], "")
    assert panel._table.item(0, COL_VALUE).text() == "Running, Alarm (0000 0000 0000 0101)"
    # полный текст — в tooltip на случай обрезки, сводка — на кнопке
    assert panel._table.item(0, COL_VALUE).toolTip() == "Running, Alarm (0000 0000 0000 0101)"
    assert button.text() == "Running, Alarm (0000 0000 0000 0101)"
    request_id = _read_row(panel, 0)
    panel.handle_read_finished(request_id, True, [0], "")
    assert panel._table.item(0, COL_VALUE).text() == "0000 0000 0000 0000"  # нет установленных
    assert button.text() == "0000 0000 0000 0000"


def test_bitmask_empty_names_shows_bit_numbers(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_state(
        [{"kind": "holding_registers", "address": 0, "count": 1, "bitmask": True}]
    )
    assert isinstance(panel._table.cellWidget(0, COL_NEW_VALUE), QToolButton)
    request_id = _read_row(panel, 0)
    panel.handle_read_finished(request_id, True, [0x00A1], "")
    assert panel._table.item(0, COL_VALUE).text() == "b0, b5, b7 (0000 0000 1010 0001)"


def test_bitmask_button_dialog_writes(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel = _bitmask_panel()
    panel.set_bus_enabled(True)
    request_id = _read_row(panel, 0)
    panel.handle_read_finished(request_id, True, [1], "")  # текущее значение — bit0

    def fake_exec(dialog: BitsDialog) -> QDialog.DialogCode:
        dialog._boxes[1].setChecked(True)  # поверх bit0 из значения — bit1
        dialog._boxes[2].setChecked(True)  # и bit2
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(BitsDialog, "exec", fake_exec)
    writes: list[tuple] = []
    panel.writeRequested.connect(lambda *args: writes.append(args))
    panel._table.cellWidget(0, COL_NEW_VALUE).click()
    assert [w[3] for w in writes] == [[0b111]]  # bits_to_value отмеченных


def test_bitmask_button_silent_without_bus(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel = _bitmask_panel()  # шина выключена
    monkeypatch.setattr(
        BitsDialog, "exec", lambda self: QDialog.DialogCode.Accepted
    )
    writes: list[tuple] = []
    panel.writeRequested.connect(lambda *args: writes.append(args))
    panel._table.cellWidget(0, COL_NEW_VALUE).click()
    assert writes == []  # молча, как текстовый ввод без подключения


def test_bitmask_dialog_checkbox_live_toggle(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel._on_display_settings()
    dialog = panel._display_dialog
    assert dialog is not None
    edit = dialog.findChild(QPlainTextEdit)
    edit.setPlainText("0=Running")
    box = dialog.findChild(QCheckBox)
    assert box is not None and not box.isChecked()
    box.setChecked(True)  # live-применение к выбранной строке
    token = panel._token_at(0)
    assert panel._row_display[token].bitmask is True
    button = panel._table.cellWidget(0, COL_NEW_VALUE)
    assert isinstance(button, QToolButton) and not button.isHidden()
    box.setChecked(False)
    assert panel._row_display[token].bitmask is False
    combo = panel._table.cellWidget(0, COL_NEW_VALUE)
    assert isinstance(combo, QComboBox) and not combo.isHidden()  # обратно enum-комбо
    dialog.close()


def test_bitmask_coils_unaffected(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_bus_enabled(True)
    panel.set_state(
        [{"kind": "coils", "address": 0, "count": 1, "bitmask": True,
          "value_names": {"0": "Off", "1": "On"}}]
    )
    widget = panel._table.cellWidget(0, COL_NEW_VALUE)
    assert not isinstance(widget, QToolButton)  # bitmask только для регистров
    request_id = _read_row(panel, 0)
    panel.handle_read_finished(request_id, True, [True], "")
    assert panel._table.item(0, COL_VALUE).text() == "On (1)"  # enum, как раньше



def test_group_reads_off_by_default(qapp: QApplication) -> None:
    panel = _grouped_panel()
    assert not panel._group_reads_button.isChecked()
    reads: list[tuple] = []
    panel.readRequested.connect(lambda *args: reads.append(args))
    panel._poll_global_rows()
    assert len(reads) == 3  # прежнее поведение: по запросу на строку


def test_grouped_poll_tick_merges_adjacent_rows(qapp: QApplication) -> None:
    panel = _grouped_panel()
    panel._group_reads_button.setChecked(True)
    reads: list[tuple] = []
    panel.readRequested.connect(lambda *args: reads.append(args))
    panel._poll_global_rows()
    # @0 и @1 слились в один запрос, @30 — отдельный (зазор > 8)
    assert len(reads) == 2
    request_id, unit, row = reads[0]
    assert unit == 1  # глобальный unit панели
    assert (row.kind, row.address, row.count) == ("holding_registers", 0, 2)
    assert (reads[1][2].address, reads[1][2].count) == (30, 1)
    # раздача values членам плана: каждая строка получает свой срез
    panel.handle_read_finished(request_id, True, [10, 20], "")
    assert panel._table.item(0, COL_VALUE).text() == "10"
    assert panel._table.item(1, COL_VALUE).text() == "20"
    panel.handle_read_finished(reads[1][0], True, [99], "")
    assert panel._table.item(2, COL_VALUE).text() == "99"


def test_grouped_read_all_merges_too(qapp: QApplication) -> None:
    panel = _grouped_panel()
    panel._group_reads_button.setChecked(True)
    reads: list[tuple] = []
    panel.readRequested.connect(lambda *args: reads.append(args))
    panel.read_all()
    assert len(reads) == 2
    assert (reads[0][2].address, reads[0][2].count) == (0, 2)


def test_grouped_read_error_falls_back_to_per_row(qapp: QApplication) -> None:
    panel = _grouped_panel()
    panel._group_reads_button.setChecked(True)
    reads: list[tuple] = []
    panel.readRequested.connect(lambda *args: reads.append(args))
    panel._poll_global_rows()
    assert len(reads) == 2
    request_id = reads[0][0]
    panel.handle_read_finished(request_id, False, [], "Illegal Data Address")
    # фолбэк: члены рассыпавшегося плана перечитаны поштучно
    assert len(reads) == 4
    fallback_ids = [reads[2][0], reads[3][0]]
    assert [reads[2][2].address, reads[3][2].address] == [0, 1]
    # повторные запросы — обычные per-row чтения (токены, не планы)
    for rid in fallback_ids:
        assert isinstance(panel._pending_reads[rid], int)
    panel.handle_read_finished(fallback_ids[0], True, [5], "")
    panel.handle_read_finished(fallback_ids[1], False, [], "boom")
    assert panel._table.item(0, COL_VALUE).text() == "5"
    assert panel._table.item(1, COL_VALUE).text() == "✗ boom"
    # и после фолбэка ничего нового не уходит: зацикливания нет
    assert len(reads) == 4


def test_group_reads_state_roundtrip(qapp: QApplication) -> None:
    panel = _grouped_panel()
    assert panel.options_state()["group_reads"] is False
    panel._group_reads_button.setChecked(True)
    options = panel.options_state()
    assert options["group_reads"] is True
    fresh = RegistersPanel(itertools.count(1).__next__)
    fresh.set_options(options)
    assert fresh._group_reads_button.isChecked()
    fresh.set_options({"group_reads": "junk"})  # не bool: игнорируется
    assert fresh._group_reads_button.isChecked()
    fresh.deleteLater()


def test_grouped_tick_skips_per_row_interval_rows(qapp: QApplication) -> None:
    panel = _grouped_panel()
    panel._group_reads_button.setChecked(True)
    # у строки @30 свой интервал — в объединённый тик она не входит
    panel._table.item(2, COL_POLL).setText("500")
    reads: list[tuple] = []
    panel.readRequested.connect(lambda *args: reads.append(args))
    panel._poll_global_rows()
    assert len(reads) == 1
    assert (reads[0][2].address, reads[0][2].count) == (0, 2)


def test_grouped_tick_splits_by_unit(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    panel.set_bus_enabled(True)
    panel._add_row(
        RegisterRow(name="", kind="holding_registers", address=1, count=1, unit_id=2)
    )
    panel._group_reads_button.setChecked(True)
    reads: list[tuple] = []
    panel.readRequested.connect(lambda *args: reads.append(args))
    panel._poll_global_rows()
    # адреса смежные, но unit разный — два запроса
    assert len(reads) == 2
    assert {read[1] for read in reads} == {1, 2}
