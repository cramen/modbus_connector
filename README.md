# modbus_connector

[Russian version: README_ru.md](README_ru.md)

A PySide6 GUI application for debugging Modbus buses and developing Modbus
devices. Works with Modbus TCP and Modbus RTU via synchronous pymodbus clients;
all Modbus logic runs in a separate thread (QThread), so the GUI never freezes.

## Features

- TCP (host, port, timeout) and RTU (serial port, baudrate, parity, etc.;
  RTU by default) connections — all settings are configured in the GUI.
  Connection settings and the register list persist between launches in
  `~/.modbus_connector/settings.json`, and can also be saved to / loaded from
  an arbitrary JSON file via the File menu.
- Register table: rows with a name, area type (coils, discrete inputs,
  holding/input registers), address and count; read and write values.
  Enter in the "New value" column sends the write command, Ctrl+R (Cmd+R on
  macOS) reads the current row; the whole table is keyboard-friendly.
- Polling of all rows with an adjustable interval.
- Address scanner ("Scanner…" button, separate window): iterates unit ids in
  a given range with configurable probes and shows devices that answered at
  least one probe.
- Log panel at the bottom of the window, toggled with the "Log" button.

## Screenshots

Main window — connected, register table with read values, request/response log:

![Main window](docs/screenshots/main_window.png)

Address scanner — probes, progress and found units:

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

### Adding registers

Press **Add register** and fill in the row: an arbitrary **Name**, the area
**Type** (coils, discrete inputs, holding registers, input registers),
**Address** (decimal or hex, e.g. `0x10`) and **Count** (how many values to
read starting at the address). All columns are plain cells — the table is
fully navigable with the keyboard. The ✕ button deletes a row.

### Reading values

- **Ctrl+R** (**Cmd+R** on macOS) — reads the row that has the keyboard focus.
- **Read all** — reads every row once.
- **Start polling** — reads all rows repeatedly with the interval set in the
  "Interval" field (milliseconds); press **Stop polling** to stop.

Read values appear in the **Value** column; every request and response is also
shown in the log panel (toggled with the "Log" button).

### Writing values

Type the value(s) into the **New value** column and press **Enter** — the
write command is sent immediately:

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
the results list. Scanning pauses polling in the main window.


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
  models.py       # Qt-free data types: TcpParams/RtuParams, RegisterRow,
                  # ScanProbe, parse_values()/format_values()
  backend.py      # ModbusBackend — synchronous pymodbus wrapper (no Qt)
  worker.py       # ModbusWorker (QObject) — signals/slots over backend, QThread
  connection_panel.py  # connection panel (TCP/RTU, state/set_state)
  registers_panel.py   # register table, polling, Enter = write
  scanner_panel.py     # address scanner (separate window)
  log_panel.py         # log panel (hideable)
  settings_store.py    # settings persistence in ~/.modbus_connector/settings.json
  main_window.py  # main window
  app.py          # QApplication creation and startup
  __main__.py     # python -m modbus_connector
tests/
  conftest.py     # modbus_server fixture: test Modbus TCP server on 127.0.0.1
  test_models.py  # parse_values()/format_values()
  test_backend.py # ModbusBackend against the test server
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
