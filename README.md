# modbus_connector

[Russian version: README_ru.md](README_ru.md)

A PySide6 GUI application for debugging Modbus buses and developing Modbus
devices. Works with Modbus TCP and Modbus RTU via synchronous pymodbus clients;
all Modbus logic runs in a separate thread (QThread), so the GUI never freezes.

## Features

- TCP (host, port, timeout) and RTU (serial port, baudrate, parity, etc.;
  RTU by default) connections — all settings are configured in the GUI.
  Connection settings, the register list and the scanner state persist between
  launches in `~/.modbus_connector/settings.json`, and can also be saved to /
  loaded from an arbitrary JSON file via the File menu.
- Register table: rows with a name, area type (coils, discrete inputs,
  holding/input registers), address and count; read and write values.
  Enter in the "New value" column sends the write command, Ctrl+R (Cmd+R on
  macOS) reads the current row; the whole table is keyboard-friendly.
- Rich value display: per-row formats (dec/hex/s16/u32/s32/f32), scaling with
  offset and engineering units; a value that changed between reads flashes
  for a couple of seconds.
- Per-row Unit ID: rows can address different devices on the same bus.
- Filter box and one-click "Sort by address" for large tables.
- Polling of all rows with an adjustable interval.
- Link visibility: transaction statistics in the status bar (count, errors
  with percentage, average response time) and a live connection indicator
  (green = alive, orange "(idle)" = link idle or degraded).
- Address scanner ("Scanner…" button, separate window): iterates unit ids in
  a given range with configurable probes and shows devices that answered at
  least one probe; double-click a found unit to select it for the connection.
  A second sweep scans the register address space of a known unit and lists
  the addresses that respond.
- Log panel at the bottom of the window, toggled with the "Log" button:
  human-readable requests/responses, optional raw bus traffic in hex
  ("Raw" checkbox) and export of the whole log to a file ("Save…").

## Screenshots

Main window — connected, register table with read values, request/response log:

![Main window](docs/screenshots/main_window.png)

Address scanner — unit sweep with probes and the register address scan:

![Scanner](docs/screenshots/scanner.png)

## Requirements

- Python 3.11+
- `PySide6`, `pymodbus[serial]==3.6.9`

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

## Run

```bash
modbus-connector
# or
python -m modbus_connector
```

## Usage

### Connecting

1. Choose the connection type: **TCP** (host, port) or **RTU** (serial port,
   baudrate, data bits, parity, stop bits — RTU is the default). Use "Refresh"
   to rescan serial ports; a newly appeared port is selected automatically.
2. Set the **Unit ID** of the target device (used for all register operations)
   and the response **Timeout**.
3. Press **Connect**. Input fields are locked while connected; press
   **Disconnect** to change settings.

The status label next to the button is live: green means the link is up,
orange with an "(idle)" suffix means the connection is configured but the last
transaction timed out — pymodbus reconnects transparently on the next request,
so this is informational, not an error. The status bar at the bottom of the
window shows transaction counters: total, errors with a percentage and the
average response time of successful operations.

### Adding registers

Press **Add register** and fill in the row: an arbitrary **Name**, the area
**Type** (coils, discrete inputs, holding registers, input registers),
**Address** (decimal or hex, e.g. `0x10`) and **Count** (how many values to
read starting at the address). The optional **Unit ID** column overrides the
connection-wide unit for this row (empty = use the connection unit) — handy
for polling several devices on one RS-485 bus. All columns are plain cells —
the table is fully navigable with the keyboard. The ✕ button deletes a row.

### Reading values

- **Ctrl+R** (**Cmd+R** on macOS) — reads the row that has the keyboard focus.
- **Read all** — reads every row once.
- **Start polling** — reads all rows repeatedly with the interval set in the
  "Interval" field (milliseconds); press **Stop polling** to stop.

Read values appear in the **Value** column; every request and response is also
shown in the log panel (toggled with the "Log" button).

### Display formats, scaling and units

For register rows the **Format** column chooses how the Value column renders:
`dec` (default), `hex` (`0xNNNN`), `s16` (signed 16-bit), and `u32`/`s32`/`f32`,
which combine register pairs into 32-bit values (big-endian: the first register
is the high word; a leftover odd register is shown as-is). Coils and discrete
inputs always show 0/1.

The **Scale**, **Offset** and **Unit** columns show engineering values instead:
each raw register is displayed as `x * scale + offset` with the unit appended
(e.g. `23.5 °C`). Scaling is skipped for the `hex` format.

A value that changed since the previous read flashes green for ~2 seconds.

Use the **Filter…** box above the table to show only rows whose name, type,
address or unit id contains the text, and **Sort by address** to reorder the
table by address.

### Writing values

Type the value(s) into the **New value** column and press **Enter** — the
write command is sent immediately. Values are always raw: display scaling
(Scale/Offset) is never applied to them.

- registers: decimal or hex numbers (`4321`, `0x10E1`); for Count > 1 enter
  several values separated by commas or spaces (`1, 2, 0xFF`) — a single value
  uses function "write single register", several values use "write multiple
  registers";
- coils: `0`/`1`, `true`/`false`, `on`/`off` (case-insensitive).

After a successful write the row is re-read automatically, so the **Value**
column reflects the applied change. Parse errors and Modbus errors are
reported in the log panel.

Note: only coils and holding registers are writable — discrete inputs and
input registers are read-only by the protocol.

### Scanning for devices

Press **Scanner…** to open the scanner window. Set the unit id range
(default 1–247) and the probe list (register type + address + count to try on
each address; sensible defaults are prefilled). **Start scan** begins the
sweep, **Stop** aborts it; units that answered at least one probe appear in
the results list. **Double-click a found unit** to copy it into the connection
panel's Unit ID field. Scanning pauses polling in the main window.

The **Address scan** section below works the other way around: for a known
unit it reads a range of addresses of a chosen register type one by one and
lists every address that answered as `0xNNNN (dec)` — a quick way to map the
register space of an unfamiliar device.

The scanner's range, probe list and address-scan parameters persist in the
settings along with everything else.

### Log panel

The log panel at the bottom of the main window (toggled with the **Log**
button) shows every request and response with timestamps. The **Raw**
checkbox additionally displays raw bus frames in hex (`→ tx …` / `← rx …`) —
off by default to keep the log readable. **Save…** exports the entire log
(including raw frames hidden by the checkbox) to a text file; **Clear**
empties it.


## Building a standalone executable

```bash
./build.sh        # macOS / Linux
build.bat         # Windows (cmd, also works by double-click)
```

The script installs PyInstaller (the `build` extra) and builds a standalone
application into `dist/`: on macOS — `ModbusConnector.app` plus a
`ModbusConnector.dmg` disk image; on Windows/Linux — a `ModbusConnector/`
folder with the executable inside (`ModbusConnector.exe` on Windows; copy the
whole folder to another machine). The artifact does not require Python on the
target machine.

macOS notes:

- Run: double-click `ModbusConnector.app` or `open dist/ModbusConnector.app`.
  Never run files from the intermediate `build/` directory (the script removes
  it after building).
- To move the app to another machine use the ready-made
  `dist/ModbusConnector.dmg` (do not rename or repack the `.app` into a `.pkg`
  yourself — such a file is not a valid installer).
- The app is ad-hoc signed: on another Mac Gatekeeper will warn about an
  unidentified developer on first launch — open via right-click → "Open", or
  remove the quarantine: `xattr -dr com.apple.quarantine ModbusConnector.app`.

Linux notes:

- RTU connections to serial ports (`/dev/ttyUSB*`, `/dev/ttyACM*`, etc.) require
  membership in the port's group, usually `dialout` (sometimes `uucp`). If you
  see `Errno 13` / "Permission denied" on connect, check the port:
  ```bash
  ls -l /dev/ttyUSB0
  ```
  Add your user to the group and re-login (or run `newgrp dialout`):
  ```bash
  sudo usermod -aG dialout $USER
  ```

## Project layout

```
src/modbus_connector/
  models.py       # Qt-free data types and helpers: TcpParams/RtuParams,
                  # RegisterRow, ScanProbe, DisplayFormat,
                  # parse_values()/format_values(),
                  # format_register_values()/format_scaled_values(),
                  # Stats/StatsSnapshot
  backend.py      # ModbusBackend — synchronous pymodbus wrapper (no Qt):
                  # read/write, unit scan, register address scan,
                  # raw traffic hook
  worker.py       # ModbusWorker (QObject) — signals/slots over backend, QThread;
                  # timing statistics, liveness checks, traffic forwarding
  connection_panel.py  # connection panel (TCP/RTU, state/set_state) with a live
                       # status indicator (gray/green/orange)
  registers_panel.py   # register table: polling, per-row unit/format/scaling,
                       # change highlighting, filter/sort, Enter = write
  scanner_panel.py     # unit scanner + register address scan (separate window)
  log_panel.py         # log panel (hideable): Raw hex traffic toggle, Save…
  settings_store.py    # settings persistence in ~/.modbus_connector/settings.json
  main_window.py  # main window, status bar with transaction statistics
  app.py          # QApplication creation and startup
  __main__.py     # python -m modbus_connector
tests/
  conftest.py     # modbus_server fixture: test Modbus TCP server on 127.0.0.1
  test_models.py  # value parsing/formatting, Stats
  test_backend.py # ModbusBackend against the test server
  test_registers_panel.py  # offscreen Qt tests for the register table
```

## Development

```bash
pytest          # tests
ruff check .    # lint
```

The backend tests start a real Modbus TCP server (pymodbus) on 127.0.0.1
with a free port and exercise read/write/scan through `ModbusBackend`.

## CI

GitHub Actions (`.github/workflows/build.yml`) runs tests and builds artifacts
for all three OSes on macOS/Windows/Linux runners on every push to `main`:
`modbus-connector-macos` (DMG), `modbus-connector-windows` and
`modbus-connector-linux` (zip with the executable). Download them from the
workflow run page (Actions → a run → Artifacts; kept for 90 days); a build can
also be started manually via "Run workflow".

Pushing a `v*` tag (e.g. `git tag v0.1.0 && git push origin v0.1.0`)
automatically attaches the same files to a GitHub Release — a permanent
download page (Releases in the repository).
