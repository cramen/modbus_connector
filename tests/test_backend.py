from collections.abc import Iterator

import pytest
from conftest import UNIT_ID
from pymodbus.client import ModbusTcpClient

from modbus_connector.backend import ModbusBackend, ModbusExceptionError
from modbus_connector.models import ScanProbe, TcpParams


@pytest.fixture()
def backend(modbus_server: int) -> Iterator[ModbusBackend]:
    b = ModbusBackend()
    b.connect(TcpParams(host="127.0.0.1", port=modbus_server))
    yield b
    b.disconnect()


def _spy_close(monkeypatch: pytest.MonkeyPatch) -> list[ModbusTcpClient]:
    closed: list[ModbusTcpClient] = []
    original_close = ModbusTcpClient.close

    def spy_close(client: ModbusTcpClient) -> None:
        closed.append(client)
        original_close(client)

    monkeypatch.setattr(ModbusTcpClient, "close", spy_close)
    return closed


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

    def test_failed_connect_closes_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        closed = _spy_close(monkeypatch)
        monkeypatch.setattr(ModbusTcpClient, "connect", lambda client: False)
        b = ModbusBackend()
        with pytest.raises(ConnectionError):
            b.connect(TcpParams(host="127.0.0.1", port=1, timeout=0.5))
        assert b._client is None
        assert len(closed) == 1
        b.disconnect()

    def test_failed_connect_closes_client_on_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        closed = _spy_close(monkeypatch)

        def raising_connect(client: ModbusTcpClient) -> bool:
            raise OSError("boom")

        monkeypatch.setattr(ModbusTcpClient, "connect", raising_connect)
        b = ModbusBackend()
        with pytest.raises(ConnectionError, match="boom"):
            b.connect(TcpParams(host="127.0.0.1", port=1, timeout=0.5))
        assert b._client is None
        assert len(closed) == 1
        b.disconnect()


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


class TestReadErrors:
    def test_out_of_range_address_is_human_readable(self, backend: ModbusBackend) -> None:
        with pytest.raises(ModbusExceptionError, match="Illegal Data Address") as exc_info:
            backend.read(UNIT_ID, "holding_registers", 10, 1)
        assert exc_info.value.exception_code == 0x02

    def test_out_of_range_write_is_human_readable(self, backend: ModbusBackend) -> None:
        with pytest.raises(ModbusExceptionError, match=r"Illegal Data Address \(0x02\)"):
            backend.write(UNIT_ID, "holding_registers", 10, [1])


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


class TestTrafficHook:
    def test_captures_tx_and_rx_frames(self, backend: ModbusBackend) -> None:
        frames: list[tuple[str, bytes]] = []
        backend.traffic_hook = lambda direction, data: frames.append((direction, data))
        backend.read(UNIT_ID, "holding_registers", 0, 2)
        tx = [data for direction, data in frames if direction == "tx"]
        rx = [data for direction, data in frames if direction == "rx"]
        assert tx and rx
        frame = tx[0]
        # Modbus TCP MBAP: tid(2) pid(2)=0 length(2) unit(1) fcode(1)
        assert frame[2:4] == b"\x00\x00"
        assert frame[6] == UNIT_ID
        assert frame[7] == 3  # read holding registers

    def test_hook_failure_does_not_break_read(self, backend: ModbusBackend) -> None:
        def failing_hook(direction: str, data: bytes) -> None:
            raise RuntimeError("boom")

        backend.traffic_hook = failing_hook
        assert backend.read(UNIT_ID, "holding_registers", 0, 2) == [100, 101]

    def test_disconnect_restores_client_io(self, modbus_server: int) -> None:
        b = ModbusBackend()
        b.connect(TcpParams(host="127.0.0.1", port=modbus_server))
        client = b._client
        assert client is not None
        b.disconnect()
        assert "send" not in client.__dict__  # instance wrapper restored
        assert "recv" not in client.__dict__


class TestScanAddresses:
    def test_holding_registers(self, backend: ModbusBackend) -> None:
        found = backend.scan_addresses(UNIT_ID, "holding_registers", 0, 14, lambda: False)
        assert list(found) == list(range(10))

    def test_input_registers(self, backend: ModbusBackend) -> None:
        found = backend.scan_addresses(UNIT_ID, "input_registers", 0, 9, lambda: False)
        assert list(found) == list(range(5))

    def test_should_stop(self, backend: ModbusBackend) -> None:
        found = []
        for address in backend.scan_addresses(
            UNIT_ID, "holding_registers", 0, 9, lambda: bool(found)
        ):
            found.append(address)
        assert found == [0]

    def test_dead_unit_yields_nothing(self, modbus_server: int) -> None:
        b = ModbusBackend()
        b.connect(TcpParams(host="127.0.0.1", port=modbus_server, timeout=0.2))
        try:
            found = b.scan_addresses(2, "holding_registers", 0, 2, lambda: False)
            assert list(found) == []
        finally:
            b.disconnect()

    def test_without_connect(self) -> None:
        with pytest.raises(ConnectionError):
            list(ModbusBackend().scan_addresses(UNIT_ID, "holding_registers", 0, 1, lambda: False))


class TestReadWriteRegisters:
    def test_readwrite(self, backend: ModbusBackend) -> None:
        result = backend.readwrite_registers(UNIT_ID, 0, 3, 5, [11, 22, 33])
        assert result == [100, 101, 102]
        assert backend.read(UNIT_ID, "holding_registers", 5, 3) == [11, 22, 33]

    def test_validation(self, backend: ModbusBackend) -> None:
        with pytest.raises(ValueError):  # empty values
            backend.readwrite_registers(UNIT_ID, 0, 1, 5, [])
        with pytest.raises(ValueError):  # value out of range
            backend.readwrite_registers(UNIT_ID, 0, 1, 5, [0x10000])
        with pytest.raises(ValueError):  # bad read count
            backend.readwrite_registers(UNIT_ID, 0, 0, 5, [1])

    def test_out_of_range_write_address(self, backend: ModbusBackend) -> None:
        with pytest.raises(ModbusExceptionError, match="Illegal Data Address"):
            backend.readwrite_registers(UNIT_ID, 0, 1, 10, [1])


class TestMaskWriteRegister:
    def test_mask_write_applies_spec_formula(self, backend: ModbusBackend) -> None:
        backend.write(UNIT_ID, "holding_registers", 5, [0xFFFF])
        backend.mask_write_register(UNIT_ID, 5, 0xFF0F, 0x00A0)
        # Modbus 0x16: (value AND and_mask) OR (or_mask AND NOT and_mask)
        expected = (0xFFFF & 0xFF0F) | (0x00A0 & (0xFFFF ^ 0xFF0F))
        assert expected == 0xFFAF
        assert backend.read(UNIT_ID, "holding_registers", 5, 1) == [expected]

    def test_mask_out_of_range_raises(self, backend: ModbusBackend) -> None:
        with pytest.raises(ValueError):
            backend.mask_write_register(UNIT_ID, 5, 0x10000, 0)
        with pytest.raises(ValueError):
            backend.mask_write_register(UNIT_ID, 5, 0, -1)

    def test_mask_write_error_is_human_readable(self, backend: ModbusBackend) -> None:
        with pytest.raises(ModbusExceptionError, match="Illegal Data Address"):
            backend.mask_write_register(UNIT_ID, 10, 0xFFFF, 0)


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
