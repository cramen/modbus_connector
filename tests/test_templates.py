import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from modbus_connector.templates import (  # noqa: E402
    TemplateInfo,
    list_templates,
    load_template,
    parse_template,
)


def test_list_templates_finds_sdm120() -> None:
    templates = list_templates()
    sdm120 = [t for t in templates if t.resource == "Eastron/SDM120.json"]
    assert len(sdm120) == 1
    info = sdm120[0]
    assert info.manufacturer == "Eastron"
    assert info.name == "Eastron SDM120"
    assert info.description


def test_list_templates_sorted_case_insensitive() -> None:
    templates = list_templates()
    keys = [(t.manufacturer.lower(), t.name.lower()) for t in templates]
    assert keys == sorted(keys)


def test_load_template_by_info_and_key() -> None:
    info = next(t for t in list_templates() if t.resource == "Eastron/SDM120.json")
    by_info = load_template(info)
    by_key = load_template("Eastron/SDM120")
    assert by_info == by_key
    assert by_info["name"] == "Eastron SDM120"
    connection = by_info["connection"]
    assert connection["type"] == "RTU"
    assert connection["rtu_baud"] == "2400"
    assert connection["unit"] == 1
    registers = by_info["registers"]
    assert isinstance(registers, list) and len(registers) == 13
    assert all(row["kind"] == "input_registers" for row in registers)


def test_load_template_unknown_key() -> None:
    with pytest.raises(ValueError, match="not found"):
        load_template("Eastron/NoSuchDevice")


def test_parse_template_tolerates_broken_files(caplog: pytest.LogCaptureFixture) -> None:
    assert parse_template("{not json", "broken.json") is None
    assert parse_template('["not", "an", "object"]', "list.json") is None
    assert parse_template('{"connection": {}, "registers": []}', "noname.json") is None
    assert parse_template('{"name": "x", "registers": []}', "noconn.json") is None
    assert parse_template('{"name": "x", "connection": {}, "registers": {}}') is None
    assert "broken.json" in caplog.text


def test_template_info_defaults() -> None:
    info = TemplateInfo(name="n", manufacturer="m", resource="m/n.json")
    assert info.description == ""


# --- Qt round-trip: шаблон проходит через RegistersPanel.set_state ---

try:
    from PySide6.QtWidgets import QApplication  # noqa: E402
except ImportError as exc:
    pytest.skip(f"Qt system libraries not available: {exc}", allow_module_level=True)

from modbus_connector.registers_panel import RegistersPanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_template_registers_roundtrip(qapp: QApplication) -> None:
    state = load_template("Eastron/SDM120")
    panel = RegistersPanel(lambda: 1)
    try:
        panel.set_state(state["registers"])
        rows = panel.state()
        assert len(rows) == 13
        voltage = rows[0]
        assert voltage["name"] == "Voltage"
        assert voltage["kind"] == "input_registers"
        assert voltage["address"] == 0
        assert voltage["count"] == 2
        assert voltage["format"] == "f32"
        assert voltage["unit"] == "V"
        assert rows[-2]["name"] == "Total active energy"
        assert rows[-2]["address"] == 342
        assert rows[-1]["name"] == "Total reactive energy"
        assert rows[-1]["address"] == 344
    finally:
        panel.deleteLater()


# (шаблон, адрес, bitmask, значение из value_names, ожидаемое имя)
ANNOTATED_ROWS = [
    ("Delta Electronics/MS300", 8194, True, 1, "Reset"),
    ("Delta Electronics/MS300", 8448, False, 17, "oH2"),
    ("Delta Electronics/MS300", 8449, True, 10, "Run cmd via comm"),
    ("Delta Electronics/C2000", 8194, True, 2, "Base block ON"),
    ("Delta Electronics/C2000", 8448, False, 26880, "Warn SpdR"),
    ("Delta Electronics/C2000", 8449, True, 11, "Parameter locked"),
    ("Huawei/SUN2000", 32000, True, 6, "Stop due to faults"),
    ("Huawei/SUN2000", 32008, True, 15, "Output DC Component Overhigh (2040)"),
    ("Huawei/SUN2000", 32089, False, 512, "On-grid"),
    ("Huawei/SUN2000", 32090, False, 2002, "DC Arc Fault"),
    ("EPEver/Tracer-AN", 12800, True, 15, "Wrong rated voltage identification"),
    ("EPEver/Tracer-AN", 12801, True, 0, "Running"),
    ("Wiren Board/WB-MDM3", 97, False, 1, "AC voltage present"),
    ("Wiren Board/WB-M1W2", 16, False, 1, "OK"),
    ("Wiren Board/WB-MRGBW-D", 4000, False, 256, "RGB + W"),
    ("Wiren Board/WB-MCM8", 8, True, 7, "Input 8"),
    ("Wiren Board/WB-MRWM2", 88, False, 1, "Blocked"),
    ("Wiren Board/WB-MAI6", 5120, False, 4096, None),
    ("Wiren Board/WB-MAO4", 10, False, 1, "Analog 0-10 V"),
]


@pytest.mark.parametrize(
    ("resource", "address", "bitmask", "value", "name"), ANNOTATED_ROWS
)
def test_template_annotations_roundtrip(
    qapp: QApplication, resource: str, address: int, bitmask: bool, value: int, name: str | None
) -> None:
    """value_names/bitmask из шаблона переживают RegistersPanel set_state→state."""
    panel = RegistersPanel(lambda: 1)
    try:
        panel.set_state(load_template(resource)["registers"])
        rows = panel.state()
        row = next(r for r in rows if r["address"] == address)
        assert row["bitmask"] is bitmask
        names = row["value_names"]
        assert names, f"{resource}@{address}: empty value_names"
        if name is not None:
            assert names.get(str(value)) == name
    finally:
        panel.deleteLater()
