"""Пассивный сниффер RTU-шины: парсер кадров, модель состояния, чтение порта.

Без Qt. Декодер pymodbus не используется (заточен под активного клиента) —
свой компактный разбор со скользящим окном и ресинхронизацией по CRC16.
Направление кадра (tx: master→slave / rx: slave→master) физически не
различить, поэтому классификация — по структуре кадра и матчеру транзакций.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import serial

from .models import RegisterKind, RtuParams, describe_exception

logger = logging.getLogger(__name__)

Direction = Literal["tx", "rx"]  # tx: master→slave (запрос), rx: slave→master (ответ)

ValuesHook = Callable[[int, RegisterKind, int, list], None]  # (unit, kind, address, values)
FrameHook = Callable[[str], None]  # человекочитаемая строка кадра
ErrorHook = Callable[[str], None]  # человекочитаемая ошибка потока чтения

MAX_FRAME = 256  # максимальный кадр: fc15/16 запрос — 9 байт + до 246 байт данных

_FC_NAMES = {
    1: "read coils",
    2: "read discrete_inputs",
    3: "read holding_registers",
    4: "read input_registers",
    5: "write coil",
    6: "write register",
    15: "write coils",
    16: "write registers",
}

_FC_KIND: dict[int, RegisterKind] = {
    1: "coils",
    2: "discrete_inputs",
    3: "holding_registers",
    4: "input_registers",
    5: "coils",
    6: "holding_registers",
    15: "coils",
    16: "holding_registers",
}


def crc16(data: bytes) -> int:
    """Modbus CRC16 (poly 0xA001, init 0xFFFF); в кадре младший байт первым."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


@dataclass(frozen=True)
class SniffedFrame:
    """Разобранный кадр: direction, unit/fc, адрес/количество, данные, exception."""

    direction: Direction
    unit: int
    function_code: int
    address: int | None  # None, если адрес не восстановить (ответ без pending)
    count: int | None
    values: list[int | bool] | None  # данные ответа/записи: u16 регистры, bool биты
    exception_code: int | None


def _u16(data: bytes | bytearray, offset: int) -> int:
    return (data[offset] << 8) | data[offset + 1]


def _decode_registers(data: bytes | bytearray) -> list[int]:
    return [_u16(data, i) for i in range(0, len(data) - 1, 2)]


def _decode_bits(data: bytes | bytearray, count: int | None) -> list[bool]:
    bits = [bool(data[i // 8] >> (i % 8) & 1) for i in range(len(data) * 8)]
    return bits[:count] if count is not None else bits


def format_frame(frame: SniffedFrame) -> str:
    """Человекочитаемая строка кадра в стиле request_tracer'а sim_backend."""
    name = _FC_NAMES.get(frame.function_code, f"function 0x{frame.function_code:02X}")
    if frame.exception_code is not None:
        return f"× exception {describe_exception(frame.exception_code)} unit={frame.unit}"
    if frame.direction == "tx":
        parts = [f"→ {name} unit={frame.unit}"]
        if frame.address is not None:
            parts.append(f"@{frame.address}")
        if frame.count is not None and frame.function_code not in (5, 6):
            parts.append(f"x{frame.count}")
        if frame.values is not None:
            if len(frame.values) == 1:
                parts.append(f"value={frame.values[0]}")
            else:
                parts.append(f"values={frame.values}")
        return " ".join(parts)
    if frame.values is not None:
        return f"← {', '.join(str(v) for v in frame.values)}"
    if frame.function_code in (15, 16):
        return "← ok"
    return f"← {name} unit={frame.unit}"


class RtuFrameParser:
    """Скользящее окно по потоку байт с линии: feed(data) -> list[SniffedFrame].

    На каждой позиции пробуются структуры кадра с проверкой CRC16; не сошлось —
    сдвиг на 1 байт (ресинхронизация от мусора). Направление: exception всегда
    rx и проверяется первым; далее структуры по fc. Если валидны обе структуры
    (запрос и ответ) — решает матчер: есть pending с тем же unit+fc → ответ.
    Эхо fc5/6: первый кадр — запрос (pending), повторный с тем же unit+fc+
    адрес+значение — ответ, отличающийся — новая запись.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._pending: dict[tuple[int, int], tuple[int, int]] = {}  # (unit, fc) -> (addr, count)

    def feed(self, data: bytes) -> list[SniffedFrame]:
        self._buffer.extend(data)
        frames: list[SniffedFrame] = []
        while (found := self._scan()) is not None:
            pos, frame, length = found
            del self._buffer[: pos + length]  # мусор до кадра отбрасывается
            frames.append(frame)
        if len(self._buffer) > MAX_FRAME:
            # валидный кадр короче MAX_FRAME: более старые байты — точно мусор
            del self._buffer[: len(self._buffer) - MAX_FRAME]
        return frames

    def _scan(self) -> tuple[int, SniffedFrame, int] | None:
        """Первый валидный кадр в окне: (позиция, кадр, длина) или None."""
        for pos in range(len(self._buffer) - 1):
            parsed = self._parse_at(pos)
            if parsed is not None:
                return pos, parsed[0], parsed[1]
        return None

    def _parse_at(self, pos: int) -> tuple[SniffedFrame, int] | None:
        buf = self._buffer[pos:]
        unit, fc = buf[0], buf[1]
        matches: list[tuple[str, int]] = []
        for kind, length in self._candidates(fc, buf):
            if length is None or len(buf) < length:
                continue  # байт мало — структуру нельзя ни подтвердить, ни исключить
            if crc16(bytes(buf[: length - 2])) == _u16_le(buf, length - 2):
                matches.append((kind, length))
        if not matches:
            return None
        if len(matches) > 1:
            # валидны обе структуры — решает матчер транзакций
            want = "response" if (unit, fc) in self._pending else "request"
            kind, length = next((m for m in matches if m[0] == want), matches[0])
        else:
            kind, length = matches[0]
        return self._build_frame(kind, length, buf, unit, fc), length

    @staticmethod
    def _candidates(fc: int, buf: bytearray) -> list[tuple[str, int | None]]:
        """(вид структуры, длина кадра); длина None — пока не вычислить."""
        if fc & 0x80:
            # exception: unit, fc|0x80, code, crc — проверяется первым
            return [("exception", 5)] if (fc & 0x7F) in _FC_NAMES else []
        if fc in (1, 2, 3, 4):
            # запрос 8 байт; ответ unit,fc,bytecount,data...,crc (5 + bc)
            response: int | None = None
            if len(buf) >= 3:
                bc = buf[2]
                if bc >= 1 and (fc in (1, 2) or bc % 2 == 0):  # регистры — чётный bc
                    response = 5 + bc
            return [("request", 8), ("response", response)]
        if fc in (5, 6):
            return [("echo", 8)]  # запрос и ответ-эхо — одна структура 8 байт
        if fc in (15, 16):
            # запрос unit,fc,addr2,count2,bc,data,crc (9 + bc); ответ 8 байт
            request: int | None = None
            if len(buf) >= 7:
                count, bc = _u16(buf, 4), buf[6]
                expected = count * 2 if fc == 16 else (count + 7) // 8
                if bc == expected and bc >= 1:
                    request = 9 + bc
            return [("request", request), ("response", 8)]
        return []

    def _build_frame(
        self, kind: str, length: int, buf: bytearray, unit: int, fc: int
    ) -> SniffedFrame:
        if kind == "exception":
            base_fc = fc & 0x7F
            self._pending.pop((unit, base_fc), None)
            return SniffedFrame("rx", unit, base_fc, None, None, None, buf[2])
        if kind == "echo":  # fc 5/6: запрос и ответ — 8-байтовое эхо
            address, word = _u16(buf, 2), _u16(buf, 4)
            values: list[int | bool] = [bool(word)] if fc == 5 else [word]
            key = (unit, fc)
            if self._pending.get(key) == (address, word):
                del self._pending[key]
                return SniffedFrame("rx", unit, fc, address, 1, values, None)
            self._pending[key] = (address, word)
            return SniffedFrame("tx", unit, fc, address, 1, values, None)
        address, count = _u16(buf, 2), _u16(buf, 4)
        if kind == "request":
            if fc in (1, 2, 3, 4):
                self._pending[(unit, fc)] = (address, count)
                return SniffedFrame("tx", unit, fc, address, count, None, None)
            # fc 15/16: данные записи после bytecount (offset 6)
            data = buf[7 : length - 2]
            values = _decode_registers(data) if fc == 16 else _decode_bits(data, count)
            self._pending[(unit, fc)] = (address, count)
            return SniffedFrame("tx", unit, fc, address, count, values, None)
        # response: unit,fc,bytecount,data...,crc
        if fc in (15, 16):
            self._pending.pop((unit, fc), None)
            return SniffedFrame("rx", unit, fc, address, count, None, None)
        pending = self._pending.pop((unit, fc), None)
        data = buf[3 : length - 2]
        if fc in (3, 4):
            values = _decode_registers(data)
        else:
            values = _decode_bits(data, pending[1] if pending else None)
        if pending is not None:
            return SniffedFrame("rx", unit, fc, pending[0], pending[1], values, None)
        return SniffedFrame("rx", unit, fc, None, None, values, None)


def _u16_le(data: bytes | bytearray, offset: int) -> int:
    return data[offset] | (data[offset + 1] << 8)


class BusModel:
    """Состояние шины по проходящим кадрам: матчер транзакций + значения.

    pending по (unit, fc): read-ответ применяется к адресу из pending-запроса,
    записи мастера (fc 5/6/15/16) применяются сразу, exception снимает pending.
    on_values(unit, kind, start_address, values) — каждое применённое изменение;
    on_frame(line) — человекочитаемые строки всех кадров.
    """

    def __init__(self) -> None:
        self.on_values: ValuesHook | None = None
        self.on_frame: FrameHook | None = None
        self._values: dict[int, dict[RegisterKind, dict[int, int | bool]]] = {}
        self._pending: dict[tuple[int, int], tuple[int, int]] = {}

    def handle_frame(self, frame: SniffedFrame) -> None:
        self._emit_frame(format_frame(frame))
        fc = frame.function_code
        key = (frame.unit, fc)
        if frame.exception_code is not None:
            self._pending.pop(key, None)
            return
        if frame.direction == "tx":
            if fc in (1, 2, 3, 4):
                if frame.address is not None and frame.count is not None:
                    self._pending[key] = (frame.address, frame.count)
            elif frame.address is not None and frame.values is not None:
                self._apply(frame.unit, _FC_KIND[fc], frame.address, frame.values)
        elif fc in (1, 2, 3, 4):  # read-ответ: адрес из pending-запроса
            pending = self._pending.pop(key, None)
            address = pending[0] if pending is not None else frame.address
            if address is not None and frame.values is not None:
                self._apply(frame.unit, _FC_KIND[fc], address, frame.values)
        # ответы записей (эхо fc 5/6, ответ fc 15/16) — уже применены на запросе

    def units(self) -> list[int]:
        """Unit-адреса с известными значениями, отсортированные."""
        return sorted(self._values)

    def snapshot(self, unit: int) -> dict[RegisterKind, dict[int, int | bool]]:
        """Копия значений unit: {kind: {address: value}} (только виденные kind)."""
        return {kind: dict(values) for kind, values in self._values.get(unit, {}).items()}

    def _apply(
        self, unit: int, kind: RegisterKind, address: int, values: list[int | bool]
    ) -> None:
        area = self._values.setdefault(unit, {}).setdefault(kind, {})
        for offset, value in enumerate(values):
            area[address + offset] = value
        hook = self.on_values
        if hook is None:
            return
        try:
            hook(unit, kind, address, list(values))
        except Exception:
            logger.exception("Ошибка в on_values")

    def _emit_frame(self, line: str) -> None:
        hook = self.on_frame
        if hook is None:
            return
        try:
            hook(line)
        except Exception:
            logger.exception("Ошибка в on_frame")


class SerialSniffer:
    """Чтение RTU-порта в отдельном потоке, сырые байты наружу через on_bytes.

    read(256) с timeout=0.1 для периодической проверки флага остановки;
    ошибка открытия порта — ConnectionError, ошибки чтения — on_error.
    """

    def __init__(self) -> None:
        self.on_error: ErrorHook | None = None
        self._serial: serial.Serial | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, params: RtuParams, on_bytes: Callable[[bytes], None]) -> None:
        if self._thread is not None:
            raise RuntimeError("Сниффер уже запущен: сначала вызовите stop()")
        try:
            port = serial.Serial(
                port=params.port,
                baudrate=params.baudrate,
                bytesize=params.bytesize,
                parity=params.parity,
                stopbits=params.stopbits,
                timeout=0.1,  # не params.timeout: периодический выход из read
            )
        except Exception as exc:
            raise ConnectionError(
                f"Не удалось открыть порт {params.port} "
                f"({params.baudrate}/{params.parity}): {exc}"
            ) from exc
        logger.info("Сниффер запущен: %s @ %s", params.port, params.baudrate)
        self._serial = port
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(port, on_bytes), daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, port = self._thread, self._serial
        self._thread = None
        self._serial = None
        if thread is not None:
            thread.join(timeout=2.0)
        if port is not None and port.is_open:
            port.close()

    def _run(self, port: serial.Serial, on_bytes: Callable[[bytes], None]) -> None:
        while not self._stop.is_set():
            try:
                data = port.read(256)
            except Exception as exc:
                if not self._stop.is_set():
                    self._emit_error(f"Ошибка чтения порта {port.port}: {exc}")
                return
            if data:
                try:
                    on_bytes(bytes(data))
                except Exception:
                    logger.exception("Ошибка в on_bytes")

    def _emit_error(self, message: str) -> None:
        logger.error("%s", message)
        hook = self.on_error
        if hook is None:
            return
        try:
            hook(message)
        except Exception:
            logger.exception("Ошибка в on_error")


class SnifferBackend:
    """Композиция: SerialSniffer → RtuFrameParser → BusModel.

    Колбэки on_values/on_frame/on_error вызываются из потока чтения порта
    (как хуки SimBackend — из потока сервера). start() сбрасывает парсер
    и модель: новая сессия сниффинга начинается с чистого состояния.
    """

    def __init__(self) -> None:
        self.on_values: ValuesHook | None = None
        self.on_frame: FrameHook | None = None
        self.on_error: ErrorHook | None = None
        self._sniffer = SerialSniffer()
        self._sniffer.on_error = self._emit_error
        self._parser = RtuFrameParser()
        self._bus = BusModel()
        self._wire_bus()
        self._lock = threading.Lock()

    def _wire_bus(self) -> None:
        self._bus.on_values = self._emit_values
        self._bus.on_frame = self._emit_frame

    @property
    def running(self) -> bool:
        return self._sniffer.running

    def start(self, params: RtuParams) -> None:
        with self._lock:
            self._parser = RtuFrameParser()
            self._bus = BusModel()
            self._wire_bus()
        self._sniffer.start(params, self._handle_bytes)

    def stop(self) -> None:
        self._sniffer.stop()

    def units(self) -> list[int]:
        with self._lock:
            return self._bus.units()

    def snapshot(self, unit: int) -> dict[RegisterKind, dict[int, int | bool]]:
        with self._lock:
            return self._bus.snapshot(unit)

    def _handle_bytes(self, data: bytes) -> None:
        with self._lock:
            frames = self._parser.feed(data)
            for frame in frames:
                self._bus.handle_frame(frame)

    def _emit_values(self, unit: int, kind: RegisterKind, address: int, values: list) -> None:
        hook = self.on_values
        if hook is not None:
            hook(unit, kind, address, values)

    def _emit_frame(self, line: str) -> None:
        hook = self.on_frame
        if hook is not None:
            hook(line)

    def _emit_error(self, message: str) -> None:
        hook = self.on_error
        if hook is not None:
            hook(message)
