#!/usr/bin/env bash
# Build a standalone application (PyInstaller).
# macOS: dist/ModbusConnector.app
# Windows/Linux: dist/ModbusConnector/ (folder with the executable inside)
set -euo pipefail
cd "$(dirname "$0")"

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
    echo "error: .venv not found, create it first: python -m venv .venv" >&2
    exit 1
fi

"$PY" -m pip install -q -e ".[build]"
ICON=assets/icon.png
if [ "$(uname)" = "Darwin" ]; then
    ICON=assets/icon.icns
fi

"$PY" -m PyInstaller \
    --noconfirm --clean \
    --windowed \
    --name ModbusConnector \
    --osx-bundle-identifier com.cramen.modbusconnector \
    --icon "$ICON" \
    --paths src \
    --add-data "src/modbus_connector/templates:modbus_connector/templates" \
    src/modbus_connector/__main__.py

rm -rf build  # промежуточные файлы PyInstaller, запускать их нельзя

if [ "$(uname)" = "Darwin" ]; then
    hdiutil create -volname ModbusConnector -srcfolder dist/ModbusConnector.app \
        -ov -format UDZO dist/ModbusConnector.dmg >/dev/null
fi

echo
echo "Build finished. Artifacts in dist/:"
ls -d dist/ModbusConnector* 2>/dev/null || true
cat <<'EOF'

How to run (macOS):
  open dist/ModbusConnector.app        # double-click works too
For other machines copy dist/ModbusConnector.dmg (or the .app via Finder "Compress").
Do NOT run files from build/ — it is a PyInstaller intermediate directory.
EOF
