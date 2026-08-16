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
