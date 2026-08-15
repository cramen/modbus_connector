import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Qt needs system libraries (libEGL etc.) that headless Linux CI runners lack;
# skip the whole module there — it still runs on macOS/Windows CI and dev machines.
try:
    from PySide6.QtCore import QEvent, Qt  # noqa: E402
    from PySide6.QtWidgets import QApplication  # noqa: E402
except ImportError as exc:
    pytest.skip(f"Qt system libraries not available: {exc}", allow_module_level=True)

from PySide6.QtGui import QKeyEvent  # noqa: E402

from modbus_connector.scanner_panel import COL_ADDRESS, COL_COUNT, ScannerPanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_address_scan_section_is_called_registers_scan(qapp: QApplication) -> None:
    panel = ScannerPanel()
    assert panel._addr_section_label.text() == "Registers scan:"


def test_probe_cells_are_plain_text_and_parse_hex(qapp: QApplication) -> None:
    panel = ScannerPanel()
    address_item = panel._probes_table.item(0, COL_ADDRESS)
    assert address_item is not None  # a plain item, not a spinbox widget
    assert panel._probes_table.cellWidget(0, COL_ADDRESS) is None
    address_item.setText("0x10")
    count_item = panel._probes_table.item(0, COL_COUNT)
    assert count_item is not None
    count_item.setText("4")
    probe = panel._probes()[0]
    assert probe.address == 16
    assert probe.count == 4


def test_invalid_probe_rows_are_skipped(qapp: QApplication) -> None:
    panel = ScannerPanel()
    assert len(panel._probes()) == 3  # default probes
    address_item = panel._probes_table.item(0, COL_ADDRESS)
    assert address_item is not None
    address_item.setText("junk")
    count_item = panel._probes_table.item(1, COL_COUNT)
    assert count_item is not None
    count_item.setText("999")  # out of range
    probes = panel._probes()
    assert len(probes) == 1
    assert probes[0].kind == "coils"  # only the untouched default row remains


def test_scanner_state_roundtrip(qapp: QApplication) -> None:
    panel = ScannerPanel()
    panel.set_state(
        {
            "start": 3,
            "end": 9,
            "probes": [
                {"kind": "input_registers", "address": 16, "count": 2},
                {"kind": "coils", "address": 0, "count": 8},
            ],
            "addr_unit": 7,
            "addr_from": 10,
            "addr_to": 20,
        }
    )
    state = panel.state()
    assert state["start"] == 3
    assert state["end"] == 9
    assert state["probes"] == [
        {"kind": "input_registers", "address": 16, "count": 2},
        {"kind": "coils", "address": 0, "count": 8},
    ]
    assert state["addr_unit"] == 7
    assert state["addr_from"] == 10
    assert state["addr_to"] == 20


def _scan_addresses(panel: ScannerPanel, addresses: list[int]) -> None:
    panel._addr_unit.setValue(3)
    panel._addr_kind.setCurrentText("holding_registers")
    panel._on_addr_start()  # stores the scanned unit/kind, clears old hits
    for address in addresses:
        panel.handle_addr_scan_hit(address)


def test_add_rows_button_gating(qapp: QApplication) -> None:
    panel = ScannerPanel()
    assert not panel._add_rows_button.isEnabled()  # no bus, no results
    panel.set_bus_enabled(True)
    assert not panel._add_rows_button.isEnabled()  # nothing found yet
    _scan_addresses(panel, [10])
    assert panel._add_rows_button.isEnabled()  # a hit arrived
    panel.set_bus_enabled(False)
    assert not panel._add_rows_button.isEnabled()


def test_add_rows_skips_duplicates(qapp: QApplication) -> None:
    import itertools

    from modbus_connector.registers_panel import RegistersPanel

    registers = RegistersPanel(itertools.count(1).__next__)
    registers.set_state(
        [{"name": "x", "kind": "holding_registers", "address": 5, "count": 1,
          "unit_id": "9"}]  # a different unit id: still a duplicate (kind+address)
    )
    lines: list[str] = []
    registers.logLine.connect(lines.append)

    scanner = ScannerPanel()
    scanner.rowsAddRequested.connect(registers.add_rows)
    scanner.set_bus_enabled(True)
    _scan_addresses(scanner, [5, 6, 7])  # 5 is already in the table (unit 9)
    scanner._add_rows_button.click()

    assert registers._table.rowCount() == 3  # 6 and 7 added, 5 skipped
    state = registers.state()
    assert [entry["address"] for entry in state] == [5, 6, 7]
    assert [entry["unit_id"] for entry in state] == ["9", "3", "3"]
    assert [entry["kind"] for entry in state] == ["holding_registers"] * 3
    assert any("skipped 1 duplicates" in line for line in lines)


def test_device_id_button_gating(qapp: QApplication) -> None:
    panel = ScannerPanel()
    panel.handle_scan_hit(7, [0])
    panel.set_bus_enabled(True)
    assert not panel._device_id_button.isEnabled()  # nothing selected
    panel._results.setCurrentRow(0)
    assert panel._device_id_button.isEnabled()
    panel.set_bus_enabled(False)
    assert not panel._device_id_button.isEnabled()


def test_device_id_flow(qapp: QApplication) -> None:
    panel = ScannerPanel()
    panel.set_bus_enabled(True)
    panel.handle_scan_hit(7, [0])
    panel._results.setCurrentRow(0)
    emitted: list[tuple] = []
    panel.deviceIdRequested.connect(lambda *args: emitted.append(args))

    panel._on_device_id_clicked()
    assert len(emitted) == 1
    request_id, unit = emitted[0]
    assert unit == 7
    assert not panel._device_id_button.isEnabled()  # a query is in flight

    panel.handle_device_id_finished(request_id + 1, True, {0: "x"}, "")  # wrong id
    assert panel._device_id_list is not None
    assert panel._device_id_list.item(0).text() == "Reading…"  # ignored

    panel.handle_device_id_finished(
        request_id, True, {0: "acme", 1: "PLC-42", 2: "1.0"}, ""
    )
    items = [
        panel._device_id_list.item(row).text()
        for row in range(panel._device_id_list.count())
    ]
    assert items == ["VendorName: acme", "ProductCode: PLC-42",
                     "MajorMinorRevision: 1.0"]
    assert panel._device_id_button.isEnabled()  # answered: re-armed
    panel._device_id_list.window().close()  # the non-modal dialog
    assert panel._device_id_request == -1


def test_add_selected_only(qapp: QApplication) -> None:
    import itertools

    from modbus_connector.registers_panel import RegistersPanel

    registers = RegistersPanel(itertools.count(1).__next__)
    registers.set_state(
        [{"name": "x", "kind": "holding_registers", "address": 5, "count": 1,
          "unit_id": "3"}]
    )
    scanner = ScannerPanel()
    scanner.rowsAddRequested.connect(registers.add_rows)
    scanner.set_bus_enabled(True)
    _scan_addresses(scanner, [5, 6, 7])
    scanner._addr_results.item(1).setCheckState(Qt.CheckState.Unchecked)  # skip 6
    scanner._add_rows_button.click()

    assert [entry["address"] for entry in registers.state()] == [5, 7]


def test_none_disables_the_button(qapp: QApplication) -> None:
    panel = ScannerPanel()
    panel.set_bus_enabled(True)
    _scan_addresses(panel, [10, 11])
    assert panel._add_rows_button.isEnabled()
    panel._addr_none_button.click()
    assert not panel._add_rows_button.isEnabled()  # zero checked
    panel._addr_all_button.click()
    assert panel._add_rows_button.isEnabled()
    assert panel._checked_hits() == [10, 11]


def test_space_toggles_hit_checkbox(qapp: QApplication) -> None:
    panel = ScannerPanel()
    _scan_addresses(panel, [10])
    results = panel._addr_results
    results.setCurrentRow(0)
    event = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier
    )
    QApplication.sendEvent(results.viewport(), event)
    assert results.item(0).checkState() == Qt.CheckState.Unchecked
