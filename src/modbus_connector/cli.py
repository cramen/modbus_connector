"""Консольная утилита modbus-connector-cli: доступ к Modbus без GUI.

Без Qt: read/write/poll/scan поверх ModbusBackend, simulate (SimBackend),
gateway (GatewayBackend), sniff (SnifferBackend), запись истории — DataLogger.
Данные — compact JSON (потоковые команды — NDJSON по строке на событие) в
stdout, диагностика и прогресс — stderr, --text переключает вывод на
человекочитаемый.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, get_args

from pymodbus.exceptions import ModbusIOException

from .backend import ModbusBackend, ModbusExceptionError
from .datalogger import DataLogger, LogSample, LogSettings
from .gateway_backend import (
    GatewayBackend,
    GatewayListenParams,
    GatewayRtuOverTcpListenParams,
    GatewayTcpListenParams,
    describe_gateway,
)
from .models import (
    DEFAULT_SCAN_PROBES,
    ByteOrder,
    ConnectionParams,
    DisplayFormat,
    RegisterKind,
    RtuOverTcpParams,
    RtuOverUdpParams,
    RtuParams,
    TcpParams,
    decode_register_values,
    format_register_values,
    parse_values,
    rows_from_csv,
)
from .sim_backend import SimBackend, SimRtuOverTcpParams, SimTcpParams, describe_sim
from .sniffer_backend import SnifferBackend, describe_sniffer

EXIT_OK = 0  # успех (Ctrl+C у потоковых команд — тоже 0)
EXIT_CONNECTION = 2  # ошибка соединения/таймаут
EXIT_MODBUS = 3  # устройство ответило Modbus exception
EXIT_USAGE = 4  # ошибка аргументов/входных файлов

_EXIT_CODES_TEXT = (
    "exit codes: 0 ok (Ctrl+C of streaming commands included), "
    "2 connection/timeout error, 3 modbus exception, 4 bad arguments/input file"
)

_KINDS: dict[str, RegisterKind] = {
    "coils": "coils",
    "coil": "coils",
    "di": "discrete_inputs",
    "discrete": "discrete_inputs",
    "discrete_inputs": "discrete_inputs",
    "hr": "holding_registers",
    "holding": "holding_registers",
    "holding_registers": "holding_registers",
    "ir": "input_registers",
    "input": "input_registers",
    "input_registers": "input_registers",
}

_BIT_KINDS = ("coils", "discrete_inputs")
_STRING_FORMATS = ("hex", "ascii", "ascii1")


class _CliParser(argparse.ArgumentParser):
    """ArgumentParser с exit code 4 на ошибках разбора (вместо стандартного 2)."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def _sleep(seconds: float) -> None:
    """time.sleep, вынесенный в функцию для подмены в тестах (симуляция Ctrl+C)."""
    time.sleep(seconds)


def _wait_until_interrupt() -> None:
    """Блокирующее ожидание Ctrl+C для потоковых команд."""
    while True:
        _sleep(0.5)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _emit(record: dict[str, Any]) -> None:
    print(json.dumps(record, ensure_ascii=False, separators=(",", ":")), flush=True)


def _event(args: argparse.Namespace, record: dict[str, Any], text: str) -> None:
    """Событие потоковой команды: NDJSON или текстовая строка (из потока backend)."""
    if getattr(args, "text", False):
        print(text, flush=True)
    else:
        _emit(record)


def _version() -> str:
    try:
        return importlib.metadata.version("modbus-connector")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0+unknown"


def _kind(text: str) -> RegisterKind:
    kind = _KINDS.get(text.lower())
    if kind is None:
        raise argparse.ArgumentTypeError(
            f"unknown register area {text!r} (coils/di/hr/ir or full names: "
            "coils, discrete_inputs, holding_registers, input_registers)"
        )
    return kind


def _nonneg_int(text: str) -> int:
    value = int(text, 0)
    if value < 0:
        raise argparse.ArgumentTypeError(f"expected a non-negative integer, got {text!r}")
    return value


def _positive_int(text: str) -> int:
    value = int(text, 0)
    if value < 1:
        raise argparse.ArgumentTypeError(f"expected a positive integer, got {text!r}")
    return value


def _client_host_port(spec: str, default_port: int = 502) -> tuple[str, int]:
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


def _listen_host_port(spec: str, default_host: str) -> tuple[str, int]:
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


def _parse_rtu_spec(text: str, timeout: float = 3.0) -> RtuParams:
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


def _params_from_args(args: argparse.Namespace) -> ConnectionParams:
    """ConnectionParams из общих флагов подключения; ровно один транспорт обязателен."""
    timeout = args.timeout
    if args.tcp is not None:
        host, port = _client_host_port(args.tcp)
        return TcpParams(host, port, timeout)
    if args.rtu_over_tcp is not None:
        host, port = _client_host_port(args.rtu_over_tcp)
        return RtuOverTcpParams(host, port, timeout)
    if args.rtu_over_udp is not None:
        host, port = _client_host_port(args.rtu_over_udp)
        return RtuOverUdpParams(host, port, timeout)
    return RtuParams(args.rtu, args.baud, args.bits, args.parity, args.stop, timeout)


def _check_unit(unit: int) -> None:
    if not 1 <= unit <= 247:
        raise ValueError(f"unit id out of range 1..247: {unit}")


def _parse_range(text: str) -> tuple[int, int]:
    """«START-END» (или одиночное «N») → (start, end)."""
    start_text, sep, end_text = text.partition("-")
    try:
        start = int(start_text, 0)
        end = int(end_text, 0) if sep else start
    except ValueError:
        raise ValueError(f"bad range {text!r} (expected START-END)") from None
    if end < start:
        raise ValueError(f"bad range {text!r} (end < start)")
    return start, end


def _parse_units(text: str | None) -> set[int] | None:
    """«1, 5, 10-20» → set; пусто/None → None (все 1..247). Мусор — ValueError."""
    if text is None or not text.strip():
        return None
    units: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        start_text, sep, end_text = part.partition("-")
        try:
            start = int(start_text, 0)
            end = int(end_text, 0) if sep else start
        except ValueError:
            raise ValueError(f"bad unit item {part!r} (expected N or N-M)") from None
        if end < start:
            raise ValueError(f"bad unit range {part!r} (end < start)")
        units.update(range(start, end + 1))
    if not units:
        raise ValueError(f"no valid units in {text!r}")
    for unit in units:
        _check_unit(unit)
    return units


def _decode_row(
    raw: list[int | bool],
    kind: RegisterKind,
    fmt: DisplayFormat,
    order: ByteOrder,
    scale: float,
    offset: float,
) -> list[int | float | bool] | str:
    """raw → values для JSON: биты как bool, hex/ascii строкой, числа со scale/offset."""
    if kind in _BIT_KINDS:
        return [bool(value) for value in raw]
    ints = [int(value) for value in raw]
    if fmt in _STRING_FORMATS:
        return format_register_values(ints, fmt, order)
    decoded = decode_register_values(ints, fmt, order)
    if scale != 1.0 or offset != 0.0:
        return [value * scale + offset for value in decoded]
    return decoded


def _values_inline(values: list[int | float | bool] | str) -> str:
    """Компактная текстовая форма values (--text и поле value лога в файл)."""
    if isinstance(values, str):
        return values
    parts = []
    for value in values:
        if isinstance(value, bool):
            parts.append(str(int(value)))
        elif isinstance(value, float):
            parts.append(f"{value:.6g}")
        else:
            parts.append(str(value))
    return ", ".join(parts)


@dataclass
class _MapRow:
    """Строка карты регистров (--map): template/session JSON или CSV таблицы."""

    name: str
    kind: RegisterKind
    address: int
    count: int
    format: DisplayFormat
    scale: float
    offset: float
    order: ByteOrder | None  # None = глобальный --order
    unit_id: int | None  # None = глобальный --unit
    values: list[int | bool] | None  # начальные значения (simulate); None = нули


def _map_entry(data: object) -> _MapRow | None:
    """Толерантный разбор записи «registers» из template/session JSON."""
    if not isinstance(data, dict):
        return None
    try:
        address = int(data.get("address"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if address < 0:
        return None
    kind = _KINDS.get(str(data.get("kind", "holding_registers")).lower())
    if kind is None:
        return None
    try:
        count = int(data.get("count", 1))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if count < 1:
        return None
    fmt = str(data.get("format", "dec")).lower()
    order = str(data.get("order", "")).upper()
    values_data = data.get("values")
    values: list[int | bool] | None = None
    if isinstance(values_data, list) and all(isinstance(v, int | bool) for v in values_data):
        values = [int(v) if not isinstance(v, bool) else v for v in values_data]
    try:
        unit_id = int(data["unit_id"])  # type: ignore[arg-type]
    except (KeyError, TypeError, ValueError):
        unit_id = None
    if unit_id is not None and not 1 <= unit_id <= 247:
        unit_id = None

    def _float(key: str, default: float) -> float:
        try:
            return float(data.get(key, default))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default

    return _MapRow(
        name=str(data.get("name", "")),
        kind=kind,
        address=address,
        count=count,
        format=fmt if fmt in get_args(DisplayFormat) else "dec",  # type: ignore[arg-type]
        scale=_float("scale", 1.0),
        offset=_float("offset", 0.0),
        order=order if order in get_args(ByteOrder) else None,  # type: ignore[arg-type]
        unit_id=unit_id,
        values=values,
    )


def _map_from_csv(text: str) -> list[_MapRow]:
    try:
        pairs = rows_from_csv(text)
    except ValueError as exc:
        raise ValueError(
            f"map file is neither template/session JSON nor register CSV: {exc}"
        ) from exc
    return [
        _MapRow(
            name=row.name,
            kind=row.kind,
            address=row.address,
            count=row.count,
            format=row.format,
            scale=display.scale,
            offset=display.offset,
            order=display.order,
            unit_id=row.unit_id,
            values=None,
        )
        for row, display in pairs
    ]


def _load_map(path: str) -> list[_MapRow]:
    """Прочитать карту регистров: JSON с ключом «registers» или CSV таблицы."""
    try:
        with open(path, encoding="utf-8") as file:
            text = file.read()
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise ValueError(f"cannot read map file {path!r}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return _map_from_csv(text)
    if not isinstance(data, dict) or not isinstance(data.get("registers"), list):
        raise ValueError(f"{path}: expected a JSON object with a 'registers' list (or CSV)")
    rows = [row for entry in data["registers"] if (row := _map_entry(entry)) is not None]
    if not rows:
        raise ValueError(f"{path}: no valid register entries in 'registers'")
    return rows


def _serve_until_interrupt(stop: Any) -> int:
    """Общий хвост потоковых команд: ждать Ctrl+C, корректно остановить backend."""
    try:
        _wait_until_interrupt()
    except KeyboardInterrupt:
        pass
    finally:
        stop()
    return EXIT_OK


def _cmd_read(args: argparse.Namespace) -> int:
    _check_unit(args.unit)
    backend = ModbusBackend()
    backend.connect(_params_from_args(args))
    try:
        raw = backend.read(args.unit, args.kind, args.address, args.count)
    finally:
        backend.disconnect()
    values = _decode_row(raw, args.kind, args.format, args.order, args.scale, args.offset)
    if args.text:
        print(_values_inline(values))
    else:
        _emit(
            {
                "unit": args.unit,
                "kind": args.kind,
                "address": args.address,
                "count": args.count,
                "raw": raw,
                "values": values,
            }
        )
    return EXIT_OK


def _cmd_write(args: argparse.Namespace) -> int:
    _check_unit(args.unit)
    if args.kind not in ("coils", "holding_registers"):
        raise ValueError(f"write supports coils/holding_registers only, got {args.kind!r}")
    values = parse_values(args.kind, " ".join(args.values))
    backend = ModbusBackend()
    backend.connect(_params_from_args(args))
    try:
        backend.write(args.unit, args.kind, args.address, values)
    finally:
        backend.disconnect()
    if args.text:
        print(f"written {len(values)} value(s) to {args.kind}@{args.address}")
    else:
        _emit(
            {
                "unit": args.unit,
                "kind": args.kind,
                "address": args.address,
                "written": len(values),
                "values": values,
            }
        )
    return EXIT_OK


def _poll_rows(args: argparse.Namespace) -> list[_MapRow]:
    if args.map is not None:
        if args.kind is not None or args.address is not None or args.count is not None:
            raise ValueError("--map conflicts with KIND/ADDRESS/COUNT (use one or the other)")
        return _load_map(args.map)
    if args.kind is None or args.address is None or args.count is None:
        raise ValueError("poll needs KIND ADDRESS COUNT (or --map FILE)")
    return [
        _MapRow("", args.kind, args.address, args.count, args.format, args.scale, args.offset,
                None, None, None)
    ]


def _poll_row(
    backend: ModbusBackend, args: argparse.Namespace, row: _MapRow, logger: DataLogger
) -> None:
    unit = row.unit_id or args.unit
    timestamp = _now()
    try:
        raw = backend.read(unit, row.kind, row.address, row.count)
    except Exception as exc:  # ошибка одной строки не останавливает поллинг
        print(f"poll {row.kind}@{row.address}: {exc}", file=sys.stderr, flush=True)
        return
    order = row.order or args.order
    values = _decode_row(raw, row.kind, row.format, order, row.scale, row.offset)
    if args.text:
        name = f"{row.name} " if row.name else ""
        print(f"{timestamp} {name}{row.kind}@{row.address}: {_values_inline(values)}", flush=True)
    else:
        record: dict[str, Any] = {
            "timestamp": timestamp,
            "unit": unit,
            "kind": row.kind,
            "address": row.address,
            "raw": raw,
            "values": values,
        }
        if row.name:
            record["name"] = row.name
        _emit(record)
    if logger.is_open:
        logger.write(
            LogSample(
                timestamp=timestamp,
                name=row.name or f"{row.kind}@{row.address}",
                address=row.address,
                kind=row.kind,
                value=_values_inline(values),
            )
        )


def _cmd_poll(args: argparse.Namespace) -> int:
    _check_unit(args.unit)
    rows = _poll_rows(args)
    logger = DataLogger()
    if args.log is not None:
        log_format = args.log_format or ("jsonl" if args.log.endswith(".jsonl") else "csv")
        logger.open(LogSettings(path=args.log, format=log_format))
    backend = ModbusBackend()
    backend.connect(_params_from_args(args))
    try:
        while True:
            for row in rows:
                _poll_row(backend, args, row, logger)
            logger.flush()
            _sleep(args.interval / 1000.0)
    except KeyboardInterrupt:
        pass
    finally:
        backend.disconnect()
        logger.close()
    return EXIT_OK


def _cmd_scan_units(args: argparse.Namespace) -> int:
    start, end = _parse_range(args.range)
    _check_unit(start)
    _check_unit(end)
    backend = ModbusBackend()
    backend.connect(_params_from_args(args))
    hits: list[dict[str, Any]] = []
    try:
        for unit, probes in backend.scan(DEFAULT_SCAN_PROBES, start, end, lambda: False):
            print(
                f"unit {unit}: {len(probes)}/{len(DEFAULT_SCAN_PROBES)} probes answered",
                file=sys.stderr,
                flush=True,
            )
            if probes:
                hits.append({"unit": unit, "probes": probes})
    except KeyboardInterrupt:
        pass
    finally:
        backend.disconnect()
    if args.text:
        for hit in hits:
            print(f"unit {hit['unit']}: probes {hit['probes']}")
        if not hits:
            print("no units found")
    else:
        _emit({"hits": hits})
    return EXIT_OK


def _cmd_scan_addresses(args: argparse.Namespace) -> int:
    _check_unit(args.unit)
    start, end = _parse_range(args.range)
    backend = ModbusBackend()
    backend.connect(_params_from_args(args))
    hits: list[dict[str, Any]] = []
    try:
        for address, values in backend.scan_addresses(
            args.unit, args.kind, start, end, lambda: False
        ):
            print(f"{args.kind}@{address}: answered", file=sys.stderr, flush=True)
            hits.append({"address": address, "values": list(values)})
    except KeyboardInterrupt:
        pass
    finally:
        backend.disconnect()
    if args.text:
        for hit in hits:
            print(f"{args.kind}@{hit['address']}: {_values_inline(hit['values'])}")
        if not hits:
            print("no addresses found")
    else:
        _emit({"hits": hits})
    return EXIT_OK


def _sim_params(args: argparse.Namespace) -> SimTcpParams | RtuParams:
    """Параметры listen-сервера симулятора; без флага транспорта — TCP 127.0.0.1:1502."""
    if args.tcp is not None:
        host, port = _listen_host_port(args.tcp, "127.0.0.1")
        return SimTcpParams(host, port)
    if args.rtu_over_tcp is not None:
        host, port = _listen_host_port(args.rtu_over_tcp, "127.0.0.1")
        return SimRtuOverTcpParams(host, port)
    if args.rtu is not None:
        return RtuParams(args.rtu, args.baud, args.bits, args.parity, args.stop)
    return SimTcpParams()


def _initial_values(row: _MapRow) -> list[int | bool]:
    """Начальные значения строки карты симулятора: из «values» или нули/False."""
    base: list[int | bool] = (
        [False] * row.count if row.kind in _BIT_KINDS else [0] * row.count
    )
    if not row.values:
        return base
    merged = (list(row.values) + base)[: row.count]
    if row.kind in _BIT_KINDS:
        return [bool(value) for value in merged]
    return [int(value) for value in merged]


def _cmd_simulate(args: argparse.Namespace) -> int:
    if args.unit is not None:
        _check_unit(args.unit)
    rows = _load_map(args.map)
    sim = SimBackend()
    for row in rows:
        sim.set_values(row.kind, row.address, _initial_values(row))
    sim.on_master_write = lambda kind, address, values: _event(
        args,
        {
            "timestamp": _now(),
            "event": "write",
            "kind": kind,
            "address": address,
            "values": [int(v) if not isinstance(v, bool) else v for v in values],
        },
        f"write {kind}@{address} {list(values)}",
    )
    params = _sim_params(args)
    sim.start(params, args.unit)
    print(f"simulator listening: {describe_sim(params)}", file=sys.stderr, flush=True)
    return _serve_until_interrupt(sim.stop)


def _parse_gateway_listen(spec: str) -> GatewayListenParams:
    """«tcp:PORT | tcp:HOST:PORT | rtuovertcp:PORT | rtuovertcp:HOST:PORT | rtu:PORT[,...]»."""
    scheme, sep, rest = spec.partition(":")
    if not sep or not rest:
        raise ValueError(f"bad listen spec {spec!r} (expected SCHEME:ENDPOINT)")
    scheme = scheme.lower().replace("-", "")
    if scheme == "tcp":
        host, port = _listen_host_port(rest, "0.0.0.0")
        return GatewayTcpListenParams(host, port)
    if scheme == "rtuovertcp":
        host, port = _listen_host_port(rest, "0.0.0.0")
        return GatewayRtuOverTcpListenParams(host, port)
    if scheme == "rtu":
        return _parse_rtu_spec(rest)
    raise ValueError(
        f"unknown listen scheme {scheme!r} (tcp / rtuovertcp / rtu)"
    )


def _parse_gateway_target(spec: str, timeout: float) -> ConnectionParams:
    """«tcp:HOST[:PORT] | rtuovertcp:HOST[:PORT] | rtuoverudp:HOST[:PORT] | rtu:PORT[,...]»."""
    scheme, sep, rest = spec.partition(":")
    if not sep or not rest:
        raise ValueError(f"bad target spec {spec!r} (expected SCHEME:ENDPOINT)")
    scheme = scheme.lower().replace("-", "")
    if scheme == "tcp":
        host, port = _client_host_port(rest)
        return TcpParams(host, port, timeout)
    if scheme == "rtuovertcp":
        host, port = _client_host_port(rest)
        return RtuOverTcpParams(host, port, timeout)
    if scheme == "rtuoverudp":
        host, port = _client_host_port(rest)
        return RtuOverUdpParams(host, port, timeout)
    if scheme == "rtu":
        return _parse_rtu_spec(rest, timeout)
    raise ValueError(
        f"unknown target scheme {scheme!r} (tcp / rtuovertcp / rtuoverudp / rtu)"
    )


def _cmd_gateway(args: argparse.Namespace) -> int:
    listen = _parse_gateway_listen(args.listen)
    target = _parse_gateway_target(args.target, args.timeout)
    units = _parse_units(args.units)
    gateway = GatewayBackend()
    gateway.on_request = lambda line: _event(
        args, {"timestamp": _now(), "event": "request", "line": line}, line
    )
    gateway.on_error = lambda message: print(
        f"gateway error: {message}", file=sys.stderr, flush=True
    )
    gateway.start(listen, target, units)
    print(f"gateway listening: {describe_gateway(listen, target)}", file=sys.stderr, flush=True)
    return _serve_until_interrupt(gateway.stop)


def _cmd_sniff(args: argparse.Namespace) -> int:
    params = RtuParams(args.rtu, args.baud, args.bits, args.parity, args.stop)
    sniffer = SnifferBackend()
    sniffer.on_frame = lambda line: _event(
        args, {"timestamp": _now(), "event": "frame", "line": line}, line
    )
    sniffer.on_values = lambda unit, kind, address, values: _event(
        args,
        {
            "timestamp": _now(),
            "event": "values",
            "unit": unit,
            "kind": kind,
            "address": address,
            "values": [int(v) if not isinstance(v, bool) else v for v in values],
        },
        f"unit {unit} {kind}@{address} {list(values)}",
    )
    sniffer.on_error = lambda message: print(
        f"sniffer error: {message}", file=sys.stderr, flush=True
    )
    sniffer.start(params)
    print(f"sniffing: {describe_sniffer(params)}", file=sys.stderr, flush=True)
    return _serve_until_interrupt(sniffer.stop)


def _add_serial_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--baud", type=int, default=9600, metavar="BPS",
                        help="serial baud rate (default 9600)")
    parser.add_argument("--bits", type=int, choices=(7, 8), default=8,
                        help="serial data bits (default 8)")
    parser.add_argument("--parity", choices=("N", "E", "O"), default="N",
                        help="serial parity (default N)")
    parser.add_argument("--stop", type=int, choices=(1, 2), default=1,
                        help="serial stop bits (default 1)")


def _add_text_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--text", action="store_true",
                        help="human-readable output instead of JSON")


def _connection_parent() -> argparse.ArgumentParser:
    """Общие флаги клиентского подключения; ровно один транспорт обязателен."""
    parent = _CliParser(add_help=False)
    transport = parent.add_mutually_exclusive_group(required=True)
    transport.add_argument("--tcp", metavar="HOST[:PORT]",
                           help="Modbus TCP (default port 502)")
    transport.add_argument("--rtu", metavar="PORT", help="Modbus RTU serial port")
    transport.add_argument("--rtu-over-tcp", metavar="HOST[:PORT]",
                           help="RTU framing over TCP (default port 502)")
    transport.add_argument("--rtu-over-udp", metavar="HOST[:PORT]",
                           help="RTU framing over UDP (default port 502)")
    _add_serial_args(parent)
    parent.add_argument("--unit", type=int, default=1, metavar="N",
                        help="unit id 1..247 (default 1)")
    parent.add_argument("--timeout", type=float, default=3.0, metavar="SEC",
                        help="response timeout in seconds (default 3.0)")
    _add_text_arg(parent)
    return parent


def _build_parser() -> argparse.ArgumentParser:
    parser = _CliParser(
        prog="modbus-connector-cli",
        description="Modbus command line tool: read/write/poll/scan plus device "
        "simulator, gateway and RTU bus sniffer. Data goes to stdout as compact "
        "JSON (streaming commands emit NDJSON), diagnostics to stderr.",
        epilog=_EXIT_CODES_TEXT,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {_version()}")
    common = _connection_parent()
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    formatter = argparse.RawDescriptionHelpFormatter

    p_read = sub.add_parser(
        "read", parents=[common], formatter_class=formatter,
        help="read registers/coils once",
        epilog="examples:\n"
        "  modbus-connector-cli read hr 0 4 --tcp 192.168.1.10\n"
        "  modbus-connector-cli read coils 0 8 --rtu /dev/ttyUSB0 --baud 19200 --text\n\n"
        + _EXIT_CODES_TEXT,
    )
    p_read.add_argument("kind", type=_kind, metavar="KIND",
                        help="coils|di|hr|ir (or full names: discrete_inputs, "
                        "holding_registers, input_registers)")
    p_read.add_argument("address", type=_nonneg_int, metavar="ADDRESS")
    p_read.add_argument("count", type=_positive_int, metavar="COUNT")
    p_read.add_argument("--format", choices=get_args(DisplayFormat), default="dec",
                        help="value decoding for register areas (default dec)")
    p_read.add_argument("--order", choices=get_args(ByteOrder), default="ABCD",
                        help="byte order for 32/64-bit formats (default ABCD)")
    p_read.add_argument("--scale", type=float, default=1.0)
    p_read.add_argument("--offset", type=float, default=0.0)
    p_read.set_defaults(func=_cmd_read)

    p_write = sub.add_parser(
        "write", parents=[common], formatter_class=formatter,
        help="write coils/holding registers",
        epilog="examples:\n"
        "  modbus-connector-cli write hr 10 100 200 --tcp 192.168.1.10\n"
        "  modbus-connector-cli write coils 0 1 0 true --rtu /dev/ttyUSB0\n\n"
        + _EXIT_CODES_TEXT,
    )
    p_write.add_argument("kind", type=_kind, metavar="KIND",
                         help="coils|hr (or full names)")
    p_write.add_argument("address", type=_nonneg_int, metavar="ADDRESS")
    p_write.add_argument("values", nargs="+", metavar="VALUE",
                         help="integers (0x.. ok) for registers, 0/1/true/false/on/off "
                         "for coils; commas allowed")
    p_write.set_defaults(func=_cmd_write)

    p_poll = sub.add_parser(
        "poll", parents=[common], formatter_class=formatter,
        help="poll values until Ctrl+C (NDJSON per cycle)",
        epilog="examples:\n"
        "  modbus-connector-cli poll hr 0 2 --interval 500 --tcp 192.168.1.10\n"
        "  modbus-connector-cli poll --map device.json --log values.csv "
        "--tcp 192.168.1.10\n\n"
        "map file: template/session JSON ({\"registers\": [{name, kind, address, "
        "count, format, ...}]}) or register CSV.\n\n" + _EXIT_CODES_TEXT,
    )
    p_poll.add_argument("kind", nargs="?", type=_kind, metavar="KIND")
    p_poll.add_argument("address", nargs="?", type=_nonneg_int, metavar="ADDRESS")
    p_poll.add_argument("count", nargs="?", type=_positive_int, metavar="COUNT")
    p_poll.add_argument("--interval", type=_positive_int, default=1000, metavar="MS",
                        help="poll interval in milliseconds (default 1000)")
    p_poll.add_argument("--log", metavar="PATH", help="also log values to a file")
    p_poll.add_argument("--log-format", choices=("csv", "jsonl"), default=None,
                        help="log file format (default: by extension, else csv)")
    p_poll.add_argument("--map", metavar="FILE",
                        help="poll all rows of a register map (template/session JSON "
                        "or CSV); KIND/ADDRESS/COUNT are not needed then")
    p_poll.add_argument("--format", choices=get_args(DisplayFormat), default="dec")
    p_poll.add_argument("--order", choices=get_args(ByteOrder), default="ABCD")
    p_poll.add_argument("--scale", type=float, default=1.0)
    p_poll.add_argument("--offset", type=float, default=0.0)
    p_poll.set_defaults(func=_cmd_poll)

    p_scan = sub.add_parser(
        "scan", formatter_class=formatter,
        help="scan unit ids or register addresses",
        epilog="examples:\n"
        "  modbus-connector-cli scan units 1-10 --tcp 192.168.1.10\n"
        "  modbus-connector-cli scan addresses hr 0-100 --rtu /dev/ttyUSB0\n\n"
        + _EXIT_CODES_TEXT,
    )
    scan_sub = p_scan.add_subparsers(dest="scan_command", required=True, metavar="MODE")
    p_units = scan_sub.add_parser(
        "units", parents=[common], formatter_class=formatter,
        help="scan unit ids with default probes",
        epilog="example:\n"
        "  modbus-connector-cli scan units 1-10 --tcp 192.168.1.10\n\n"
        + _EXIT_CODES_TEXT,
    )
    p_units.add_argument("range", metavar="START-END", help="unit id range (1..247)")
    p_units.set_defaults(func=_cmd_scan_units)
    p_addresses = scan_sub.add_parser(
        "addresses", parents=[common], formatter_class=formatter,
        help="scan register addresses of one unit",
        epilog="example:\n"
        "  modbus-connector-cli scan addresses hr 0-100 --tcp 192.168.1.10 --unit 5\n\n"
        + _EXIT_CODES_TEXT,
    )
    p_addresses.add_argument("kind", type=_kind, metavar="KIND")
    p_addresses.add_argument("range", metavar="START-END", help="address range")
    p_addresses.set_defaults(func=_cmd_scan_addresses)

    p_sim = sub.add_parser(
        "simulate", formatter_class=formatter,
        help="run a Modbus slave simulator with a register map",
        epilog="examples:\n"
        "  modbus-connector-cli simulate --map device.json --tcp 1502\n"
        "  modbus-connector-cli simulate --map map.csv --rtu /dev/ttyUSB0 --unit 5\n\n"
        "map file: template/session JSON ({\"registers\": [{name, kind, address, "
        "count, format, values, ...}]}) or register CSV; entries without 'values' "
        "start at 0/False. Default listen: tcp 127.0.0.1:1502.\n\n" + _EXIT_CODES_TEXT,
    )
    p_sim.add_argument("--map", required=True, metavar="FILE", help="register map file")
    sim_transport = p_sim.add_mutually_exclusive_group()
    sim_transport.add_argument("--tcp", metavar="[HOST:]PORT",
                               help="Modbus TCP listen (default host 127.0.0.1)")
    sim_transport.add_argument("--rtu-over-tcp", metavar="[HOST:]PORT",
                               help="RTU framing over TCP listen")
    sim_transport.add_argument("--rtu", metavar="PORT", help="Modbus RTU serial listen")
    _add_serial_args(p_sim)
    p_sim.add_argument("--unit", type=int, default=None, metavar="N",
                       help="answer only this unit id (default: any)")
    _add_text_arg(p_sim)
    p_sim.set_defaults(func=_cmd_simulate)

    p_gw = sub.add_parser(
        "gateway", formatter_class=formatter,
        help="transparent Modbus gateway: listen server forwarding to a target",
        epilog="examples:\n"
        "  modbus-connector-cli gateway --listen tcp:1502 --target rtu:/dev/ttyUSB0,baud=19200\n"
        "  modbus-connector-cli gateway --listen rtu:/dev/ttyS1 --target tcp:192.168.1.10 "
        '--units "1,5,10-20"\n\n'
        "listen spec:  tcp:PORT | tcp:HOST:PORT | rtuovertcp:PORT | "
        "rtuovertcp:HOST:PORT\n"
        "              | rtu:PORT[,baud=9600][,bits=8][,parity=N][,stop=1]\n"
        "target spec:  tcp:HOST[:PORT] | rtuovertcp:HOST[:PORT] | "
        "rtuoverudp:HOST[:PORT]\n"
        "              | rtu:PORT[,baud=9600][,bits=8][,parity=N][,stop=1]\n\n"
        + _EXIT_CODES_TEXT,
    )
    p_gw.add_argument("--listen", required=True, metavar="SPEC",
                      help="listen endpoint spec (see below)")
    p_gw.add_argument("--target", required=True, metavar="SPEC",
                      help="target endpoint spec (see below)")
    p_gw.add_argument("--units", metavar="LIST",
                      help='unit ids to serve, e.g. "1,5,10-20" (default: all 1..247)')
    p_gw.add_argument("--timeout", type=float, default=3.0, metavar="SEC",
                      help="target response timeout in seconds (default 3.0)")
    _add_text_arg(p_gw)
    p_gw.set_defaults(func=_cmd_gateway)

    p_sniff = sub.add_parser(
        "sniff", formatter_class=formatter,
        help="passively sniff an RTU bus (frames + restored values)",
        epilog="examples:\n"
        "  modbus-connector-cli sniff --rtu /dev/ttyUSB1 --baud 19200\n"
        "  modbus-connector-cli sniff --rtu COM4 --text\n\n" + _EXIT_CODES_TEXT,
    )
    p_sniff.add_argument("--rtu", required=True, metavar="PORT",
                         help="serial port of a separate listen-only adapter")
    _add_serial_args(p_sniff)
    _add_text_arg(p_sniff)
    p_sniff.set_defaults(func=_cmd_sniff)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Точка входа CLI; SystemExit (0/4) — от argparse (--help/--version/ошибки разбора)."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        result: int = args.func(args)
        return result
    except KeyboardInterrupt:
        return EXIT_OK
    except ModbusExceptionError as exc:
        print(f"error: {exc} [exception code 0x{exc.exception_code:02X}]", file=sys.stderr)
        return EXIT_MODBUS
    except FileNotFoundError as exc:  # раньше OSError: входные файлы — код 4
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except (ConnectionError, ModbusIOException, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONNECTION
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
