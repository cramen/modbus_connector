import os
import time
from collections.abc import Callable

import pytest

from modbus_connector.models import RtuParams
from modbus_connector.sniffer_backend import (
    BusModel,
    RtuFrameParser,
    SerialSniffer,
    SniffedFrame,
    SnifferBackend,
    crc16,
    format_frame,
)


def _crc(data: bytes) -> bytes:
    return data + crc16(data).to_bytes(2, "little")


def _read_request(unit: int, fc: int, address: int, count: int) -> bytes:
    return _crc(bytes([unit, fc]) + address.to_bytes(2, "big") + count.to_bytes(2, "big"))


def _read_response_regs(unit: int, fc: int, values: list[int]) -> bytes:
    data = b"".join(v.to_bytes(2, "big") for v in values)
    return _crc(bytes([unit, fc, len(data)]) + data)


def _pack_bits(bits: list[bool]) -> bytes:
    data = bytearray((len(bits) + 7) // 8)
    for i, bit in enumerate(bits):
        if bit:
            data[i // 8] |= 1 << (i % 8)
    return bytes(data)


def _read_response_bits(unit: int, fc: int, bits: list[bool]) -> bytes:
    data = _pack_bits(bits)
    return _crc(bytes([unit, fc, len(data)]) + data)


def _write_single(unit: int, fc: int, address: int, word: int) -> bytes:
    return _crc(bytes([unit, fc]) + address.to_bytes(2, "big") + word.to_bytes(2, "big"))


def _write_multi_request(unit: int, fc: int, address: int, data: bytes, count: int) -> bytes:
    body = bytes([unit, fc]) + address.to_bytes(2, "big") + count.to_bytes(2, "big")
    return _crc(body + bytes([len(data)]) + data)


def _exception(unit: int, fc: int, code: int) -> bytes:
    return _crc(bytes([unit, fc | 0x80, code]))


def _frame(
    direction: str,
    unit: int,
    fc: int,
    address: int | None = None,
    count: int | None = None,
    values: list | None = None,
    exception: int | None = None,
) -> SniffedFrame:
    return SniffedFrame(direction, unit, fc, address, count, values, exception)  # type: ignore[arg-type]


def _wait_for(predicate: Callable[[], bool], timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_crc16_known_vectors() -> None:
    assert crc16(b"") == 0xFFFF
    assert crc16(b"123456789") == 0x4B37  # check-вектор CRC-16/MODBUS
    data = bytes([1, 3, 0, 0, 0, 6])
    # CRC, дописанный к сообщению (low byte first), даёт нулевой остаток
    assert crc16(data + crc16(data).to_bytes(2, "little")) == 0


def test_read_request_fc3() -> None:
    frames = RtuFrameParser().feed(_read_request(1, 3, 200, 6))
    assert frames == [SniffedFrame("tx", 1, 3, 200, 6, None, None)]


def test_read_response_matched_to_pending() -> None:
    parser = RtuFrameParser()
    parser.feed(_read_request(1, 3, 200, 6))
    frames = parser.feed(_read_response_regs(1, 3, [100, 101, 102, 103, 104, 105]))
    assert frames == [SniffedFrame("rx", 1, 3, 200, 6, [100, 101, 102, 103, 104, 105], None)]


def test_read_response_without_pending() -> None:
    (frame,) = RtuFrameParser().feed(_read_response_regs(1, 3, [7, 8]))
    assert frame.direction == "rx"
    assert frame.address is None and frame.count is None
    assert frame.values == [7, 8]


def test_fc6_write_and_echo() -> None:
    parser = RtuFrameParser()
    wire = _write_single(1, 6, 5, 77)
    (tx,) = parser.feed(wire)
    (rx,) = parser.feed(wire)
    assert tx.direction == "tx" and tx.address == 5 and tx.values == [77]
    assert rx.direction == "rx" and rx.address == 5 and rx.values == [77]


def test_fc6_echo_ambiguity_new_write_is_tx() -> None:
    # второй 8-байтовый кадр fc6 с ДРУГИМ адресом/значением — новая запись, не эхо
    parser = RtuFrameParser()
    (tx1,) = parser.feed(_write_single(1, 6, 5, 77))
    (tx2,) = parser.feed(_write_single(1, 6, 6, 78))
    assert tx1.direction == "tx" and tx2.direction == "tx"
    assert tx2.address == 6 and tx2.values == [78]


def test_fc16_write_and_response() -> None:
    parser = RtuFrameParser()
    data = b"".join(v.to_bytes(2, "big") for v in [11, 22, 33])
    (tx,) = parser.feed(_write_multi_request(1, 16, 0, data, 3))
    assert tx.direction == "tx" and tx.address == 0 and tx.count == 3
    assert tx.values == [11, 22, 33]
    # ответ fc16 — 8-байтовая структура (unit,fc,addr2,count2,crc)
    (rx,) = parser.feed(_read_request(1, 16, 0, 3))
    assert rx.direction == "rx" and rx.address == 0 and rx.count == 3
    assert rx.values is None


def test_fc15_write_coils() -> None:
    parser = RtuFrameParser()
    bits = [True, False, True, True, False]
    (tx,) = parser.feed(_write_multi_request(2, 15, 4, _pack_bits(bits), len(bits)))
    assert tx.direction == "tx" and tx.address == 4 and tx.count == 5
    assert tx.values == bits


def test_fc1_bits_trimmed_to_pending_count() -> None:
    parser = RtuFrameParser()
    bits = [True, False, True, True, False, False, True, False, True, True]
    parser.feed(_read_request(2, 1, 3, len(bits)))
    (rx,) = parser.feed(_read_response_bits(2, 1, bits))
    assert rx.direction == "rx" and rx.address == 3 and rx.count == len(bits)
    assert rx.values == bits  # padding-биты последнего байта обрезаны по count


def test_exception_checked_and_clears_pending() -> None:
    parser = RtuFrameParser()
    parser.feed(_read_request(1, 3, 0, 2))
    (exc,) = parser.feed(_exception(1, 3, 0x02))
    assert exc.direction == "rx" and exc.function_code == 3 and exc.exception_code == 0x02
    # pending снят: поздний ответ без pending — адрес не восстановить
    (rx,) = parser.feed(_read_response_regs(1, 3, [1, 2]))
    assert rx.address is None


def test_garbage_resync() -> None:
    parser = RtuFrameParser()
    frames = parser.feed(b"\xde\xad\xbe\xef" + _read_request(1, 3, 0, 4))
    assert frames == [SniffedFrame("tx", 1, 3, 0, 4, None, None)]


def test_truncated_frame_waits_for_rest() -> None:
    parser = RtuFrameParser()
    wire = _read_request(1, 3, 0, 4)
    assert parser.feed(wire[:5]) == []
    assert parser.feed(wire[5:]) == [SniffedFrame("tx", 1, 3, 0, 4, None, None)]


def test_concatenated_frames_and_byte_by_byte_feed() -> None:
    wire = _read_request(1, 3, 10, 2) + _read_response_regs(1, 3, [5, 6])
    whole = RtuFrameParser().feed(wire)
    assert [frame.direction for frame in whole] == ["tx", "rx"]
    parser = RtuFrameParser()
    gradual = [frame for byte in wire for frame in parser.feed(bytes([byte]))]
    assert gradual == whole


def test_unknown_fc_skipped_with_resync() -> None:
    parser = RtuFrameParser()
    frames = parser.feed(_crc(bytes([1, 7, 0, 1])) + _read_request(1, 3, 0, 1))
    assert frames == [SniffedFrame("tx", 1, 3, 0, 1, None, None)]


def test_format_frame() -> None:
    assert (
        format_frame(SniffedFrame("tx", 1, 3, 200, 6, None, None))
        == "→ read holding_registers unit=1 @200 x6"
    )
    assert format_frame(SniffedFrame("rx", 1, 3, 200, 6, [100, 101], None)) == "← 100, 101"
    assert (
        format_frame(SniffedFrame("tx", 1, 6, 5, 1, [77], None))
        == "→ write register unit=1 @5 value=77"
    )
    assert format_frame(SniffedFrame("rx", 1, 16, 0, 3, None, None)) == "← ok"
    assert (
        format_frame(SniffedFrame("rx", 1, 3, None, None, None, 0x02))
        == "× exception Illegal Data Address (0x02) unit=1"
    )


def test_bus_read_transaction_fills_values() -> None:
    bus = BusModel()
    seen: list[tuple] = []
    bus.on_values = lambda *args: seen.append(args)
    bus.handle_frame(_frame("tx", 1, 3, 10, 2))
    bus.handle_frame(_frame("rx", 1, 3, values=[5, 6]))
    assert bus.snapshot(1) == {"holding_registers": {10: 5, 11: 6}}
    assert seen == [(1, "holding_registers", 10, [5, 6])]


def test_bus_write_applies_immediately() -> None:
    bus = BusModel()
    bus.handle_frame(_frame("tx", 1, 6, 5, values=[77]))
    bus.handle_frame(_frame("tx", 1, 5, 0, values=[True]))
    bus.handle_frame(_frame("tx", 2, 16, 100, values=[1, 2]))
    bus.handle_frame(_frame("tx", 2, 15, 3, values=[True, True]))
    assert bus.snapshot(1)["holding_registers"] == {5: 77}
    assert bus.snapshot(1)["coils"] == {0: True}
    assert bus.snapshot(2)["holding_registers"] == {100: 1, 101: 2}
    assert bus.snapshot(2)["coils"] == {3: True, 4: True}


def test_bus_units_sorted_and_frame_log() -> None:
    bus = BusModel()
    lines: list[str] = []
    bus.on_frame = lines.append
    bus.handle_frame(_frame("tx", 9, 6, 0, values=[1]))
    bus.handle_frame(_frame("tx", 3, 6, 0, values=[1]))
    assert bus.units() == [3, 9]
    assert lines == [
        "→ write register unit=9 @0 value=1",
        "→ write register unit=3 @0 value=1",
    ]


def test_bus_exception_writes_no_values() -> None:
    bus = BusModel()
    seen: list[tuple] = []
    bus.on_values = lambda *args: seen.append(args)
    bus.handle_frame(_frame("tx", 1, 3, 0, 2))
    bus.handle_frame(_frame("rx", 1, 3, exception=0x02))
    assert seen == [] and bus.snapshot(1) == {}
    # pending снят exception'ом: поздний ответ без адреса значений не добавляет
    bus.handle_frame(_frame("rx", 1, 3, values=[1, 2]))
    assert bus.snapshot(1) == {}


def test_bus_snapshot_is_a_copy() -> None:
    bus = BusModel()
    bus.handle_frame(_frame("tx", 1, 6, 5, values=[77]))
    snap = bus.snapshot(1)
    snap["holding_registers"][5] = 0
    assert bus.snapshot(1)["holding_registers"] == {5: 77}


def test_serial_sniffer_bad_port_raises() -> None:
    sniffer = SerialSniffer()
    with pytest.raises(ConnectionError, match="Не удалось открыть порт"):
        sniffer.start(RtuParams(port="definitely/not/a/port"), lambda data: None)
    assert not sniffer.running


@pytest.mark.skipif(os.name == "nt", reason="pty недоступен на Windows")
def test_pty_full_chain() -> None:
    import pty

    master_fd, slave_fd = pty.openpty()
    slave_name = os.ttyname(slave_fd)
    backend = SnifferBackend()
    lines: list[str] = []
    values_seen: list[tuple] = []
    backend.on_frame = lines.append
    backend.on_values = lambda *args: values_seen.append(args)
    try:
        backend.start(RtuParams(port=slave_name, baudrate=9600))
        assert backend.running
        wire = _read_request(1, 3, 200, 2) + _read_response_regs(1, 3, [100, 101])
        os.write(master_fd, wire)
        assert _wait_for(
            lambda: backend.snapshot(1).get("holding_registers") == {200: 100, 201: 101}
        )
        assert backend.units() == [1]
        assert _wait_for(lambda: len(lines) == 2)
        assert lines[0] == "→ read holding_registers unit=1 @200 x2"
        assert lines[1] == "← 100, 101"
        assert values_seen == [(1, "holding_registers", 200, [100, 101])]
    finally:
        backend.stop()
        os.close(master_fd)
        os.close(slave_fd)
    assert not backend.running
