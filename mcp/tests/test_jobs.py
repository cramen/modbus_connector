"""Тесты JobRegistry: gateway/simulate/poll-задачи против тестового сервера."""

import socket
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from modbus_connector_mcp.jobs import JobRegistry

from modbus_connector.backend import ModbusBackend
from modbus_connector.models import TcpParams


def _wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture()
def registry() -> Iterator[JobRegistry]:
    reg = JobRegistry()
    yield reg
    reg.stop_all()


def _read_events(registry: JobRegistry, job_id: str, since_seq: int = 0) -> dict[str, Any]:
    return registry.events(job_id, since_seq=since_seq)


def test_job_ids_and_unknown(registry: JobRegistry) -> None:
    with pytest.raises(ValueError, match="unknown job kind"):
        registry.start("teleport", {})
    with pytest.raises(ValueError, match="unknown job"):
        registry.stop("poll-42")
    with pytest.raises(ValueError, match="unknown job"):
        registry.events("poll-42")


def test_gateway_job_forwards_read(
    registry: JobRegistry, modbus_server: int, unused_port: int
) -> None:
    listen_port = unused_port
    job = registry.start(
        "gateway",
        {"listen": f"tcp:127.0.0.1:{listen_port}", "target": f"tcp:127.0.0.1:{modbus_server}"},
    )
    assert job.job_id == "gateway-1"
    assert job.status == "running"
    client = ModbusBackend()
    client.connect(TcpParams("127.0.0.1", listen_port))
    try:
        assert client.read(1, "holding_registers", 0, 4) == [100, 101, 102, 103]
        client.write(1, "holding_registers", 5, [55])
        assert client.read(1, "holding_registers", 5, 1) == [55]
    finally:
        client.disconnect()
    assert _wait_for(
        lambda: any(
            "read holding_registers" in event["data"].get("line", "")
            for event in _read_events(registry, job.job_id)["events"]
        )
    )
    # инкрементальное чтение: since_seq отсекает уже полученные события
    first = _read_events(registry, job.job_id)
    assert first["events"]
    assert first["next_seq"] == first["events"][-1]["seq"]
    again = _read_events(registry, job.job_id, since_seq=first["next_seq"])
    assert again["events"] == []
    assert again["next_seq"] == first["next_seq"]
    stopped = registry.stop(job.job_id)
    assert stopped.status == "stopped"
    assert registry.status(job.job_id)["status"] == "stopped"


def test_simulate_job_logs_master_write(registry: JobRegistry, unused_port: int) -> None:
    listen_port = unused_port
    job = registry.start(
        "simulate",
        {
            "listen": f"tcp:127.0.0.1:{listen_port}",
            "map": {"registers": [{"kind": "hr", "address": 0, "values": [42]}]},
        },
    )
    assert job.job_id == "simulate-1"
    client = ModbusBackend()
    client.connect(TcpParams("127.0.0.1", listen_port))
    try:
        assert client.read(1, "holding_registers", 0, 1) == [42]  # карта применилась
        client.write(1, "holding_registers", 5, [1, 2])
    finally:
        client.disconnect()
    assert _wait_for(
        lambda: any(
            event["data"].get("event") == "write"
            and event["data"].get("address") == 5
            and event["data"].get("values") == [1, 2]
            for event in _read_events(registry, job.job_id)["events"]
        )
    )
    registry.stop(job.job_id)


def test_poll_job_emits_values(registry: JobRegistry, modbus_server: int) -> None:
    job = registry.start(
        "poll",
        {
            "conn": f"tcp:127.0.0.1:{modbus_server}",
            "unit": 1,
            "kind": "hr",
            "address": 0,
            "count": 2,
            "interval_ms": 50,
        },
    )
    assert job.job_id == "poll-1"
    assert _wait_for(lambda: len(_read_events(registry, job.job_id)["events"]) >= 2)
    event = _read_events(registry, job.job_id)["events"][0]
    assert event["data"]["raw"] == [100, 101]
    assert event["data"]["values"] == [100, 101]
    registry.stop(job.job_id)
    # после остановки события не прибавляются
    count = len(_read_events(registry, job.job_id)["events"])
    time.sleep(0.2)
    assert len(_read_events(registry, job.job_id)["events"]) == count


def test_poll_job_validation(registry: JobRegistry) -> None:
    with pytest.raises(ValueError, match="missing required param 'conn'"):
        registry.start("poll", {"kind": "hr", "address": 0, "count": 1})
    with pytest.raises(ValueError, match="unknown register area"):
        registry.start("poll", {"conn": "tcp:127.0.0.1:1", "kind": "bogus",
                                "address": 0, "count": 1})
    assert registry.list() == []  # неудачный start не оставляет задач


def test_stop_all_releases_port(
    registry: JobRegistry, modbus_server: int, unused_port: int
) -> None:
    listen_port = unused_port
    registry.start(
        "gateway",
        {"listen": f"tcp:127.0.0.1:{listen_port}", "target": f"tcp:127.0.0.1:{modbus_server}"},
    )
    registry.stop_all()
    with socket.socket() as probe:  # порт освободился — bind проходит
        probe.bind(("127.0.0.1", listen_port))
    assert registry.list()[0]["status"] == "stopped"


def test_sniff_bad_port(registry: JobRegistry) -> None:
    # реального serial-порта в тестах нет: проверяем, что ошибка открытия порта
    # прилетает синхронно и задача не регистрируется
    with pytest.raises(ConnectionError, match="Не удалось открыть порт"):
        registry.start("sniff", {"port": "/dev/modbus-mcp-nonexistent"})
    assert registry.list() == []


def test_read_only_registry(unused_port: int) -> None:
    registry = JobRegistry(read_only=True)
    try:
        with pytest.raises(ValueError, match="read-only"):
            registry.start("simulate", {"listen": f"tcp:127.0.0.1:{unused_port}"})
        with pytest.raises(ValueError, match="read-only"):
            registry.start("gateway", {})
        # sniff/poll в read-only разрешены (падают уже на валидации/сети, не на режиме)
        with pytest.raises(ConnectionError):
            registry.start("sniff", {"port": "/dev/modbus-mcp-nonexistent"})
    finally:
        registry.stop_all()
