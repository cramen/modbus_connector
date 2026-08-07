from dataclasses import dataclass
from typing import Literal

RegisterKind = Literal["coils", "discrete_inputs", "holding_registers", "input_registers"]


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
