#!/usr/bin/env bash
# Собрать modbus-connector_<version>_amd64.deb из папки PyInstaller.
# Использование: packaging/linux/make_deb.sh 1.2.3   (Linux-only, нужен dpkg-deb)
set -euo pipefail
cd "$(dirname "$0")/../.."

VERSION=${1:?usage: make_deb.sh <version>}
DIST=dist/ModbusConnector
PKGROOT=dist/deb-root
if [ ! -x "$DIST/ModbusConnector" ]; then
    echo "error: $DIST/ModbusConnector not found — run ./build.sh first" >&2
    exit 1
fi

rm -rf "$PKGROOT"
mkdir -p "$PKGROOT/DEBIAN" \
         "$PKGROOT/opt/modbus-connector" \
         "$PKGROOT/usr/bin" \
         "$PKGROOT/usr/share/applications" \
         "$PKGROOT/usr/share/pixmaps"

cp -r "$DIST/." "$PKGROOT/opt/modbus-connector/"
ln -s /opt/modbus-connector/ModbusConnector "$PKGROOT/usr/bin/modbus-connector"
cp packaging/linux/ModbusConnector.desktop \
   "$PKGROOT/usr/share/applications/modbus-connector.desktop"
cp assets/icon.png "$PKGROOT/usr/share/pixmaps/modbus-connector.png"

sed "s/__VERSION__/$VERSION/" packaging/linux/deb-control > "$PKGROOT/DEBIAN/control"

dpkg-deb --root-owner-group --build "$PKGROOT" "dist/modbus-connector_${VERSION}_amd64.deb"
rm -rf "$PKGROOT"
echo "Built dist/modbus-connector_${VERSION}_amd64.deb"
