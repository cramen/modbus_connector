import struct
from dataclasses import dataclass
from typing import Literal

RegisterKind = Literal["coils", "discrete_inputs", "holding_registers", "input_registers"]

DisplayFormat = Literal["dec", "hex", "s16", "u32", "s32", "f32"]


@dataclass(frozen=True)
class TcpParams:
    host: str
    port: int = 502
    timeout: float = 3.0


@dataclass(frozen=True)
class RtuParams:
    port: str
    baudrate: int = 9600
    bytesize: int = 8
    parity: str = "N"
    stopbits: int = 1
    timeout: float = 3.0


ConnectionParams = TcpParams | RtuParams


@dataclass
class RegisterRow:
    name: str
    kind: RegisterKind
    address: int
    count: int = 1
    format: DisplayFormat = "dec"
    scale: float = 1.0
    offset: float = 0.0
    unit: str = ""
    unit_id: int | None = None  # None = use the global unit from the connection panel


@dataclass
class ScanProbe:
    kind: RegisterKind
    address: int = 0
    count: int = 1


DEFAULT_SCAN_PROBES: list[ScanProbe] = [
    ScanProbe("holding_registers", 0, 1),
    ScanProbe("input_registers", 0, 1),
    ScanProbe("coils", 0, 1),
]

_BOOL_TOKENS: dict[str, bool] = {
    "0": False,
    "1": True,
    "false": False,
    "true": True,
    "off": False,
    "on": True,
}


def _split_tokens(text: str) -> list[str]:
    return [token for token in text.replace(",", " ").split() if token]


def parse_values(kind: RegisterKind, text: str) -> list[int | bool]:
    tokens = _split_tokens(text)
    if not tokens:
        raise ValueError("Пустой ввод: введите значения через запятую или пробел")
    if kind in ("coils", "discrete_inputs"):
        values: list[int | bool] = []
        for token in tokens:
            value = _BOOL_TOKENS.get(token.lower())
            if value is None:
                raise ValueError(
                    f"Недопустимое значение coil: {token!r} "
                    "(ожидается 0/1, true/false, on/off)"
                )
            values.append(value)
        return values
    values = []
    for token in tokens:
        try:
            value = int(token, 0)
        except ValueError:
            raise ValueError(
                f"Недопустимое значение регистра: {token!r} "
                "(ожидается целое число, hex как 0x1A)"
            ) from None
        if not 0 <= value <= 0xFFFF:
            raise ValueError(f"Значение регистра вне диапазона 0..65535: {token!r}")
        values.append(value)
    return values


def format_values(values: list[int | bool]) -> str:
    return ", ".join(str(int(v)) for v in values)


def _to_s16(value: int) -> int:
    return value - 0x10000 if value >= 0x8000 else value


def _to_s32(value: int) -> int:
    return value - 0x1_0000_0000 if value >= 0x8000_0000 else value


def format_register_values(values: list[int], fmt: DisplayFormat) -> str:
    if fmt == "dec":
        return format_values(values)
    if fmt == "hex":
        return ", ".join(f"0x{v:04X}" for v in values)
    if fmt == "s16":
        return ", ".join(str(_to_s16(v)) for v in values)
    parts = []
    pairs_end = len(values) - len(values) % 2
    for i in range(0, pairs_end, 2):
        combined = values[i] << 16 | values[i + 1]  # big-endian: first register is high word
        if fmt == "u32":
            parts.append(str(combined))
        elif fmt == "s32":
            parts.append(str(_to_s32(combined)))
        else:  # f32
            parts.append(f"{struct.unpack('>f', struct.pack('>I', combined))[0]:.6g}")
    if len(values) % 2:
        parts.append(str(values[-1]))  # odd trailing register has no pair, show as decimal
    return ", ".join(parts)


def format_scaled_values(values: list[int], scale: float, offset: float, unit: str) -> str:
    text = ", ".join(f"{v * scale + offset:.4g}" for v in values)
    return f"{text} {unit}" if text and unit else text


@dataclass(frozen=True)
class StatsSnapshot:
    total: int = 0
    errors: int = 0
    avg_ms: float = 0.0  # mean duration of successful operations only
    last_ms: float = 0.0

    @property
    def error_percent(self) -> float:
        return self.errors / self.total * 100 if self.total else 0.0


class Stats:
    def __init__(self) -> None:
        self._total = 0
        self._errors = 0
        self._ok_count = 0
        self._ok_ms = 0.0
        self._last_ms = 0.0

    def record(self, ok: bool, duration_ms: float) -> None:
        self._total += 1
        self._last_ms = duration_ms
        if ok:
            self._ok_count += 1
            self._ok_ms += duration_ms
        else:
            self._errors += 1

    def snapshot(self) -> StatsSnapshot:
        return StatsSnapshot(
            total=self._total,
            errors=self._errors,
            avg_ms=self._ok_ms / self._ok_count if self._ok_count else 0.0,
            last_ms=self._last_ms,
        )

    def reset(self) -> None:
        self._total = 0
        self._errors = 0
        self._ok_count = 0
        self._ok_ms = 0.0
        self._last_ms = 0.0
