"""MCP-сервер modbus-connector: Modbus-инструменты для агентов (FastMCP, stdio).

Без Qt: sync backend'ы modbus_connector вызываются через asyncio.to_thread.
Ошибки (соединение, Modbus exception с кодом, невалидный спек) пробрасываются
исключением — SDK превращает его в tool-ошибку (isError), сервер не падает.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from modbus_connector.backend import ModbusBackend, ModbusExceptionError
from modbus_connector.conn_spec import parse_connection_spec
from modbus_connector.models import DEFAULT_SCAN_PROBES, ByteOrder, DisplayFormat
from modbus_connector.templates import list_templates, load_template

from . import __version__
from .jobs import JobRegistry, decode_row, parse_format, parse_kind, parse_order

_CONN_SPEC_HELP = (
    "conn spec: tcp:HOST[:PORT] | rtuovertcp:HOST[:PORT] | rtuoverudp:HOST[:PORT] "
    "| rtu:PORT[,baud=9600][,bits=8][,parity=N][,stop=1]"
)


def _dump(record: Any) -> str:
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


def _check_unit(unit: int) -> None:
    if not 1 <= unit <= 247:
        raise ValueError(f"unit id out of range 1..247: {unit}")


def _reraise_with_code(exc: ModbusExceptionError) -> ValueError:
    return ValueError(f"{exc} [exception code 0x{exc.exception_code:02X}]")


def _read_sync(
    conn: str,
    unit: int,
    kind: str,
    address: int,
    count: int,
    fmt: str,
    order: str,
    scale: float,
    offset: float,
) -> str:
    register_kind = parse_kind(kind)
    display_format = parse_format(fmt)
    byte_order = parse_order(order)
    backend = ModbusBackend()
    backend.connect(parse_connection_spec(conn))
    try:
        raw = backend.read(unit, register_kind, address, count)
    except ModbusExceptionError as exc:
        raise _reraise_with_code(exc) from exc
    finally:
        backend.disconnect()
    values = decode_row(raw, register_kind, display_format, byte_order, scale, offset)
    return _dump(
        {
            "unit": unit,
            "kind": register_kind,
            "address": address,
            "count": count,
            "raw": raw,
            "values": values,
        }
    )


def _write_sync(conn: str, unit: int, kind: str, address: int, values: list[Any]) -> str:
    register_kind = parse_kind(kind)
    if register_kind not in ("coils", "holding_registers"):
        raise ValueError(f"write supports coils/holding_registers only, got {register_kind!r}")
    if address < 0:
        raise ValueError(f"address out of range: {address}")
    if not isinstance(values, list) or not values:
        raise ValueError("values must be a non-empty list")
    coerced: list[int | bool]
    if register_kind == "coils":
        coerced = [bool(value) for value in values]
    else:
        coerced = [int(value) for value in values]
    backend = ModbusBackend()
    backend.connect(parse_connection_spec(conn))
    try:
        backend.write(unit, register_kind, address, coerced)
    except ModbusExceptionError as exc:
        raise _reraise_with_code(exc) from exc
    finally:
        backend.disconnect()
    return _dump({"written": len(coerced)})


def _scan_units_sync(conn: str, start: int, end: int) -> str:
    _check_unit(start)
    _check_unit(end)
    backend = ModbusBackend()
    backend.connect(parse_connection_spec(conn))
    hits: list[dict[str, Any]] = []
    try:
        for unit, probes in backend.scan(DEFAULT_SCAN_PROBES, start, end, lambda: False):
            if probes:
                hits.append({"unit": unit, "probes": probes})
    finally:
        backend.disconnect()
    return _dump({"hits": hits})


def _scan_addresses_sync(conn: str, unit: int, kind: str, start: int, end: int) -> str:
    _check_unit(unit)
    register_kind = parse_kind(kind)
    if start < 0 or end < start:
        raise ValueError(f"bad address range {start}..{end}")
    backend = ModbusBackend()
    backend.connect(parse_connection_spec(conn))
    hits: list[dict[str, Any]] = []
    try:
        for address, values in backend.scan_addresses(
            unit, register_kind, start, end, lambda: False
        ):
            hits.append(
                {
                    "address": address,
                    "values": [v if isinstance(v, bool) else int(v) for v in values],
                }
            )
    finally:
        backend.disconnect()
    return _dump({"hits": hits})


def create_server(read_only: bool = False) -> tuple[FastMCP, JobRegistry]:
    """FastMCP-сервер со всеми инструментами + реестр фоновых задач."""
    mcp = FastMCP("modbus-connector")
    registry = JobRegistry(read_only=read_only)

    def _check_writable() -> None:
        if read_only:
            raise ValueError("server is in read-only mode: writes are disabled")

    @mcp.tool()
    async def modbus_read(
        conn: str,
        unit: int,
        kind: str,
        address: int,
        count: int,
        format: DisplayFormat = "dec",
        order: ByteOrder = "ABCD",
        scale: float = 1.0,
        offset: float = 0.0,
    ) -> str:
        """Read Modbus registers/coils once. kind: coils|di|hr|ir (or full names)."""
        _check_unit(unit)
        if address < 0 or count < 1:
            raise ValueError(f"bad address/count: {address}/{count}")
        return await asyncio.to_thread(
            _read_sync, conn, unit, kind, address, count, format, order, scale, offset
        )

    @mcp.tool()
    async def modbus_write(
        conn: str, unit: int, kind: str, address: int, values: list[int | bool]
    ) -> str:
        """Write coils/holding_registers. Returns {"written": N}."""
        _check_writable()
        _check_unit(unit)
        return await asyncio.to_thread(_write_sync, conn, unit, kind, address, values)

    @mcp.tool()
    async def modbus_scan_units(conn: str, start: int, end: int) -> str:
        """Scan unit ids start..end with default probes. Slow on silent units."""
        return await asyncio.to_thread(_scan_units_sync, conn, start, end)

    @mcp.tool()
    async def modbus_scan_addresses(
        conn: str, unit: int, kind: str, start: int, end: int
    ) -> str:
        """Scan register addresses start..end of one unit, returning those that answer."""
        return await asyncio.to_thread(_scan_addresses_sync, conn, unit, kind, start, end)

    @mcp.tool()
    async def templates_list() -> str:
        """List bundled device register-map templates."""

        def _run() -> str:
            return _dump(
                [
                    {
                        "name": info.name,
                        "manufacturer": info.manufacturer,
                        "resource": info.resource,
                        "description": info.description,
                    }
                    for info in list_templates()
                ]
            )

        return await asyncio.to_thread(_run)

    @mcp.tool()
    async def templates_get(resource: str) -> str:
        """Full JSON of a device template by resource key (e.g. "Eastron/SDM120")."""
        return await asyncio.to_thread(lambda: _dump(load_template(resource)))

    @mcp.tool()
    async def job_start(kind: str, params: dict[str, Any]) -> str:
        """Start a background job: simulate | gateway | sniff | poll.

        simulate: {"listen": spec, "unit": int|null, "map": {"registers": [...]}|null}
        gateway:  {"listen": spec, "target": spec, "units": [int]|null}
        sniff:    {"port": str, "baud": int, "bits": int, "parity": "N|E|O", "stop": int}
        poll:     {"conn": spec, "unit", "kind", "address", "count",
                   "interval_ms", "format", "order"}
        """
        job = await asyncio.to_thread(registry.start, kind, params)
        return _dump({"job_id": job.job_id, "status": job.status, "params": job.params})

    @mcp.tool()
    async def job_stop(job_id: str) -> str:
        """Stop a background job."""
        job = await asyncio.to_thread(registry.stop, job_id)
        return _dump(job.summary())

    @mcp.tool()
    async def job_list() -> str:
        """List all background jobs with their status."""
        return _dump({"jobs": registry.list()})

    @mcp.tool()
    async def job_status(job_id: str) -> str:
        """Status of one background job."""
        return _dump(registry.status(job_id))

    @mcp.tool()
    async def job_events(job_id: str, since_seq: int = 0, limit: int = 100) -> str:
        """Events of a job with seq > since_seq; pass next_seq for incremental reads."""
        return _dump(registry.events(job_id, since_seq, limit))

    return mcp, registry


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="modbus-connector-mcp",
        description="MCP server exposing Modbus Connector tools: read/write/scan, "
        "device templates and background jobs (simulator/gateway/sniffer/polling). "
        + _CONN_SPEC_HELP,
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="disable modbus_write and simulate/gateway jobs (sniff/poll stay allowed)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)
    mcp, registry = create_server(read_only=args.read_only)
    try:
        mcp.run()  # stdio-транспорт, блокирует до EOF
    finally:
        registry.stop_all()


if __name__ == "__main__":
    main()
