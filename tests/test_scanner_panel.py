import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Qt needs system libraries (libEGL etc.) that headless Linux CI runners lack;
# skip the whole module there — it still runs on macOS/Windows CI and dev machines.
try:
    from PySide6.QtWidgets import QApplication  # noqa: E402
except ImportError as exc:
    pytest.skip(f"Qt system libraries not available: {exc}", allow_module_level=True)

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
