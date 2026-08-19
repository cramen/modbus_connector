import ast
import csv
import io
import math
import re
import struct
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import CodeType
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
    # alarm rules over the scaled primary value (AlarmRule is defined below)
    alarms: list["AlarmRule"] = field(default_factory=list)


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


def register_width(fmt: DisplayFormat) -> int:
    """Число 16-битных регистров на одно значение формата (1/2/4)."""
    return _GROUP_SIZES.get(fmt, 1)


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


def encode_register_values(
    value: float, fmt: DisplayFormat, order: ByteOrder = "ABCD"
) -> list[int]:
    """Encode a number into raw registers (inverse of decode_register_values).

    dec/s16 — один регистр (round + clamp в диапазон формата), 32/64-битные
    форматы — группа из 2/4 регистров с учётом порядка байт; для f32/f64
    clamp нет — непредставимое значение даёт OverflowError из struct.pack.
    hex/ascii — строковые форматы отображения, кодированию не подлежат
    (ValueError). Используется симулятором, чтобы писать вычисленные
    правилами числа обратно в карту регистров.
    """
    if fmt == "dec":
        return [round(_clamp(value, 0, 0xFFFF))]
    if fmt == "s16":
        return [round(_clamp(value, -0x8000, 0x7FFF)) & 0xFFFF]
    if fmt not in _GROUP_SIZES:
        raise ValueError(f"Формат {fmt!r} не кодируется в регистры")
    group = _GROUP_SIZES[fmt]
    bits = 16 * group
    if fmt in ("u32", "u64"):
        data = round(_clamp(value, 0, 2**bits - 1)).to_bytes(2 * group, "big")
    elif fmt in ("s32", "s64"):
        half = 2 ** (bits - 1)
        data = round(_clamp(value, -half, half - 1)).to_bytes(2 * group, "big", signed=True)
    elif fmt == "f32":
        data = struct.pack(">f", value)
    else:  # f64
        data = struct.pack(">d", value)
    # обратная перестановка: канонические байты раскладываем по местам прибытия
    perm = _order_permutation(order, len(data))
    raw = bytes(data[perm[j]] for j in range(len(data)))
    return [int.from_bytes(raw[i : i + 2], "big") for i in range(0, len(raw), 2)]


def parse_formatted_values(text: str, fmt: DisplayFormat, count: int) -> list[int]:
    """Разбор ввода в формате отображения: числа через запятую/пробел.

    Каждое число кодируется encode_register_values в свою группу регистров
    (ширина группы — register_width(fmt)); результат дополняется нулями/
    обрезается до count регистров. Для dec/hex ввода — parse_values, для
    ascii — encode_ascii_values. ValueError на пустом вводе и нечисловых
    токенах, OverflowError на непредставимом float."""
    parts = [p for p in re.split(r"[,\s]+", text.strip()) if p]
    if not parts:
        raise ValueError("Пустой ввод: введите значения через запятую или пробел")
    encoded: list[int] = []
    for part in parts:
        try:
            number = float(part)
        except ValueError:
            raise ValueError(f"Недопустимое число: {part!r}") from None
        encoded.extend(encode_register_values(number, fmt))
    return (encoded + [0] * count)[:count]


def encode_ascii_values(text: str, count: int) -> list[int]:
    """Текст → регистры для ascii-формата: 2 символа на регистр (high byte
    first), нечётная длина и хвост добиваются NUL; непечатные/не-ASCII
    символы заменяются на '?'. Результат — ровно count регистров (pad/truncate).
    Зеркало ascii-ветки format_register_values."""
    raw = bytearray()
    for ch in text:
        code = ord(ch)
        raw.append(code if 0x20 <= code <= 0x7E else 0x3F)
    if len(raw) % 2:
        raw.append(0)
    values = [int.from_bytes(raw[i : i + 2], "big") for i in range(0, len(raw), 2)]
    return (values + [0] * count)[:count]


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


AlarmCondition = Literal["gt", "lt", "ge", "le", "eq", "ne", "in_range", "outside_range"]

AlarmColor = Literal["red", "yellow"]

_RANGE_CONDITIONS = ("in_range", "outside_range")


@dataclass(frozen=True)
class AlarmRule:
    """Правило аларма над числовым значением регистра.

    Для диапазонных условий (in_range/outside_range) value/value2 — границы,
    нормализуются в порядке (нижняя, верхняя); границы включаются в диапазон.
    Для остальных условий value2 должен быть None. log/sound — флаги реакции
    (запись в лог / звук), потребитель решает, как их применять.
    """

    condition: AlarmCondition
    value: float
    value2: float | None = None
    color: AlarmColor = "red"
    log: bool = True
    sound: bool = False

    def __post_init__(self) -> None:
        if self.condition not in get_args(AlarmCondition):
            raise ValueError(f"Неизвестное условие аларма: {self.condition!r}")
        if self.color not in get_args(AlarmColor):
            raise ValueError(f"Неизвестный цвет аларма: {self.color!r}")
        if self.condition in _RANGE_CONDITIONS:
            if self.value2 is None:
                raise ValueError(f"Условие {self.condition!r} требует value2")
            if self.value > self.value2:
                lo, hi = self.value2, self.value
                object.__setattr__(self, "value", lo)
                object.__setattr__(self, "value2", hi)
        elif self.value2 is not None:
            raise ValueError(f"Условие {self.condition!r} не использует value2")


def rule_matches(rule: AlarmRule, x: float) -> bool:
    """Проверить значение против одного правила (границы диапазона включительно)."""
    condition = rule.condition
    if condition == "gt":
        return x > rule.value
    if condition == "ge":
        return x >= rule.value
    if condition == "lt":
        return x < rule.value
    if condition == "le":
        return x <= rule.value
    if condition == "eq":
        return x == rule.value
    if condition == "ne":
        return x != rule.value
    in_range = rule.value <= x <= (rule.value2 if rule.value2 is not None else rule.value)
    return in_range if condition == "in_range" else not in_range


def evaluate_alarm(x: float, rules: Sequence[AlarmRule]) -> AlarmRule | None:
    """Первое совпавшее правило (приоритет = порядок в списке) или None."""
    for rule in rules:
        if rule_matches(rule, x):
            return rule
    return None


def alarm_rule_to_json(rule: AlarmRule) -> dict[str, object]:
    data: dict[str, object] = {
        "condition": rule.condition,
        "value": rule.value,
        "color": rule.color,
        "log": rule.log,
        "sound": rule.sound,
    }
    if rule.value2 is not None:
        data["value2"] = rule.value2
    return data


def _json_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def alarm_rule_from_json(data: object) -> AlarmRule | None:
    """Разобрать правило из JSON-словаря; мусор/неизвестные значения → None."""
    if not isinstance(data, dict):
        return None
    condition = data.get("condition")
    if condition not in get_args(AlarmCondition):
        return None
    value = _json_float(data.get("value"))
    if value is None:
        return None
    raw_value2 = data.get("value2")
    value2 = _json_float(raw_value2) if raw_value2 is not None else None
    if raw_value2 is not None and value2 is None:
        return None
    color = data.get("color", "red")
    if color not in get_args(AlarmColor):
        return None
    try:
        return AlarmRule(
            condition=condition,
            value=value,
            value2=value2,
            color=color,
            log=bool(data.get("log", True)),
            sound=bool(data.get("sound", False)),
        )
    except ValueError:
        return None


def alarm_rules_from_json(data: object) -> list[AlarmRule]:
    """Разобрать список правил; битые элементы пропускаются."""
    if not isinstance(data, list):
        return []
    return [rule for item in data if (rule := alarm_rule_from_json(item)) is not None]


def diff_snapshots(old: list | None, new: list | None) -> bool:
    """Различаются ли два снимка RAW-значений одной строки.

    None — «нет данных» (строку ещё не читали): None против None различия
    нет, None против значений — есть. Сравнение по raw-спискам, а не по
    отформатированному тексту, чтобы смена формата отображения не давала
    ложных различий."""
    if old is None or new is None:
        return old is not new
    return old != new


def _clamp(x: float, lo: float, hi: float) -> float:
    return min(max(x, lo), hi)


# whitelist функций, доступных в выражениях (log — натуральный)
EXPRESSION_FUNCTIONS: dict[str, Callable[..., float]] = {
    "abs": abs,
    "sqrt": math.sqrt,
    "exp": math.exp,
    "log": math.log,
    "log2": math.log2,
    "log10": math.log10,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "floor": math.floor,
    "ceil": math.ceil,
    "round": round,
    "min": min,
    "max": max,
    "pow": pow,
    "clamp": _clamp,
}

EXPRESSION_CONSTANTS: dict[str, float] = {"pi": math.pi, "e": math.e}

# AST провалидирован, поэтому eval безопасен: __builtins__ нет, только
# whitelisted функции/константы, значения строк приходят через locals
_EXPRESSION_GLOBALS: dict[str, object] = {
    "__builtins__": {},
    **EXPRESSION_FUNCTIONS,
    **EXPRESSION_CONSTANTS,
}

_EXPRESSION_BIN_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
_EXPRESSION_UNARY_OPS = (ast.UAdd, ast.USub)


@dataclass(frozen=True)
class Expression:
    """Скомпилированное выражение над значениями строк регистров.

    text — исходная строка, deps — имена строк-зависимостей (ссылки [имя]),
    names — дополнительные голые имена (extra_names из parse_expression),
    реально использованные в выражении (в deps НЕ входят).
    evaluate(values, names=...): значения приводятся к float; refs ищутся
    в values, extra-имена — в names; отсутствующая зависимость — KeyError
    с её именем (семантика одинаковая для обоих маппингов). Математические
    ошибки (деление на 0, выход за область определения, переполнение,
    неверная арность функции) не бросаются, а дают float("nan").
    """

    text: str
    deps: frozenset[str]
    _code: CodeType = field(repr=False, compare=False)
    _refs: dict[str, str] = field(repr=False, compare=False)  # плейсхолдер -> имя строки
    names: frozenset[str] = frozenset()
    _functions: Mapping[str, Callable[..., float]] = field(
        default_factory=dict, repr=False, compare=False
    )

    def evaluate(
        self, values: Mapping[str, float], *, names: Mapping[str, float] | None = None
    ) -> float:
        local_vars = {
            placeholder: float(values[name]) for placeholder, name in self._refs.items()
        }
        if self.names:
            extra = names if names is not None else {}
            for name in self.names:
                local_vars[name] = float(extra[name])
        eval_globals: dict[str, object] = _EXPRESSION_GLOBALS
        if self._functions:
            eval_globals = {**_EXPRESSION_GLOBALS, **self._functions}
        try:
            result = eval(self._code, eval_globals, local_vars)
        except (ArithmeticError, ValueError, TypeError):
            return float("nan")
        return float(result)


def _extract_refs(text: str) -> tuple[str, list[str]]:
    """Заменить ссылки [имя] плейсхолдерами __ref_N; вернуть (текст, имена)."""
    out: list[str] = []
    names: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "[":
            end = text.find("]", i + 1)
            if end == -1:
                raise ValueError(f"Незакрытая скобка '[' в выражении: {text!r}")
            name = text[i + 1 : end]
            if not name.strip():
                raise ValueError(f"Пустая ссылка [] в выражении: {text!r}")
            out.append(f"__ref_{len(names)}")
            names.append(name)
            i = end + 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out), names


def _validate_expression_node(
    node: ast.AST,
    refs: dict[str, str],
    seen: set[str],
    functions: Mapping[str, Callable[..., float]],
    extra_names: frozenset[str],
    used_names: set[str],
) -> None:
    """Строгая проверка AST: только числа, refs, pi/e, арифметика и whitelisted вызовы."""
    if isinstance(node, ast.Expression):
        _validate_expression_node(node.body, refs, seen, functions, extra_names, used_names)
    elif isinstance(node, ast.BinOp):
        if not isinstance(node.op, _EXPRESSION_BIN_OPS):
            raise ValueError(f"Недопустимая операция {type(node.op).__name__} в выражении")
        _validate_expression_node(node.left, refs, seen, functions, extra_names, used_names)
        _validate_expression_node(node.right, refs, seen, functions, extra_names, used_names)
    elif isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _EXPRESSION_UNARY_OPS):
            raise ValueError(f"Недопустимая операция {type(node.op).__name__} в выражении")
        _validate_expression_node(node.operand, refs, seen, functions, extra_names, used_names)
    elif isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Вызов не-функции не поддерживается")
        func_id = node.func.id
        if func_id in refs:
            raise ValueError(f"Ссылка [{refs[func_id]}] не является функцией")
        if func_id not in functions:
            allowed = ", ".join(sorted(functions))
            raise ValueError(f"Неизвестная функция {func_id!r} (доступны: {allowed})")
        if node.keywords:
            raise ValueError("Именованные аргументы не поддерживаются")
        for arg in node.args:
            _validate_expression_node(arg, refs, seen, functions, extra_names, used_names)
    elif isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, int | float):
            raise ValueError(f"Недопустимый литерал {node.value!r}: только числа")
    elif isinstance(node, ast.Name):
        if node.id in refs:
            # каждый плейсхолдер встречается в подставленном тексте ровно раз;
            # повтор — значит, пользователь сам написал __ref_N
            if node.id in seen:
                raise ValueError(f"Недопустимое имя {node.id!r} в выражении")
            seen.add(node.id)
        elif node.id in extra_names:
            used_names.add(node.id)
        elif node.id not in EXPRESSION_CONSTANTS:
            raise ValueError(
                f"Неизвестное имя {node.id!r}: ссылки на строки пишутся как [имя], "
                "константы — pi и e"
            )
    else:
        raise ValueError(f"Недопустимая конструкция {type(node).__name__} в выражении")


def _check_extra_name(name: str, kind: str, taken: Mapping[str, object]) -> None:
    """Проверить доп. имя (функция/переменная): идентификатор без конфликтов."""
    if not name.isidentifier() or name.startswith("__ref_"):
        raise ValueError(f"Недопустимое имя {kind} {name!r}")
    if name in EXPRESSION_FUNCTIONS or name in EXPRESSION_CONSTANTS or name in taken:
        raise ValueError(f"Имя {kind} {name!r} конфликтует с существующим")


def parse_expression(
    text: str,
    *,
    extra_functions: Mapping[str, Callable[..., float]] | None = None,
    extra_names: Iterable[str] | None = None,
) -> Expression:
    """Разобрать выражение вида ([temperature] + [flow rate]) / 2.

    Ссылки на строки — [имя] (имена могут содержать пробелы и юникод).
    Допускаются + - * / // % **, унарные +/-, скобки, числа (в т.ч. 1e3),
    константы pi/e и функции из EXPRESSION_FUNCTIONS. Всё остальное
    (атрибуты, подзапросы, присваивания, лямбды, произвольные имена) —
    ValueError с читаемым сообщением.

    extra_functions — дополнительные разрешённые функции поверх
    EXPRESSION_FUNCTIONS (глобальный whitelist не меняется); конфликт имён
    с существующими функциями/константами — ValueError. extra_names —
    дополнительные разрешённые голые имена (значения подаются в evaluate
    отдельным маппингом names=); в deps не входят.
    """
    functions: dict[str, Callable[..., float]] = dict(extra_functions or {})
    for func_name in functions:
        _check_extra_name(func_name, "функции", {})
    names_allowed = frozenset(extra_names or ())
    for extra_name in names_allowed:
        _check_extra_name(extra_name, "переменной", functions)
    substituted, ref_names = _extract_refs(text)
    try:
        tree = ast.parse(substituted, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Некорректный синтаксис выражения: {text!r}") from exc
    refs = {f"__ref_{i}": name for i, name in enumerate(ref_names)}
    used_names: set[str] = set()
    all_functions: dict[str, Callable[..., float]] = {**EXPRESSION_FUNCTIONS, **functions}
    _validate_expression_node(tree, refs, set(), all_functions, names_allowed, used_names)
    return Expression(
        text=text,
        deps=frozenset(ref_names),
        _code=compile(tree, "<expression>", "eval"),
        _refs=refs,
        names=frozenset(used_names),
        _functions=functions,
    )
