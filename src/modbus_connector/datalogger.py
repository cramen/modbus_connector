"""Фоновое логирование опрошенных значений в файл (CSV / JSON Lines); без Qt."""

import csv
import json
from dataclasses import dataclass, field
from typing import IO, Literal, get_args

LogFormat = Literal["csv", "jsonl"]
LogField = Literal["timestamp", "name", "address", "kind"]
LOG_FIELDS: tuple[LogField, ...] = get_args(LogField)


@dataclass
class LogSettings:
    path: str = ""
    format: LogFormat = "csv"
    fields: frozenset[LogField] = field(default_factory=lambda: frozenset(LOG_FIELDS))
    append: bool = True


@dataclass
class LogSample:
    timestamp: str  # wall clock ISO 8601 with milliseconds, from the caller
    name: str
    address: int
    kind: str
    value: str


class DataLogger:
    """Пишет LogSample в файл: CSV (строки через csv-модуль) или JSON Lines
    (по объекту на строку — удобно читать потоково и дописывать в конец)."""

    def __init__(self) -> None:
        self._file: IO[str] | None = None
        self._writer: csv.writer | None = None  # type: ignore[type-arg]
        self._settings: LogSettings | None = None
        self.rows_written = 0

    @property
    def is_open(self) -> bool:
        return self._file is not None

    def _columns(self) -> list[LogField]:
        assert self._settings is not None
        return [f for f in LOG_FIELDS if f in self._settings.fields]

    def open(self, settings: LogSettings) -> None:
        """Открыть файл (OSError пробрасывается вызывающему)."""
        file = open(  # noqa: SIM115  # closed explicitly in close()
            settings.path, "a" if settings.append else "w", newline="", encoding="utf-8"
        )
        self._file = file
        self._settings = settings
        self.rows_written = 0
        if settings.format == "csv":
            self._writer = csv.writer(file, lineterminator="\n")
            if file.tell() == 0:  # a new/empty file gets a header, appends don't
                self._writer.writerow([*self._columns(), "value"])

    def write(self, sample: LogSample) -> None:
        if self._file is None or self._settings is None:
            return
        columns = self._columns()
        if self._settings.format == "csv":
            assert self._writer is not None
            self._writer.writerow([getattr(sample, f) for f in columns] + [sample.value])
        else:
            record = {f: getattr(sample, f) for f in columns}
            record["value"] = sample.value
            self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.rows_written += 1

    def flush(self) -> None:
        if self._file is not None:
            self._file.flush()

    def close(self) -> None:
        if self._file is None:
            return
        self._file.close()
        self._file = None
        self._writer = None
