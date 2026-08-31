"""E2E: сервер подпроцессом, клиент MCP SDK по stdio-транспорту."""

import asyncio
import json
import sys
from typing import Any

import pytest
from mcp.client.stdio import stdio_client

from mcp import ClientSession, StdioServerParameters

EXPECTED_TOOLS = {
    "modbus_read",
    "modbus_write",
    "modbus_scan_units",
    "modbus_scan_addresses",
    "templates_list",
    "templates_get",
    "job_start",
    "job_stop",
    "job_list",
    "job_status",
    "job_events",
}


async def _call(session: ClientSession, name: str, arguments: dict[str, Any]) -> Any:
    result = await session.call_tool(name, arguments)
    assert not result.isError, result.content[0].text
    return json.loads(result.content[0].text)


@pytest.mark.asyncio()
async def test_e2e_stdio(modbus_server: int) -> None:
    server_params = StdioServerParameters(
        command=sys.executable, args=["-m", "modbus_connector_mcp.server"]
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == EXPECTED_TOOLS

            data = await _call(
                session,
                "modbus_read",
                {"conn": f"tcp:127.0.0.1:{modbus_server}", "unit": 1, "kind": "hr",
                 "address": 0, "count": 2},
            )
            assert data["raw"] == [100, 101]
            assert data["values"] == [100, 101]

            started = await _call(
                session,
                "job_start",
                {"kind": "poll",
                 "params": {"conn": f"tcp:127.0.0.1:{modbus_server}", "unit": 1,
                            "kind": "hr", "address": 0, "count": 2, "interval_ms": 50}},
            )
            job_id = started["job_id"]
            assert started["status"] == "running"
            await asyncio.sleep(0.4)
            payload = await _call(session, "job_events", {"job_id": job_id})
            assert payload["events"], "poll-job не выдала событий"
            assert payload["events"][0]["data"]["raw"] == [100, 101]
            status = await _call(session, "job_status", {"job_id": job_id})
            assert status["status"] == "running"
            stopped = await _call(session, "job_stop", {"job_id": job_id})
            assert stopped["status"] == "stopped"

            # ошибка tool-вызова (чтение вне карты) приходит как isError, сервер жив
            error = await session.call_tool(
                "modbus_read",
                {"conn": f"tcp:127.0.0.1:{modbus_server}", "unit": 1, "kind": "hr",
                 "address": 100, "count": 1},
            )
            assert error.isError
            assert "exception code 0x02" in error.content[0].text
            jobs = await _call(session, "job_list", {})
            assert jobs["jobs"][0]["job_id"] == job_id
