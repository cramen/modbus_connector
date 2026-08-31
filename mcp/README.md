# modbus-connector-mcp

MCP server that gives LLM agents (Claude Desktop, CLI agents) access to
Modbus devices through the [Modbus Connector](https://github.com/cramen/modbus_connector)
backends: reading and writing registers, unit/address scanners, and
long-running jobs — device simulator, gateway, RTU sniffer and polling.

No Qt, no GUI — the only dependencies are `modbus-connector` (pymodbus-only
base) and the official `mcp` SDK.

## Installation

```bash
pip install modbus-connector-mcp
```

## Running with Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "modbus": {
      "command": "modbus-connector-mcp",
      "args": []
    }
  }
}
```

Start the server with `--read-only` to forbid all writes (`modbus_write`
and the `simulate`/`gateway` jobs are rejected; reads, scanners,
`sniff`/`poll` jobs keep working).

The server speaks stdio (JSON-RPC 2.0) and exits when the client
disconnects — all running jobs stop with it.

## Connection specs

Every tool takes a `conn` (or `listen`/`target`) spec string:

- `tcp:192.168.1.10:502` — Modbus TCP (default port 502)
- `rtu:/dev/ttyUSB0,baud=9600,bits=8,parity=N,stop=1` — Modbus RTU serial
- `rtuovertcp:192.168.1.10:502` — RTU framing over TCP (also `rtu-over-tcp:`), `rtuoverudp:...`

## Tools

| Tool | What it does |
| --- | --- |
| `modbus_read` | One-shot read of coils/discrete_inputs/holding/input registers, decoded values (format/order/scale/offset) |
| `modbus_write` | Write coils/holding registers |
| `modbus_scan_units` | Scan a unit-id range |
| `modbus_scan_addresses` | Scan a register address range of one unit |
| `templates_list` / `templates_get` | Bundled device templates catalog |
| `job_start` / `job_stop` / `job_list` / `job_status` / `job_events` | Long-running jobs (see below) |

## Jobs

`job_start(kind, params)` returns immediately with a `job_id`; the process
keeps running inside the server:

- `simulate` — `{"listen": "tcp:1502", "unit": 1|null, "map": {"registers": [...]}}`
  (map = template/session JSON register list; master writes become events)
- `gateway` — `{"listen": "tcp:5020", "target": "rtu:/dev/ttyUSB0,baud=9600", "units": [1,5]|null}`
- `sniff` — `{"port": "/dev/cu.usbserial", "baud": 19200, ...}` (passive RTU listening)
- `poll` — `{"conn": "tcp:...", "unit": 1, "kind": "holding_registers",
  "address": 0, "count": 2, "interval_ms": 1000}`

`job_events(job_id, since_seq, limit)` returns up to `limit` events newer
than `since_seq` plus a `next_seq` cursor for the next call — poll it
incrementally to follow the traffic.

## Port conflicts

The server does not share devices with the GUI or CLI: a serial port or a
TCP listen port can be opened by one process only. If a job fails with
"port busy", close the other session using it.

## Development

```bash
pip install -e mcp           # into the repo's dev venv
.venv/bin/python -m pytest mcp/tests -q
```
