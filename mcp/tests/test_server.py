"""Тесты tools сервера: вызовы через FastMCP.call_tool против тестового сервера."""

import json
import subprocess
import sys
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from modbus_connector_mcp.server import create_server


async def call(mcp: FastMCP, name: str, arguments: dict[str, Any]) -> Any:
    """Вызвать tool и разобрать JSON из текстового ответа; ошибки прилетают ToolError."""
    content, _structured = await mcp.call_tool(name, arguments)
    return json.loads(content[0].text)


@pytest.fixture()
def mcp_server():
    mcp, registry = create_server()
    yield mcp
    registry.stop_all()


@pytest.mark.asyncio()
async def test_read_values(mcp_server: FastMCP, modbus_server: int) -> None:
    data = await call(
        mcp_server,
        "modbus_read",
        {"conn": f"tcp:127.0.0.1:{modbus_server}", "unit": 1, "kind": "hr",
         "address": 0, "count": 4},
    )
    assert data == {
        "unit": 1,
        "kind": "holding_registers",
        "address": 0,
        "count": 4,
        "raw": [100, 101, 102, 103],
        "values": [100, 101, 102, 103],
    }


@pytest.mark.asyncio()
async def test_read_coils(mcp_server: FastMCP, modbus_server: int) -> None:
    data = await call(
        mcp_server,
        "modbus_read",
        {"conn": f"tcp:127.0.0.1:{modbus_server}", "unit": 1, "kind": "coils",
         "address": 0, "count": 4},
    )
    assert data["raw"] == [True, False, True, False]
    assert data["values"] == [True, False, True, False]


@pytest.mark.asyncio()
async def test_read_scaled(mcp_server: FastMCP, modbus_server: int) -> None:
    data = await call(
        mcp_server,
        "modbus_read",
        {"conn": f"tcp:127.0.0.1:{modbus_server}", "unit": 1, "kind": "ir",
         "address": 0, "count": 1, "scale": 0.1, "offset": 1.0},
    )
    assert data["values"] == [7 * 0.1 + 1.0]


@pytest.mark.asyncio()
async def test_read_modbus_exception(mcp_server: FastMCP, modbus_server: int) -> None:
    with pytest.raises(ToolError) as exc_info:
        await call(
            mcp_server,
            "modbus_read",
            {"conn": f"tcp:127.0.0.1:{modbus_server}", "unit": 1, "kind": "hr",
             "address": 100, "count": 1},
        )
    assert "exception code 0x02" in str(exc_info.value)  # Illegal Address


@pytest.mark.asyncio()
async def test_write_and_read_back(mcp_server: FastMCP, modbus_server: int) -> None:
    written = await call(
        mcp_server,
        "modbus_write",
        {"conn": f"tcp:127.0.0.1:{modbus_server}", "unit": 1, "kind": "hr",
         "address": 5, "values": [7, 8]},
    )
    assert written == {"written": 2}
    data = await call(
        mcp_server,
        "modbus_read",
        {"conn": f"tcp:127.0.0.1:{modbus_server}", "unit": 1, "kind": "hr",
         "address": 5, "count": 2},
    )
    assert data["raw"] == [7, 8]


@pytest.mark.asyncio()
async def test_read_only_blocks_write(modbus_server: int) -> None:
    mcp, registry = create_server(read_only=True)
    try:
        with pytest.raises(ToolError, match="read-only"):
            await call(
                mcp,
                "modbus_write",
                {"conn": f"tcp:127.0.0.1:{modbus_server}", "unit": 1, "kind": "hr",
                 "address": 5, "values": [1]},
            )
        with pytest.raises(ToolError, match="read-only"):
            await call(mcp, "job_start", {"kind": "gateway", "params": {}})
        # sniff/poll разрешены в read-only: ошибка только о валидации/порте, не о режиме
        with pytest.raises(ToolError) as exc_info:
            await call(mcp, "job_start", {"kind": "sniff", "params": {"port": "/dev/none-x"}})
        assert "read-only" not in str(exc_info.value)
    finally:
        registry.stop_all()


@pytest.mark.asyncio()
async def test_scan_units(mcp_server: FastMCP, modbus_server: int) -> None:
    data = await call(
        mcp_server,
        "modbus_scan_units",
        {"conn": f"tcp:127.0.0.1:{modbus_server}", "start": 1, "end": 1},
    )
    assert data == {"hits": [{"unit": 1, "probes": [0, 1, 2]}]}


@pytest.mark.asyncio()
async def test_scan_addresses(mcp_server: FastMCP, modbus_server: int) -> None:
    data = await call(
        mcp_server,
        "modbus_scan_addresses",
        {"conn": f"tcp:127.0.0.1:{modbus_server}", "unit": 1, "kind": "hr",
         "start": 0, "end": 3},
    )
    assert [hit["address"] for hit in data["hits"]] == [0, 1, 2, 3]
    assert data["hits"][0]["values"] == [100, 101]


@pytest.mark.asyncio()
async def test_bad_conn_spec(mcp_server: FastMCP) -> None:
    with pytest.raises(ToolError, match="bad target spec"):
        await call(
            mcp_server,
            "modbus_read",
            {"conn": "bogus", "unit": 1, "kind": "hr", "address": 0, "count": 1},
        )


@pytest.mark.asyncio()
async def test_templates_list_and_get(mcp_server: FastMCP) -> None:
    entries = await call(mcp_server, "templates_list", {})
    assert entries
    for entry in entries:
        assert set(entry) == {"name", "manufacturer", "resource", "description"}
    template = await call(mcp_server, "templates_get", {"resource": entries[0]["resource"]})
    assert isinstance(template["registers"], list)
    with pytest.raises(ToolError, match="template not found"):
        await call(mcp_server, "templates_get", {"resource": "Nope/Nothing"})


def test_no_qt_import() -> None:
    """Сервер не тянет Qt: PySide6 не появляется в sys.modules после импорта."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import modbus_connector_mcp.server, sys; print('PySide6' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False"
