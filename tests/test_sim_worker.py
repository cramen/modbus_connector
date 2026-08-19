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
        Q_ARG,
        Q_RETURN_ARG,
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

from modbus_connector.sim_backend import SimTcpParams  # noqa: E402
from modbus_connector.sim_worker import SimWorker  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def sim_worker(qapp: QApplication) -> Iterator[SimWorker]:
    thread = QThread()
    worker = SimWorker()
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


def _invoke(worker: SimWorker, method: str, *args: Any) -> Any:
    return QMetaObject.invokeMethod(
        worker, method, Qt.ConnectionType.BlockingQueuedConnection, *args
    )


class _Starter(QObject):
    """Сигнальный мост: слот (object, object) с dataclass-параметрами не
    маршаллится через Q_ARG, зато сигнал→слот доставляет PyObject как есть
    (так UI и будет вызывать start_server)."""

    startRequested = Signal(object, object)


def _start(worker: SimWorker, port: int, unit: int | None = None) -> None:
    starter = _Starter()
    starter.startRequested.connect(
        worker.start_server, Qt.ConnectionType.BlockingQueuedConnection
    )
    starter.startRequested.emit(SimTcpParams("127.0.0.1", port), unit)


def _set_values(worker: SimWorker, kind: str, address: int, values: list) -> None:
    _invoke(
        worker, "set_values",
        Q_ARG("QString", kind), Q_ARG("int", address), Q_ARG("QVariantList", values),
    )


def _get_values(worker: SimWorker, kind: str, address: int, count: int) -> list:
    return _invoke(
        worker, "get_values",
        Q_RETURN_ARG("QVariantList"),
        Q_ARG("QString", kind), Q_ARG("int", address), Q_ARG("int", count),
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


def test_start_stop_server(sim_worker: SimWorker) -> None:
    spy = QSignalSpy(sim_worker.serverChanged)
    port = _free_port()
    _start(sim_worker, port)
    # _start блокируется до конца слота, а QSignalSpy пишет синхронно на
    # эмиссии — сигнал уже записан, ждать нечего
    assert spy.count() >= 1
    ok, message = spy.at(0)
    assert ok is True
    assert f"tcp 127.0.0.1:{port}" in message

    _invoke(sim_worker, "stop_server")
    assert _collect_until(spy, lambda: spy.count() >= 2)
    ok, message = spy.at(1)
    assert ok is False
    assert message == "Stopped"


def test_stop_server_idempotent(sim_worker: SimWorker) -> None:
    spy = QSignalSpy(sim_worker.serverChanged)
    _invoke(sim_worker, "stop_server")  # сервер не запускался — безопасно
    _invoke(sim_worker, "stop_server")
    assert _collect_until(spy, lambda: spy.count() >= 2)
    assert all(args[0] is False for args in _all_args(spy))


def test_start_busy_port(sim_worker: SimWorker) -> None:
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        port = blocker.getsockname()[1]
        spy = QSignalSpy(sim_worker.serverChanged)
        _start(sim_worker, port)
        assert spy.count() >= 1
        ok, message = spy.at(0)
        assert ok is False
        assert str(port) in message


def test_master_wrote(sim_worker: SimWorker) -> None:
    port = _free_port()
    _start(sim_worker, port)
    spy = QSignalSpy(sim_worker.masterWrote)
    client = ModbusTcpClient("127.0.0.1", port=port)
    try:
        assert client.connect()
        client.write_register(20, 42, slave=1)
        client.write_registers(30, [1, 2, 3], slave=1)
        client.write_coil(4, True, slave=1)
        assert _collect_until(spy, lambda: spy.count() >= 3)
        writes = [tuple(args) for args in _all_args(spy)]
        assert ("holding_registers", 20, [42]) in writes
        assert ("holding_registers", 30, [1, 2, 3]) in writes
        assert ("coils", 4, [True]) in writes
    finally:
        client.close()


def test_set_get_values_roundtrip(sim_worker: SimWorker) -> None:
    _start(sim_worker, _free_port())
    _set_values(sim_worker, "holding_registers", 10, [100, 200])
    assert list(_get_values(sim_worker, "holding_registers", 10, 2)) == [100, 200]
    _set_values(sim_worker, "coils", 0, [True, False])
    assert list(_get_values(sim_worker, "coils", 0, 2)) == [True, False]
    # ошибки валидации гасятся в logLine, get_values возвращает []
    assert list(_get_values(sim_worker, "holding_registers", 99999, 1)) == []


def test_request_line(sim_worker: SimWorker) -> None:
    port = _free_port()
    _start(sim_worker, port)
    spy = QSignalSpy(sim_worker.requestLine)
    client = ModbusTcpClient("127.0.0.1", port=port)
    try:
        assert client.connect()
        client.read_holding_registers(10, count=4, slave=7)
        assert _collect_until(
            spy,
            lambda: any(
                "read holding_registers" in args[0] and "unit=7" in args[0]
                for args in _all_args(spy)
            ),
        )
    finally:
        client.close()


def test_client_changed(sim_worker: SimWorker) -> None:
    port = _free_port()
    _start(sim_worker, port)
    spy = QSignalSpy(sim_worker.clientChanged)
    client = ModbusTcpClient("127.0.0.1", port=port)
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


def test_tick_timer(sim_worker: SimWorker) -> None:
    _invoke(sim_worker, "set_tick_interval", Q_ARG("int", 500))
    _start(sim_worker, _free_port())
    spy = QSignalSpy(sim_worker.ticked)
    deadline = time.monotonic() + 1.6
    while time.monotonic() < deadline and spy.count() < 2:
        spy.wait(200)
    assert spy.count() >= 2  # ~3 тика за 1.6 с при интервале 500 мс
    _invoke(sim_worker, "stop_server")
    assert not spy.wait(700)  # после stop_server тиков нет
