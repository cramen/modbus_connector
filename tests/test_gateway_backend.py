import socket
from collections.abc import Iterator

import pytest
from pymodbus.client import ModbusTcpClient

from modbus_connector.gateway_backend import (
    GatewayBackend,
    GatewayTcpListenParams,
)
from modbus_connector.models import RtuOverTcpParams, TcpParams


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def gateway(modbus_server: int) -> Iterator[tuple[GatewayBackend, int, list[str]]]:
    """Шлюз TCP -> TCP на тестовый сервер: (backend, listen_port, запросы)."""
    backend = GatewayBackend()
    requests: list[str] = []
    backend.on_request = requests.append
    listen_port = _free_port()
    backend.start(
        GatewayTcpListenParams("127.0.0.1", listen_port),
        TcpParams("127.0.0.1", modbus_server, timeout=1.0),
    )
    yield backend, listen_port, requests
    backend.stop()


@pytest.fixture()
def master(gateway: tuple[GatewayBackend, int, list[str]]) -> Iterator[ModbusTcpClient]:
    client = ModbusTcpClient("127.0.0.1", port=gateway[1], timeout=1.0)
    assert client.connect()
    yield client
    client.close()


def test_read_through(
    gateway: tuple[GatewayBackend, int, list[str]], master: ModbusTcpClient
) -> None:
    rr = master.read_holding_registers(0, count=10, slave=1)
    assert not rr.isError()
    assert rr.registers == [100 + i for i in range(10)]
    rr = master.read_input_registers(0, count=5, slave=1)
    assert rr.registers == [7 + i for i in range(5)]
    rr = master.read_coils(0, count=8, slave=1)
    assert rr.bits == [i % 2 == 0 for i in range(8)]
    rr = master.read_discrete_inputs(0, count=8, slave=1)
    assert rr.bits == [i % 2 == 1 for i in range(8)]
    assert "-> unit 1 read holding_registers@0 count 10" in gateway[2]
    assert any(line.startswith("<- ok (") for line in gateway[2])


def test_write_through(master: ModbusTcpClient) -> None:
    rr = master.write_coil(0, False, slave=1)
    assert not rr.isError()
    # pymodbus 3.6.9 не обрезает bits до count (возвращает целый байт)
    assert master.read_coils(0, count=1, slave=1).bits[:1] == [False]
    rr = master.write_register(1, 555, slave=1)
    assert not rr.isError()
    assert master.read_holding_registers(1, count=1, slave=1).registers == [555]
    rr = master.write_registers(2, [1, 2, 3], slave=1)
    assert not rr.isError()
    assert master.read_holding_registers(2, count=3, slave=1).registers == [1, 2, 3]


def test_rtu_over_tcp_target(modbus_rtu_server: int) -> None:
    backend = GatewayBackend()
    listen_port = _free_port()
    backend.start(
        GatewayTcpListenParams("127.0.0.1", listen_port),
        RtuOverTcpParams("127.0.0.1", modbus_rtu_server, timeout=1.0),
    )
    try:
        with ModbusTcpClient("127.0.0.1", port=listen_port, timeout=1.0) as client:
            assert client.connect()
            rr = client.read_holding_registers(0, count=10, slave=1)
            assert rr.registers == [100 + i for i in range(10)]
    finally:
        backend.stop()


def test_units_filter(modbus_server: int) -> None:
    backend = GatewayBackend()
    listen_port = _free_port()
    backend.start(
        GatewayTcpListenParams("127.0.0.1", listen_port),
        TcpParams("127.0.0.1", modbus_server, timeout=1.0),
        units={1},
    )
    try:
        with ModbusTcpClient("127.0.0.1", port=listen_port, timeout=1.0) as client:
            assert client.connect()
            rr = client.read_holding_registers(0, count=2, slave=1)
            assert not rr.isError()
            assert rr.registers == [100, 101]
            # неизвестный unit: socket-framer pymodbus молча отбрасывает кадр
            # (до GatewayNoResponse дело не доходит) — мастер получает таймаут
            rr = client.read_holding_registers(0, count=2, slave=2)
            assert rr.isError()
            assert "No response received" in str(rr)
    finally:
        backend.stop()


def test_target_error(
    gateway: tuple[GatewayBackend, int, list[str]], master: ModbusTcpClient
) -> None:
    # чтение вне карты target: backend получает Illegal Address, пробрасывает,
    # сервер отвечает exception 0x04 Slave Failure
    rr = master.read_holding_registers(9000, count=1, slave=1)
    assert rr.isError()
    assert rr.exception_code == 0x04
    assert any(
        line.startswith("<- error:") and "holding_registers@9000" in line
        for line in gateway[2]
    )


def test_stop(gateway: tuple[GatewayBackend, int, list[str]], master: ModbusTcpClient) -> None:
    backend, listen_port, _ = gateway
    assert backend.running
    master.close()
    backend.stop()
    assert not backend.running
    with pytest.raises(OSError):
        with socket.create_connection(("127.0.0.1", listen_port), timeout=1.0):
            pass


def test_listen_port_busy(modbus_server: int) -> None:
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        backend = GatewayBackend()
        with pytest.raises(ConnectionError, match="Не удалось запустить шлюз"):
            backend.start(
                GatewayTcpListenParams("127.0.0.1", port),
                TcpParams("127.0.0.1", modbus_server, timeout=1.0),
            )
        assert not backend.running


def test_target_unreachable() -> None:
    backend = GatewayBackend()
    with pytest.raises(ConnectionError, match="Не удалось подключиться"):
        backend.start(
            GatewayTcpListenParams("127.0.0.1", _free_port()),
            TcpParams("127.0.0.1", _free_port(), timeout=0.5),
        )
    assert not backend.running
