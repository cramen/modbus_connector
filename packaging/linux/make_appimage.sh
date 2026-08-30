#!/usr/bin/env bash
# Собрать ModbusConnector.AppImage из папки PyInstaller (dist/ModbusConnector/).
# Нужен appimagetool (переменная APPIMAGETOOL или в PATH); Linux-only.
set -euo pipefail
cd "$(dirname "$0")/../.."

DIST=dist/ModbusConnector
APPDIR=dist/AppDir
if [ ! -x "$DIST/ModbusConnector" ]; then
    echo "error: $DIST/ModbusConnector not found — run ./build.sh first" >&2
    exit 1
fi

rm -rf "$APPDIR"
mkdir -p "$APPDIR"
cp -r "$DIST" "$APPDIR/ModbusConnector"
cp packaging/linux/ModbusConnector.desktop "$APPDIR/ModbusConnector.desktop"
cp assets/icon.png "$APPDIR/modbus-connector.png"

cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/ModbusConnector/ModbusConnector" "$@"
EOF
chmod +x "$APPDIR/AppRun"

APPIMAGETOOL=${APPIMAGETOOL:-appimagetool}
"$APPIMAGETOOL" "$APPDIR" dist/ModbusConnector.AppImage
rm -rf "$APPDIR"
echo "Built dist/ModbusConnector.AppImage"
