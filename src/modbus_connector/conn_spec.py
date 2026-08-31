"""Разбор строк-спецификаций подключений и listen-endpoint'ов.

Без Qt. Общий код CLI (modbus-connector-cli) и MCP-сервера:
«tcp:HOST[:PORT]», «rtuovertcp:...», «rtuoverudp:...»,
«rtu:PORT[,baud=...,bits=...,parity=...,stop=...]».
"""

from __future__ import annotations

from typing import Any

from .gateway_backend import (
    GatewayListenParams,
    GatewayRtuOverTcpListenParams,
    GatewayTcpListenParams,
)
from .models import (
    ConnectionParams,
    RtuOverTcpParams,
    RtuOverUdpParams,
    RtuParams,
    TcpParams,
)


def parse_client_endpoint(spec: str, default_port: int = 502) -> tuple[str, int]:
    """«HOST[:PORT]» для клиентского подключения; host обязателен."""
    host, sep, port_text = spec.rpartition(":")
    if not sep:
        host, port_text = spec, ""
    if not host:
        raise ValueError(f"bad endpoint {spec!r} (expected HOST[:PORT])")
    try:
        port = int(port_text) if port_text else default_port
    except ValueError:
        raise ValueError(f"bad port in endpoint {spec!r}") from None
    if not 1 <= port <= 65535:
        raise ValueError(f"port out of range in endpoint {spec!r}")
    return host, port


def parse_listen_endpoint(spec: str, default_host: str) -> tuple[str, int]:
    """«PORT» или «HOST:PORT» для listen-стороны; host по умолчанию — default_host."""
    host, sep, port_text = spec.rpartition(":")
    if not sep:
        host, port_text = default_host, spec
    if not host:
        host = default_host
    try:
        port = int(port_text)
    except ValueError:
        raise ValueError(f"bad listen endpoint {spec!r} (expected PORT or HOST:PORT)") from None
    if not 1 <= port <= 65535:
        raise ValueError(f"port out of range in listen endpoint {spec!r}")
    return host, port


def parse_rtu_spec(text: str, timeout: float = 3.0) -> RtuParams:
    """«PORT[,baud=9600][,bits=8][,parity=N][,stop=1]» → RtuParams."""
    parts = [part.strip() for part in text.split(",")]
    port = parts[0]
    if not port:
        raise ValueError("bad rtu spec (expected rtu:PORT[,baud=...][,bits=...][,parity=...])")
    option_keys = {"baud": "baudrate", "bits": "bytesize", "parity": "parity", "stop": "stopbits"}
    options: dict[str, Any] = {"timeout": timeout}
    for item in parts[1:]:
        key, sep, value = item.partition("=")
        if not sep or key.strip().lower() not in option_keys:
            raise ValueError(f"bad rtu option {item!r} (expected baud=/bits=/parity=/stop=)")
        field = option_keys[key.strip().lower()]
        if field == "parity":
            parity = value.strip().upper()
            if parity not in ("N", "E", "O"):
                raise ValueError(f"bad parity {value!r} (N/E/O)")
            options[field] = parity
        else:
            try:
                options[field] = int(value)
            except ValueError:
                raise ValueError(f"bad rtu option {item!r} (integer expected)") from None
    return RtuParams(port, **options)


def parse_listen_spec(spec: str) -> GatewayListenParams:
    """«tcp:PORT | tcp:HOST:PORT | rtuovertcp:PORT | rtuovertcp:HOST:PORT | rtu:PORT[,...]»."""
    scheme, sep, rest = spec.partition(":")
    if not sep or not rest:
        raise ValueError(f"bad listen spec {spec!r} (expected SCHEME:ENDPOINT)")
    scheme = scheme.lower().replace("-", "")
    if scheme == "tcp":
        host, port = parse_listen_endpoint(rest, "0.0.0.0")
        return GatewayTcpListenParams(host, port)
    if scheme == "rtuovertcp":
        host, port = parse_listen_endpoint(rest, "0.0.0.0")
        return GatewayRtuOverTcpListenParams(host, port)
    if scheme == "rtu":
        return parse_rtu_spec(rest)
    raise ValueError(f"unknown listen scheme {scheme!r} (tcp / rtuovertcp / rtu)")


def parse_connection_spec(spec: str, timeout: float = 3.0) -> ConnectionParams:
    """«tcp:HOST[:PORT] | rtuovertcp:HOST[:PORT] | rtuoverudp:HOST[:PORT] | rtu:PORT[,...]»."""
    scheme, sep, rest = spec.partition(":")
    if not sep or not rest:
        raise ValueError(f"bad target spec {spec!r} (expected SCHEME:ENDPOINT)")
    scheme = scheme.lower().replace("-", "")
    if scheme == "tcp":
        host, port = parse_client_endpoint(rest)
        return TcpParams(host, port, timeout)
    if scheme == "rtuovertcp":
        host, port = parse_client_endpoint(rest)
        return RtuOverTcpParams(host, port, timeout)
    if scheme == "rtuoverudp":
        host, port = parse_client_endpoint(rest)
        return RtuOverUdpParams(host, port, timeout)
    if scheme == "rtu":
        return parse_rtu_spec(rest, timeout)
    raise ValueError(
        f"unknown target scheme {scheme!r} (tcp / rtuovertcp / rtuoverudp / rtu)"
    )
