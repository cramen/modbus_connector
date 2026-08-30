import os
import socket
import time
from collections.abc import Iterator
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Qt needs system libraries (libEGL etc.) that headless Linux CI runners lack;
# skip the whole module there — it still runs on macOS/Windows CI and dev machines.
try:
    from PySide6.QtCore import (
        QMetaObject,
        QObject,
        Qt,
        QThread,
        Signal,
    )
    from PySide6.QtTest import QSignalSpy
    from PySide6.QtWidgets import QApplication
except ImportError as exc:
    pytest.skip(f"Qt system libraries not available: {exc}", allow_module_level=True)

from pymodbus.client import ModbusTcpClient  # noqa: E402

from modbus_connector.gateway_backend import GatewayTcpListenParams  # noqa: E402
from modbus_connector.gateway_worker import GatewayWorker  # noqa: E402
from modbus_connector.models import TcpParams  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def gateway_worker(qapp: QApplication) -> Iterator[GatewayWorker]:
    thread = QThread()
    worker = GatewayWorker()
    worker.moveToThread(thread)
    thread.start()
    yield worker
    if thread.isRunning():
        QMetaObject.invokeMethod(
            worker, "shutdown", Qt.ConnectionType.BlockingQueuedConnection
        )
        thread.quit()
        assert thread.wait(5000)
    worker.deleteLater()
    qapp.processEvents()


def _invoke(worker: GatewayWorker, method: str, *args: Any) -> Any:
    return QMetaObject.invokeMethod(
        worker, method, Qt.ConnectionType.BlockingQueuedConnection, *args
    )


class _Starter(QObject):
    """Сигнальный мост: слот (object, object, object) с dataclass-параметрами
    не маршаллится через Q_ARG, зато сигнал→слот доставляет PyObject как есть
    (так UI и будет вызывать start_gateway)."""

    startRequested = Signal(object, object, object)


def _start(
    worker: GatewayWorker, listen_port: int, target_port: int,
    units: set[int] | None = None,
) -> None:
    starter = _Starter()
    starter.startRequested.connect(
        worker.start_gateway, Qt.ConnectionType.BlockingQueuedConnection
    )
    starter.startRequested.emit(
        GatewayTcpListenParams("127.0.0.1", listen_port),
        TcpParams("127.0.0.1", target_port, timeout=1.0),
        units,
    )


def _collect_until(spy: QSignalSpy, predicate: Any, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        spy.wait(50)
    return predicate()


def _all_args(spy: QSignalSpy) -> list[list]:
    return [list(spy.at(i)) for i in range(spy.count())]


def test_start_stop_gateway(gateway_worker: GatewayWorker, modbus_server: int) -> None:
    spy = QSignalSpy(gateway_worker.gatewayChanged)
    listen_port = _free_port()
    _start(gateway_worker, listen_port, modbus_server)
    # _start блокируется до конца слота, а QSignalSpy пишет синхронно на
    # эмиссии — сигнал уже записан, ждать нечего
    assert spy.count() >= 1
    ok, message = spy.at(0)
    assert ok is True
    assert f"gw tcp 127.0.0.1:{listen_port}" in message

    _invoke(gateway_worker, "stop_gateway")
    assert _collect_until(spy, lambda: spy.count() >= 2)
    ok, message = spy.at(1)
    assert ok is False
    assert message == "Stopped"


def test_forward_read_and_write(gateway_worker: GatewayWorker, modbus_server: int) -> None:
    listen_port = _free_port()
    _start(gateway_worker, listen_port, modbus_server)
    spy = QSignalSpy(gateway_worker.logLine)
    client = ModbusTcpClient("127.0.0.1", port=listen_port)
    probe = ModbusTcpClient("127.0.0.1", port=modbus_server)
    try:
        assert client.connect()
        assert probe.connect()
        # чтение через шлюз: фикстура отдаёт hr 0..9 = 100..109
        result = client.read_holding_registers(0, count=2, slave=1)
        assert not result.isError()
        assert result.registers == [100, 101]
        # запись через шлюз доезжает до target
        client.write_register(5, 777, slave=1)
        assert probe.read_holding_registers(5, count=1, slave=1).registers == [777]
        assert _collect_until(
            spy,
            lambda: any(
                "-> unit 1 read holding_registers@0" in args[0]
                for args in _all_args(spy)
            ),
        )
        assert any(
            "-> unit 1 write holding_registers@5" in args[0] for args in _all_args(spy)
        )
        assert any("<- ok" in args[0] for args in _all_args(spy))
    finally:
        client.close()
        probe.close()


def test_units_filter(gateway_worker: GatewayWorker, modbus_server: int) -> None:
    listen_port = _free_port()
    # target-фикстура обслуживает только unit 1 — фильтруем {1}
    _start(gateway_worker, listen_port, modbus_server, units={1})
    client = ModbusTcpClient("127.0.0.1", port=listen_port, timeout=0.5)
    try:
        assert client.connect()
        # unit 1 обслуживается, unit 2 — молчание (таймаут у мастера)
        result = client.read_holding_registers(0, count=1, slave=1)
        assert not result.isError()
        assert result.registers == [100]
        result = client.read_holding_registers(0, count=1, slave=2)
        assert result is None or result.isError()
    finally:
        client.close()


def test_start_busy_port(gateway_worker: GatewayWorker, modbus_server: int) -> None:
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        listen_port = blocker.getsockname()[1]
        spy = QSignalSpy(gateway_worker.gatewayChanged)
        _start(gateway_worker, listen_port, modbus_server)
        assert spy.count() >= 1
        ok, message = spy.at(0)
        assert ok is False
        assert str(listen_port) in message


def test_start_dead_target(gateway_worker: GatewayWorker) -> None:
    spy = QSignalSpy(gateway_worker.gatewayChanged)
    _start(gateway_worker, _free_port(), _free_port())  # target никто не слушает
    assert spy.count() >= 1
    ok, _message = spy.at(0)
    assert ok is False


def test_client_changed(gateway_worker: GatewayWorker, modbus_server: int) -> None:
    listen_port = _free_port()
    _start(gateway_worker, listen_port, modbus_server)
    spy = QSignalSpy(gateway_worker.clientChanged)
    client = ModbusTcpClient("127.0.0.1", port=listen_port)
    try:
        assert client.connect()
        assert _collect_until(
            spy, lambda: any(args[0] is True for args in _all_args(spy))
        )
    finally:
        client.close()
    assert _collect_until(
        spy, lambda: spy.count() >= 2 and spy.at(spy.count() - 1)[0] is False
    )
