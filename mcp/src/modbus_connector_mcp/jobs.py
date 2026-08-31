"""Фоновые задачи MCP-сервера: simulate/gateway/sniff/poll с журналом событий.

Хуки backend'ов вызываются из их потоков, поэтому события складываются в
кольцевой буфер задачи под Lock; чтение буфера (events_since) — тоже под ним.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from datetime import datetime
from typing import Any, get_args

from modbus_connector.backend import ModbusBackend
from modbus_connector.conn_spec import parse_connection_spec, parse_listen_spec
from modbus_connector.gateway_backend import (
    GatewayBackend,
    GatewayRtuOverTcpListenParams,
    GatewayTcpListenParams,
)
from modbus_connector.models import (
    ByteOrder,
    ConnectionParams,
    DisplayFormat,
    RegisterKind,
    RtuParams,
    decode_register_values,
    format_register_values,
)
from modbus_connector.sim_backend import SimBackend, SimRtuOverTcpParams, SimTcpParams
from modbus_connector.sniffer_backend import SnifferBackend

logger = logging.getLogger(__name__)

JOB_KINDS = ("simulate", "gateway", "sniff", "poll")
WRITE_JOB_KINDS = ("simulate", "gateway")  # поднимают сервер — в read-only запрещены

MAX_EVENTS = 1000  # событий в кольцевом буфере задачи

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


def parse_kind(text: str) -> RegisterKind:
    """«coils|di|hr|ir» или полное имя области → RegisterKind."""
    kind = _KINDS.get(str(text).lower())
    if kind is None:
        raise ValueError(f"unknown register area {text!r} (coils/di/hr/ir or full names)")
    return kind


def parse_format(text: str) -> DisplayFormat:
    fmt = str(text).lower()
    if fmt not in get_args(DisplayFormat):
        raise ValueError(f"unknown format {text!r} ({'/'.join(get_args(DisplayFormat))})")
    return fmt  # type: ignore[return-value]


def parse_order(text: str) -> ByteOrder:
    order = str(text).upper()
    if order not in get_args(ByteOrder):
        raise ValueError(f"unknown byte order {text!r} ({'/'.join(get_args(ByteOrder))})")
    return order  # type: ignore[return-value]


def decode_row(
    raw: list[int | bool],
    kind: RegisterKind,
    fmt: DisplayFormat,
    order: ByteOrder,
    scale: float = 1.0,
    offset: float = 0.0,
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


def _require(params: dict[str, Any], key: str) -> Any:
    value = params.get(key)
    if value is None:
        raise ValueError(f"missing required param {key!r}")
    return value


def _int_param(
    params: dict[str, Any],
    key: str,
    default: int | None = None,
    minimum: int | None = None,
) -> int:
    value = params.get(key, default)
    if value is None:
        raise ValueError(f"missing required param {key!r}")
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"param {key!r} must be an integer, got {value!r}") from None
    if minimum is not None and result < minimum:
        raise ValueError(f"param {key!r} must be >= {minimum}, got {result}")
    return result


def _unit_id(value: Any) -> int:
    unit = _int_param({"unit": value}, "unit")
    if not 1 <= unit <= 247:
        raise ValueError(f"unit id out of range 1..247: {unit}")
    return unit


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


class Job:
    """Одна фоновая задача: runner (backend/PollRunner) + кольцевой буфер событий."""

    def __init__(self, job_id: str, kind: str, params: dict[str, Any]) -> None:
        self.job_id = job_id
        self.kind = kind
        self.params = params
        self.status = "running"
        self.started_at = _now()
        self.runner: Any = None
        self._events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
        self._seq = 0
        self._lock = threading.Lock()

    def emit(self, data: dict[str, Any]) -> None:
        """Добавить событие в буфер (вызывается из потоков backend'ов)."""
        with self._lock:
            self._seq += 1
            self._events.append({"seq": self._seq, "timestamp": _now(), "data": data})

    def events_since(self, since_seq: int, limit: int) -> dict[str, Any]:
        with self._lock:
            events = [event for event in self._events if event["seq"] > since_seq][:limit]
        return {"events": events, "next_seq": events[-1]["seq"] if events else since_seq}

    def summary(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "status": self.status,
            "started_at": self.started_at,
            "params": self.params,
        }

    def fail(self) -> None:
        with self._lock:
            self.status = "error"

    def stop(self) -> None:
        runner = self.runner
        if runner is not None:
            try:
                runner.stop()
            except Exception:
                logger.exception("Ошибка остановки задачи %s", self.job_id)
        with self._lock:
            if self.status == "running":
                self.status = "stopped"


class PollRunner:
    """Циклическое чтение одной области регистров в потоке; каждый цикл — событие."""

    def __init__(self, params: dict[str, Any], emit: Callable[[dict[str, Any]], None]) -> None:
        self._conn: ConnectionParams = parse_connection_spec(str(_require(params, "conn")))
        self._unit = _unit_id(params.get("unit", 1))
        self._kind = parse_kind(str(_require(params, "kind")))
        self._address = _int_param(params, "address", minimum=0)
        self._count = _int_param(params, "count", minimum=1)
        self._interval_ms = _int_param(params, "interval_ms", default=1000, minimum=1)
        self._format = parse_format(str(params.get("format", "dec")))
        self._order = parse_order(str(params.get("order", "ABCD")))
        self._emit = emit
        self._backend = ModbusBackend()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._backend.connect(self._conn)  # ConnectionError, если target недоступен
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        interval = self._interval_ms / 1000.0
        while not self._stop.is_set():
            try:
                raw = self._backend.read(self._unit, self._kind, self._address, self._count)
                values = decode_row(raw, self._kind, self._format, self._order)
                self._emit({"raw": raw, "values": values})
            except Exception as exc:  # ошибка одного цикла не останавливает поллинг
                self._emit({"error": str(exc)})
            self._stop.wait(interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._backend.disconnect()


def _sim_listen_params(spec: str) -> SimTcpParams | RtuParams:
    """Listen-спек шлюзового формата → параметры симулятора (те же схемы, свой dataclass)."""
    listen = parse_listen_spec(spec)
    if isinstance(listen, GatewayRtuOverTcpListenParams):
        return SimRtuOverTcpParams(listen.host, listen.port)
    if isinstance(listen, GatewayTcpListenParams):
        return SimTcpParams(listen.host, listen.port)
    return listen


def _apply_map(sim: SimBackend, map_data: Any) -> None:
    """Начальные значения карты: объект с ключом «registers» или сам список записей."""
    if map_data is None:
        return
    entries = map_data.get("registers") if isinstance(map_data, dict) else map_data
    if not isinstance(entries, list):
        raise ValueError("simulate 'map' must be an object with a 'registers' list")
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            kind = parse_kind(str(entry.get("kind", "holding_registers")))
            address = _int_param(entry, "address", minimum=0)
        except ValueError:
            continue
        raw_values = entry.get("values")
        if isinstance(raw_values, list) and raw_values:
            if kind in _BIT_KINDS:
                values: list[int | bool] = [bool(v) for v in raw_values]
            else:
                values = [int(v) for v in raw_values]
        else:
            count = _int_param(entry, "count", default=1, minimum=1)
            values = [False] * count if kind in _BIT_KINDS else [0] * count
        sim.set_values(kind, address, values)


def _start_simulate(params: dict[str, Any], job: Job) -> SimBackend:
    listen = _sim_listen_params(str(params.get("listen", "tcp:127.0.0.1:1502")))
    unit = params.get("unit")
    sim = SimBackend()
    sim.on_master_write = lambda kind, address, values: job.emit(
        {
            "event": "write",
            "kind": kind,
            "address": address,
            "values": [v if isinstance(v, bool) else int(v) for v in values],
        }
    )
    sim.on_request = lambda line: job.emit({"event": "request", "line": line})
    try:
        _apply_map(sim, params.get("map"))
        sim.start(listen, _unit_id(unit) if unit is not None else None)
    except Exception:
        sim.stop()
        raise
    return sim


def _start_gateway(params: dict[str, Any], job: Job) -> GatewayBackend:
    listen = parse_listen_spec(str(_require(params, "listen")))
    target = parse_connection_spec(str(_require(params, "target")))
    units_value = params.get("units")
    units = {_unit_id(u) for u in units_value} if isinstance(units_value, list) else None
    gateway = GatewayBackend()
    gateway.on_request = lambda line: job.emit({"event": "request", "line": line})

    def _on_error(message: str) -> None:
        job.emit({"event": "error", "message": message})
        job.fail()

    gateway.on_error = _on_error
    try:
        gateway.start(listen, target, units)
    except Exception:
        gateway.stop()
        raise
    return gateway


def _start_sniff(params: dict[str, Any], job: Job) -> SnifferBackend:
    parity = str(params.get("parity", "N")).upper()
    if parity not in ("N", "E", "O"):
        raise ValueError(f"bad parity {parity!r} (N/E/O)")
    rtu = RtuParams(
        port=str(_require(params, "port")),
        baudrate=_int_param(params, "baud", default=9600, minimum=1),
        bytesize=_int_param(params, "bits", default=8, minimum=1),
        parity=parity,
        stopbits=_int_param(params, "stop", default=1, minimum=1),
    )
    sniffer = SnifferBackend()
    sniffer.on_frame = lambda line: job.emit({"event": "frame", "line": line})
    sniffer.on_values = lambda unit, kind, address, values: job.emit(
        {
            "event": "values",
            "unit": unit,
            "kind": kind,
            "address": address,
            "values": [v if isinstance(v, bool) else int(v) for v in values],
        }
    )

    def _on_error(message: str) -> None:
        job.emit({"event": "error", "message": message})
        job.fail()

    sniffer.on_error = _on_error
    sniffer.start(rtu)  # ConnectionError синхронно, если порт не открывается
    return sniffer


def _start_poll(params: dict[str, Any], job: Job) -> PollRunner:
    runner = PollRunner(params, job.emit)
    runner.start()
    return runner


def _start_runner(kind: str, params: dict[str, Any], job: Job) -> Any:
    if kind == "simulate":
        return _start_simulate(params, job)
    if kind == "gateway":
        return _start_gateway(params, job)
    if kind == "sniff":
        return _start_sniff(params, job)
    return _start_poll(params, job)


class JobRegistry:
    """Реестр фоновых задач: start/stop/list/status/events, потокобезопасно."""

    def __init__(self, read_only: bool = False) -> None:
        self._read_only = read_only
        self._jobs: dict[str, Job] = {}
        self._counters: dict[str, int] = {}
        self._lock = threading.Lock()

    def start(self, kind: str, params: dict[str, Any]) -> Job:
        """Создать задачу и запустить её runner; ошибка запуска — исключение."""
        if kind not in JOB_KINDS:
            raise ValueError(f"unknown job kind {kind!r} ({'/'.join(JOB_KINDS)})")
        if self._read_only and kind in WRITE_JOB_KINDS:
            raise ValueError(f"job kind {kind!r} is not allowed in read-only mode")
        if not isinstance(params, dict):
            raise ValueError("job params must be an object")
        with self._lock:
            self._counters[kind] = self._counters.get(kind, 0) + 1
            job_id = f"{kind}-{self._counters[kind]}"
        job = Job(job_id, kind, dict(params))
        job.runner = _start_runner(kind, params, job)  # поднимает сеть — может блокировать
        with self._lock:
            self._jobs[job_id] = job
        return job

    def _get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(str(job_id))
        if job is None:
            raise ValueError(f"unknown job {job_id!r}")
        return job

    def stop(self, job_id: str) -> Job:
        job = self._get(job_id)
        job.stop()
        return job

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = list(self._jobs.values())
        return [job.summary() for job in jobs]

    def status(self, job_id: str) -> dict[str, Any]:
        return self._get(job_id).summary()

    def events(self, job_id: str, since_seq: int = 0, limit: int = 100) -> dict[str, Any]:
        return self._get(job_id).events_since(max(0, int(since_seq)), max(1, int(limit)))

    def stop_all(self) -> None:
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            job.stop()
