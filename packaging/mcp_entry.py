"""Точка входа PyInstaller для modbus-connector-mcp (onefile console)."""

import sys

from modbus_connector_mcp.server import main

if __name__ == "__main__":
    sys.exit(main())
