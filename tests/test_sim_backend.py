import fcntl
import os
import socket
import time
from collections.abc import Callable, Iterator

import pytest
from pymodbus.exceptions import ModbusIOException

from modbus_connector.backend import ModbusBackend
from modbus_connector.models import RtuParams, TcpParams
from modbus_connector.sim_backend import BLOCK_SIZE, SimBackend, SimTcpParams


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for(predicate: Callable[[], bool], timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@pytest.fixture()
def sim() -> Iterator[SimBackend]:
    backend = SimBackend()
    yield backend
    backend.stop()


@pytest.fixture()
def client() -> Iterator[ModbusBackend]:
    backend = ModbusBackend()
    yield backend
    backend.disconnect()


def test_tcp_read_write_all_areas(sim: SimBackend, client: ModbusBackend) -> None:
    port = _free_port()
    sim.start(SimTcpParams("127.0.0.1", port))
    assert sim.running
    client.connect(TcpParams("127.0.0.1", port))

    # начальные значения — нули
    assert client.read(1, "holding_registers", 0, 4) == [0, 0, 0, 0]
    assert client.read(1, "coils", 0, 3) == [False, False, False]

    # set_values видны клиенту во всех 4 областях
    sim.set_values("holding_registers", 10, [100, 200])
    assert client.read(1, "holding_registers", 10, 2) == [100, 200]
    sim.set_values("input_registers", 3, [7, 8, 9])
    assert client.read(1, "input_registers", 3, 3) == [7, 8, 9]
    sim.set_values("coils", 0, [True, False, True])
    assert client.read(1, "coils", 0, 3) == [True, False, True]
    sim.set_values("discrete_inputs", 5, [True, True])
    assert client.read(1, "discrete_inputs", 5, 2) == [True, True]

    # запись клиентом видна через get_values
    client.write(1, "holding_registers", 20, [42])
    assert sim.get_values("holding_registers", 20, 1) == [42]
    client.write(1, "coils", 4, [True])
    assert sim.get_values("coils", 4, 1) == [True]

    # границы карты
    sim.set_values("holding_registers", BLOCK_SIZE - 1, [1])
    assert client.read(1, "holding_registers", BLOCK_SIZE - 1, 1) == [1]
    with pytest.raises(ModbusIOException):  # за пределами карты — ошибка устройства
        client.read(1, "holding_registers", BLOCK_SIZE, 1)


def test_tcp_master_write_hook(sim: SimBackend, client: ModbusBackend) -> None:
    port = _free_port()
    writes: list[tuple[str, int, list]] = []
    sim.on_master_write = lambda kind, address, values: writes.append(
        (kind, address, values)
    )
    sim.start(SimTcpParams("127.0.0.1", port))
    client.connect(TcpParams("127.0.0.1", port))

    client.write(1, "holding_registers", 20, [42])
    client.write(1, "holding_registers", 30, [1, 2, 3])
    client.write(1, "coils", 4, [True])
    assert ("holding_registers", 20, [42]) in writes
    assert ("holding_registers", 30, [1, 2, 3]) in writes
    assert ("coils", 4, [True]) in writes

    # set_values самого симулятора хук не вызывает
    writes.clear()
    sim.set_values("holding_registers", 50, [9])
    assert writes == []


def test_tcp_request_log(sim: SimBackend, client: ModbusBackend) -> None:
    port = _free_port()
    lines: list[str] = []
    sim.on_request = lines.append
    sim.start(SimTcpParams("127.0.0.1", port))
    client.connect(TcpParams("127.0.0.1", port))

    client.read(7, "holding_registers", 10, 4)
    client.write(7, "coils", 2, [True])
    assert _wait_for(lambda: any("read holding_registers" in line for line in lines))
    assert any("unit=7" in line and "@10" in line for line in lines)
    assert any("write coil" in line for line in lines)


def test_tcp_client_connect_hook(sim: SimBackend, client: ModbusBackend) -> None:
    port = _free_port()
    events: list[tuple[bool, str]] = []
    sim.on_client = lambda *args: events.append(args)
    sim.start(SimTcpParams("127.0.0.1", port))
    client.connect(TcpParams("127.0.0.1", port))
    assert _wait_for(lambda: [ok for ok, _addr in events] == [True])
    assert events[0][1].startswith("127.0.0.1:")  # адрес клиента передаётся
    client.disconnect()
    assert _wait_for(lambda: [ok for ok, _addr in events] == [True, False])


def test_tcp_identity(sim: SimBackend, client: ModbusBackend) -> None:
    port = _free_port()
    sim.start(SimTcpParams("127.0.0.1", port))
    client.connect(TcpParams("127.0.0.1", port))
    info = client.read_device_identification(1)  # basic (read_code=1): 0x00-0x02
    assert info[0x00] == "ModbusConnector"
    assert info[0x01] == "SIM"

    # ModelName (0x05) — regular-стрим, читаем read_code=2 напрямую
    from pymodbus.client import ModbusTcpClient

    raw = ModbusTcpClient("127.0.0.1", port=port)
    try:
        assert raw.connect()
        result = raw.read_device_information(read_code=2, object_id=0, slave=1)
        regular = {
            int(k): v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
            for k, v in result.information.items()
        }
        assert regular[0x05] == "ModbusConnector simulator"
    finally:
        raw.close()


def test_tcp_single_unit(sim: SimBackend, client: ModbusBackend) -> None:
    port = _free_port()
    sim.start(SimTcpParams("127.0.0.1", port), unit=7)
    client.connect(TcpParams("127.0.0.1", port))
    assert client.read(7, "holding_registers", 0, 1) == [0]
    with pytest.raises(ModbusIOException):  # чужой unit — ошибка чтения
        client.read(1, "holding_registers", 0, 1)


def test_tcp_any_unit(sim: SimBackend, client: ModbusBackend) -> None:
    port = _free_port()
    sim.start(SimTcpParams("127.0.0.1", port))  # unit=None — любой unit id
    client.connect(TcpParams("127.0.0.1", port))
    for unit in (1, 7, 247):
        assert client.read(unit, "holding_registers", 0, 1) == [0]


def test_double_start_raises(sim: SimBackend) -> None:
    sim.start(SimTcpParams("127.0.0.1", _free_port()))
    with pytest.raises(RuntimeError, match="stop"):
        sim.start(SimTcpParams("127.0.0.1", _free_port()))


def test_tcp_port_busy(sim: SimBackend) -> None:
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        port = blocker.getsockname()[1]
        with pytest.raises(ConnectionError, match=str(port)):
            sim.start(SimTcpParams("127.0.0.1", port))
    assert not sim.running


def test_stop_and_restart_same_port(sim: SimBackend, client: ModbusBackend) -> None:
    port = _free_port()
    sim.start(SimTcpParams("127.0.0.1", port))
    assert sim.running
    sim.stop()
    assert not sim.running
    # повторная остановка безопасна
    sim.stop()

    sim.start(SimTcpParams("127.0.0.1", port))
    assert sim.running
    sim.set_values("holding_registers", 0, [55])
    client.connect(TcpParams("127.0.0.1", port))
    assert client.read(1, "holding_registers", 0, 1) == [55]


def test_set_get_values_validation(sim: SimBackend) -> None:
    with pytest.raises(ValueError, match="Неизвестная область"):
        sim.set_values("bogus", 0, [1])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="выходит за"):
        sim.set_values("holding_registers", BLOCK_SIZE, [1])
    with pytest.raises(ValueError, match="выходит за"):
        sim.get_values("coils", BLOCK_SIZE - 1, 2)
    with pytest.raises(ValueError, match="unit"):
        sim.start(SimTcpParams("127.0.0.1", _free_port()), unit=0)


class _FdSerial:
    """pyserial Serial поверх уже открытого fd (pty-master на macOS имени не имеет).

    Повторяет serialposix.Serial.open(), только без os.open по имени: fd уже
    дан, остальное (termios, abort-pipes) настраивается как обычно.
    """

    def __new__(cls, fd: int, **kwargs: object) -> "serial.Serial":  # noqa: F821
        import serial

        class FdSerial(serial.Serial):
            def open(self) -> None:
                if self.is_open:
                    raise serial.SerialException("Port is already open.")
                self.fd = fd
                self.pipe_abort_read_r, self.pipe_abort_read_w = os.pipe()
                self.pipe_abort_write_r, self.pipe_abort_write_w = os.pipe()
                fcntl.fcntl(self.pipe_abort_read_r, fcntl.F_SETFL, os.O_NONBLOCK)
                fcntl.fcntl(self.pipe_abort_write_w, fcntl.F_SETFL, os.O_NONBLOCK)
                self._reconfigure_port(force_update=True)
                self._reset_input_buffer()
                self.is_open = True

        instance = FdSerial(**kwargs)
        instance.open()
        return instance


@pytest.mark.skipif(os.name == "nt", reason="pty недоступен на Windows")
def test_rtu_pty(sim: SimBackend) -> None:
    import pty

    from pymodbus.client import ModbusSerialClient

    master_fd, slave_fd = pty.openpty()
    slave_name = os.ttyname(slave_fd)
    client = ModbusSerialClient("unused", baudrate=9600, timeout=1.0)
    try:
        sim.start(RtuParams(port=slave_name, baudrate=9600, timeout=1.0))
        assert sim.running
        sim.set_values("holding_registers", 0, [11, 22, 33])

        # master-сторону pty нельзя переоткрыть по имени — отдаём fd pyserial'ю
        client.socket = _FdSerial(os.dup(master_fd), baudrate=9600, timeout=1.0)
        assert client.connect()
        assert client.read_holding_registers(0, count=3, slave=1).registers == [11, 22, 33]

        client.write_register(5, 77, slave=1)
        assert sim.get_values("holding_registers", 5, 1) == [77]
    finally:
        client.close()
        os.close(master_fd)
        os.close(slave_fd)
