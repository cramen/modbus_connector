import os
import time
from collections.abc import Iterator
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Qt needs system libraries (libEGL etc.) that headless Linux CI runners lack;
# skip the whole module there — it still runs on macOS/Windows CI and dev machines.
try:
    from PySide6.QtCore import QMetaObject, QObject, Qt, QThread, Signal
    from PySide6.QtTest import QSignalSpy
    from PySide6.QtWidgets import QApplication
except ImportError as exc:
    pytest.skip(f"Qt system libraries not available: {exc}", allow_module_level=True)

from modbus_connector.models import RtuParams  # noqa: E402
from modbus_connector.sniffer_backend import crc16  # noqa: E402
from modbus_connector.sniffer_worker import SnifferWorker  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def sniffer_worker(qapp: QApplication) -> Iterator[SnifferWorker]:
    thread = QThread()
    worker = SnifferWorker()
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


class _Starter(QObject):
    """Сигнальный мост: слот (object) с dataclass-параметрами не маршаллится
    через Q_ARG, зато сигнал→слот доставляет PyObject как есть (так UI и
    вызывает start_sniffing)."""

    startRequested = Signal(object)


def _start(worker: SnifferWorker, params: RtuParams) -> None:
    starter = _Starter()
    starter.startRequested.connect(
        worker.start_sniffing, Qt.ConnectionType.BlockingQueuedConnection
    )
    starter.startRequested.emit(params)


def _collect_until(spy: QSignalSpy, predicate: Any, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        spy.wait(50)
    return predicate()


def _frame(body: bytes) -> bytes:
    crc = crc16(body)
    return body + bytes([crc & 0xFF, crc >> 8])


def test_start_invalid_port(sniffer_worker: SnifferWorker) -> None:
    spy = QSignalSpy(sniffer_worker.sniffingChanged)
    log_spy = QSignalSpy(sniffer_worker.logLine)
    _start(sniffer_worker, RtuParams(port="/dev/nonexistent-modbus-port"))
    # _start блокируется до конца слота, QSignalSpy пишет синхронно на эмиссии
    assert spy.count() >= 1
    ok, message = spy.at(0)
    assert ok is False
    assert "nonexistent" in message
    assert any("✗" in args[0] for args in (list(log_spy.at(i)) for i in range(log_spy.count())))


def test_stop_idempotent(sniffer_worker: SnifferWorker) -> None:
    spy = QSignalSpy(sniffer_worker.sniffingChanged)
    QMetaObject.invokeMethod(
        sniffer_worker, "stop_sniffing", Qt.ConnectionType.BlockingQueuedConnection
    )
    QMetaObject.invokeMethod(
        sniffer_worker, "stop_sniffing", Qt.ConnectionType.BlockingQueuedConnection
    )
    assert _collect_until(spy, lambda: spy.count() >= 2)
    assert all(spy.at(i)[0] is False for i in range(spy.count()))
    assert spy.at(0)[1] == "Stopped"


def test_frame_signals(sniffer_worker: SnifferWorker) -> None:
    """Байты кадров → valuesChanged/frameLine/frameForUnit (через backend).

    Поток чтения порта здесь не поднят, поэтому _handle_bytes зовём напрямую —
    парсер/модель не делятся с читателем, гонок нет; эмиссия из GUI-потока
    доставляется в спай синхронно.
    """
    values_spy = QSignalSpy(sniffer_worker.valuesChanged)
    line_spy = QSignalSpy(sniffer_worker.frameLine)
    unit_spy = QSignalSpy(sniffer_worker.frameForUnit)
    request = _frame(bytes([7, 3, 0, 10, 0, 2]))  # read holding @10 x2, unit 7
    response = _frame(bytes([7, 3, 4, 0, 100, 0, 200]))  # ← 100, 200
    sniffer_worker._backend._handle_bytes(request + response)
    assert values_spy.count() == 1
    unit, kind, address, values = values_spy.at(0)
    assert (unit, kind, address) == (7, "holding_registers", 10)
    assert list(values) == [100, 200]
    lines = [line_spy.at(i)[0] for i in range(line_spy.count())]
    assert any("read holding_registers" in line and "unit=7" in line for line in lines)
    unit_lines = [tuple(unit_spy.at(i)) for i in range(unit_spy.count())]
    assert unit_lines and all(u == 7 for u, _line in unit_lines)
    assert any("100" in line and "200" in line for _u, line in unit_lines)
