"""Точка входа PyInstaller для modbus-connector-cli (onefile console)."""

import sys

from modbus_connector.cli import main

if __name__ == "__main__":
    sys.exit(main())
