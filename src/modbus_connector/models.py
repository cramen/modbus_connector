import csv
import io
import struct
from dataclasses import dataclass, field
from typing import Literal, get_args

RegisterKind = Literal["coils", "discrete_inputs", "holding_registers", "input_registers"]

DisplayFormat = Literal["dec", "hex", "s16", "u32", "s32", "f32", "u64", "s64", "f64", "ascii"]

ByteOrder = Literal["ABCD", "CDAB", "BADC", "DCBA"]


@dataclass(frozen=True)
class TcpParams:
    host: str
    port: int = 502
    timeout: float = 3.0


@dataclass(frozen=True)
class RtuOverTcpParams:
    host: str
    port: int = 502
    timeout: float = 3.0


@dataclass(frozen=True)
class RtuOverUdpParams:
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


ConnectionParams = TcpParams | RtuParams | RtuOverTcpParams | RtuOverUdpParams


def describe_connection(params: ConnectionParams) -> str:
    if isinstance(params, TcpParams):
        return f"tcp {params.host}:{params.port}"
    if isinstance(params, RtuOverTcpParams):
        return f"rtu/tcp {params.host}:{params.port}"
    if isinstance(params, RtuOverUdpParams):
        return f"rtu/udp {params.host}:{params.port}"
    return f"rtu {params.port} @ {params.baudrate}"


@dataclass
class RegisterRow:
    name: str
    kind: RegisterKind
    address: int
    count: int = 1
    format: DisplayFormat = "dec"
    order: ByteOrder | None = None  # None = inherit the panel's global order
    scale: float = 1.0
    offset: float = 0.0
    unit: str = ""
    unit_id: int | None = None  # None = use the global unit from the connection panel
    poll_ms: int | None = None  # None = use the global polling interval


@dataclass
class RowDisplaySettings:
    scale: float = 1.0
    offset: float = 0.0
    unit: str = ""
    order: ByteOrder | None = None  # None = inherit the panel's global order
    log: bool = True  # include the row when logging values to a file


CSV_COLUMNS = [
    "name", "kind", "address", "count", "unit_id", "poll_ms",
    "format", "scale", "offset", "unit", "order",
]

CSV_ALIASES = {"type": "kind"}  # file column name (lowercased) -> target field


def row_to_csv_record(row: RegisterRow, display: RowDisplaySettings) -> dict[str, object]:
    return {
        "name": row.name,
        "kind": row.kind,
        "address": row.address,
        "count": row.count,
        "unit_id": "" if row.unit_id is None else row.unit_id,
        "poll_ms": "" if row.poll_ms is None else row.poll_ms,
        "format": row.format,
        "scale": display.scale,
        "offset": display.offset,
        "unit": display.unit,
        "order": display.order or "",  # "" = inherit the global order
    }


def row_to_csv_cells(
    row: RegisterRow, display: RowDisplaySettings, columns: list[str] | None = None
) -> list[object]:
    record = row_to_csv_record(row, display)
    return [record[column] for column in (columns or CSV_COLUMNS)]


def rows_to_csv(
    rows: list[RegisterRow],
    displays: list[RowDisplaySettings],
    columns: list[str] | None = None,
) -> str:
    columns = columns or CSV_COLUMNS
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row, display in zip(rows, displays, strict=True):
        writer.writerow(row_to_csv_cells(row, display, columns))
    return buffer.getvalue()


def csv_header(text: str) -> list[str]:
    first_line = text.split("\n", 1)[0]
    return next(csv.reader([first_line]), [])


def guess_column_mapping(header: list[str]) -> dict[str, str]:
    """Угадывает сопоставление «колонка файла -> поле» по имени (регистр не важен)."""
    mapping = {}
    for column in header:
        name = column.strip().lower()
        if name in CSV_COLUMNS:
            mapping[column] = name
        elif name in CSV_ALIASES:
            mapping[column] = CSV_ALIASES[name]
    return mapping


def _csv_int(text: str, default: int) -> int:
    try:
        return int(text, 0)
    except ValueError:
        return default


def _csv_opt_int(text: str, lo: int, hi: int) -> int | None:
    try:
        value = int(text, 0) if text else None
    except ValueError:
        value = None
    return value if value is not None and lo <= value <= hi else None


def _csv_float(text: str, default: float) -> float:
    try:
        return float(text) if text else default
    except ValueError:
        return default


def rows_from_csv(
    text: str, mapping: dict[str, str] | None = None
) -> list[tuple[RegisterRow, RowDisplaySettings]]:
    """Парсит CSV таблицы регистров (заголовок обязателен, регистр не важен).

    mapping «колонка файла -> поле» задаётся явно или угадывается по именам;
    колонки вне сопоставления игнорируются, поля вне сопоставления получают
    значения по умолчанию; строки с нечитаемым address пропускаются.
    ValueError — не сопоставлены обязательные поля (name/kind/address) или
    ни одной корректной строки.
    """
    reader = csv.DictReader(io.StringIO(text))
    effective = (
        {key.strip(): field for key, field in mapping.items()}
        if mapping is not None
        else guess_column_mapping([name for name in reader.fieldnames or []])
    )
    for essential in ("name", "kind", "address"):
        if essential not in effective.values():
            raise ValueError(f"В CSV не сопоставлено обязательное поле {essential!r}")
    result = []
    for record in reader:
        data = {
            effective[key.strip()]: (value or "").strip()
            for key, value in record.items()
            if key is not None and key.strip() in effective
        }
        try:
            address = int(data["address"], 0)
        except (KeyError, ValueError):
            continue  # rows without a parseable address are skipped
        kind = data.get("kind", "")
        fmt = data.get("format", "")
        order = data.get("order", "")
        result.append(
            (
                RegisterRow(
                    name=data.get("name", ""),
                    kind=(
                        kind if kind in get_args(RegisterKind) else "holding_registers"
                    ),
                    address=address,
                    count=_csv_int(data.get("count", ""), 1),
                    format=fmt if fmt in get_args(DisplayFormat) else "dec",
                    unit_id=_csv_opt_int(data.get("unit_id", ""), 1, 247),
                    poll_ms=_csv_opt_int(data.get("poll_ms", ""), 100, 600_000),
                ),
                RowDisplaySettings(
                    scale=_csv_float(data.get("scale", ""), 1.0),
                    offset=_csv_float(data.get("offset", ""), 0.0),
                    unit=data.get("unit", ""),
                    order=order if order in get_args(ByteOrder) else None,
                ),
            )
        )
    if not result:
        raise ValueError("В CSV нет ни одной корректной строки")
    return result


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


def _order_permutation(order: ByteOrder, n_bytes: int) -> list[int]:
    """Indices that reorder arriving group bytes into canonical big-endian.

    The literal names the byte roles in arrival order: ABCD = already canonical
    (first register is the high word, big-endian bytes), CDAB = 16-bit words in
    reverse order, BADC = bytes swapped within each 16-bit word,
    DCBA = full byte reverse.
    """
    if order == "BADC":
        return [i + 1 if i % 2 == 0 else i - 1 for i in range(n_bytes)]
    if order == "CDAB":
        perm = []
        for word in range(n_bytes // 2 - 1, -1, -1):
            perm += [word * 2, word * 2 + 1]
        return perm
    if order == "DCBA":
        return list(range(n_bytes - 1, -1, -1))
    return list(range(n_bytes))


_GROUP_SIZES = {"u32": 2, "s32": 2, "f32": 2, "u64": 4, "s64": 4, "f64": 4}


def decode_register_values(
    values: list[int], fmt: DisplayFormat, order: ByteOrder = "ABCD"
) -> list[int | float]:
    """Decode raw registers into numbers per fmt/order.

    32-bit formats combine register pairs, 64-bit formats groups of four;
    trailing registers that do not fill a whole group pass through as decimals.
    Only numeric formats are supported — hex/ascii are string formats handled
    directly by format_register_values.
    """
    if fmt == "dec":
        return [int(value) for value in values]
    if fmt == "s16":
        return [_to_s16(value) for value in values]
    group = _GROUP_SIZES[fmt]
    decoded: list[int | float] = []
    groups_end = len(values) - len(values) % group
    for i in range(0, groups_end, group):
        raw = b"".join(value.to_bytes(2, "big") for value in values[i : i + group])
        data = bytes(raw[j] for j in _order_permutation(order, len(raw)))
        if fmt in ("u32", "u64"):
            decoded.append(int.from_bytes(data, "big"))
        elif fmt in ("s32", "s64"):
            decoded.append(int.from_bytes(data, "big", signed=True))
        elif fmt == "f32":
            decoded.append(struct.unpack(">f", data)[0])
        else:  # f64
            decoded.append(struct.unpack(">d", data)[0])
    decoded.extend(values[groups_end:])
    return decoded


def format_register_values(
    values: list[int], fmt: DisplayFormat, order: ByteOrder = "ABCD"
) -> str:
    """Render raw registers in the given display format.

    `order` permutes the bytes of each group (see _order_permutation; for
    64-bit groups the pattern tiles over all 8 bytes: CDAB reverses the four
    16-bit words, BADC swaps bytes within each word, DCBA reverses everything).
    """
    if fmt == "hex":
        return ", ".join(f"0x{v:04X}" for v in values)
    if fmt == "ascii":
        # two chars per register (high, low byte); NUL terminates, other
        # non-printable bytes show as '.'; `order` does not apply to strings
        chars = []
        for value in values:
            for byte in value.to_bytes(2, "big"):
                if byte == 0:
                    return "".join(chars)
                chars.append(chr(byte) if 0x20 <= byte <= 0x7E else ".")
        return "".join(chars)
    return ", ".join(
        f"{v:.6g}" if isinstance(v, float) else str(v)
        for v in decode_register_values(values, fmt, order)
    )


def format_scaled_values(
    values: list[int | float], scale: float, offset: float, unit: str
) -> str:
    text = ", ".join(f"{v * scale + offset:.4g}" for v in values)
    return f"{text} {unit}" if text and unit else text


EXCEPTION_CODES: dict[int, str] = {
    0x01: "Illegal Function",
    0x02: "Illegal Data Address",
    0x03: "Illegal Data Value",
    0x04: "Slave Device Failure",
    0x05: "Acknowledge",
    0x06: "Slave Device Busy",
    0x08: "Memory Parity Error",
    0x0A: "Gateway Path Unavailable",
    0x0B: "Gateway Target Device Failed to Respond",
}


def describe_exception(code: int) -> str:
    name = EXCEPTION_CODES.get(code)
    return f"{name} (0x{code:02X})" if name else f"Exception 0x{code:02X}"


@dataclass(frozen=True)
class StatsSnapshot:
    total: int = 0
    errors: int = 0
    avg_ms: float = 0.0  # mean duration of successful operations only
    last_ms: float = 0.0
    error_kinds: dict[str, int] = field(default_factory=dict)

    @property
    def error_percent(self) -> float:
        return self.errors / self.total * 100 if self.total else 0.0

    @property
    def top_error_kind(self) -> str | None:
        if not self.error_kinds:
            return None
        return max(self.error_kinds, key=lambda kind: self.error_kinds[kind])


class Stats:
    def __init__(self) -> None:
        self._total = 0
        self._errors = 0
        self._ok_count = 0
        self._ok_ms = 0.0
        self._last_ms = 0.0
        self._error_kinds: dict[str, int] = {}

    def record(self, ok: bool, duration_ms: float, error_kind: str | None = None) -> None:
        self._total += 1
        self._last_ms = duration_ms
        if ok:
            self._ok_count += 1
            self._ok_ms += duration_ms
        else:
            self._errors += 1
            kind = error_kind or "other"
            self._error_kinds[kind] = self._error_kinds.get(kind, 0) + 1

    def snapshot(self) -> StatsSnapshot:
        return StatsSnapshot(
            total=self._total,
            errors=self._errors,
            avg_ms=self._ok_ms / self._ok_count if self._ok_count else 0.0,
            last_ms=self._last_ms,
            error_kinds=dict(self._error_kinds),
        )

    def reset(self) -> None:
        self._total = 0
        self._errors = 0
        self._ok_count = 0
        self._ok_ms = 0.0
        self._last_ms = 0.0
        self._error_kinds.clear()
