from collections.abc import Iterator

import pytest
from conftest import UNIT_ID

from modbus_connector.backend import ModbusBackend
from modbus_connector.models import ScanProbe, TcpParams


@pytest.fixture()
def backend(modbus_server: int) -> Iterator[ModbusBackend]:
    b = ModbusBackend()
    b.connect(TcpParams(host="127.0.0.1", port=modbus_server))
    yield b
    b.disconnect()


class TestConnect:
    def test_connected_property(self, modbus_server: int) -> None:
        b = ModbusBackend()
        assert not b.connected
        b.connect(TcpParams(host="127.0.0.1", port=modbus_server))
        assert b.connected
        b.disconnect()
        assert not b.connected

    def test_connect_refused(self) -> None:
        b = ModbusBackend()
        with pytest.raises((ConnectionError, OSError)):
            b.connect(TcpParams(host="127.0.0.1", port=1, timeout=0.5))


class TestRead:
    def test_holding_registers(self, backend: ModbusBackend) -> None:
        assert backend.read(UNIT_ID, "holding_registers", 0, 10) == [100 + i for i in range(10)]

    def test_input_registers(self, backend: ModbusBackend) -> None:
        assert backend.read(UNIT_ID, "input_registers", 0, 5) == [7 + i for i in range(5)]

    def test_coils(self, backend: ModbusBackend) -> None:
        assert backend.read(UNIT_ID, "coils", 0, 8) == [i % 2 == 0 for i in range(8)]

    def test_discrete_inputs(self, backend: ModbusBackend) -> None:
        assert backend.read(UNIT_ID, "discrete_inputs", 0, 8) == [i % 2 == 1 for i in range(8)]

    def test_read_partial(self, backend: ModbusBackend) -> None:
        assert backend.read(UNIT_ID, "holding_registers", 3, 2) == [103, 104]


class TestWrite:
    def test_write_single_register(self, backend: ModbusBackend) -> None:
        backend.write(UNIT_ID, "holding_registers", 5, [555])
        assert backend.read(UNIT_ID, "holding_registers", 5, 1) == [555]

    def test_write_multiple_registers(self, backend: ModbusBackend) -> None:
        backend.write(UNIT_ID, "holding_registers", 0, [1, 2, 3])
        assert backend.read(UNIT_ID, "holding_registers", 0, 4) == [1, 2, 3, 103]

    def test_write_single_coil(self, backend: ModbusBackend) -> None:
        backend.write(UNIT_ID, "coils", 2, [False])
        assert backend.read(UNIT_ID, "coils", 0, 4) == [True, False, False, False]

    def test_write_multiple_coils(self, backend: ModbusBackend) -> None:
        backend.write(UNIT_ID, "coils", 4, [True, True])
        assert backend.read(UNIT_ID, "coils", 4, 4) == [True, True, True, False]

    def test_write_input_registers_raises(self, backend: ModbusBackend) -> None:
        with pytest.raises(ValueError):
            backend.write(UNIT_ID, "input_registers", 0, [1])

    def test_write_discrete_inputs_raises(self, backend: ModbusBackend) -> None:
        with pytest.raises(ValueError):
            backend.write(UNIT_ID, "discrete_inputs", 0, [True])


class TestNotConnected:
    def test_read_without_connect(self) -> None:
        with pytest.raises(ConnectionError):
            ModbusBackend().read(UNIT_ID, "holding_registers", 0, 1)

    def test_write_without_connect(self) -> None:
        with pytest.raises(ConnectionError):
            ModbusBackend().write(UNIT_ID, "holding_registers", 0, [1])

    def test_scan_without_connect(self) -> None:
        probes = [ScanProbe(kind="holding_registers", address=0, count=1)]
        with pytest.raises(ConnectionError):
            list(ModbusBackend().scan(probes, 1, 3, lambda: False))

    def test_disconnect_without_connect(self) -> None:
        ModbusBackend().disconnect()


class TestScan:
    def test_finds_unit(self, backend: ModbusBackend) -> None:
        probes = [ScanProbe(kind="holding_registers", address=0, count=1)]
        expected = [(unit, [0] if unit == UNIT_ID else []) for unit in range(1, 11)]
        assert list(backend.scan(probes, 1, 10, lambda: False)) == expected

    def test_reports_every_unit(self, backend: ModbusBackend) -> None:
        probes = [ScanProbe(kind="holding_registers", address=0, count=1)]
        results = list(backend.scan(probes, 1, 3, lambda: False))
        assert results == [(1, [0]), (2, []), (3, [])]

    def test_empty_range(self, backend: ModbusBackend) -> None:
        probes = [ScanProbe(kind="holding_registers", address=0, count=1)]
        assert list(backend.scan(probes, 2, 5, lambda: False)) == [(u, []) for u in range(2, 6)]

    def test_should_stop(self, backend: ModbusBackend) -> None:
        probes = [ScanProbe(kind="holding_registers", address=0, count=1)]
        hits: list[tuple[int, list[int]]] = []
        for hit in backend.scan(probes, 1, 10, lambda: bool(hits)):
            hits.append(hit)
        assert hits == [(1, [0])]

    def test_should_stop_between_probes(self, backend: ModbusBackend) -> None:
        probes = [
            ScanProbe(kind="holding_registers", address=0, count=1),
            ScanProbe(kind="input_registers", address=0, count=1),
        ]
        calls = 0

        def stop() -> bool:
            nonlocal calls
            calls += 1
            return calls > 2

        assert list(backend.scan(probes, 1, 1, stop)) == []

    def test_aborts_on_connection_loss(self, backend: ModbusBackend) -> None:
        probes = [ScanProbe(kind="holding_registers", address=0, count=1)]
        calls = 0

        def stop() -> bool:
            nonlocal calls
            calls += 1
            if calls == 3:
                backend.disconnect()
            return False

        with pytest.raises(ConnectionError):
            list(backend.scan(probes, 1, 10, stop))
