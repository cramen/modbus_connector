import csv
import json
from pathlib import Path

from modbus_connector.datalogger import DataLogger, LogSample, LogSettings


def _sample(**overrides: object) -> LogSample:
    values: dict[str, object] = {
        "timestamp": "2026-08-13T12:00:00.123",
        "name": "temperature",
        "address": 0,
        "kind": "holding_registers",
        "value": "-39.9",
    }
    values.update(overrides)
    return LogSample(**values)  # type: ignore[arg-type]


def _read_csv(path: Path) -> list[list[str]]:
    return list(csv.reader(path.read_text(encoding="utf-8").splitlines()))


def test_csv_writes_header_and_rows(tmp_path: Path) -> None:
    path = tmp_path / "log.csv"
    logger = DataLogger()
    logger.open(LogSettings(path=str(path)))
    logger.write(_sample())
    logger.write(_sample(value="1.2"))
    logger.close()

    rows = _read_csv(path)
    assert rows[0] == ["timestamp", "name", "address", "kind", "value"]
    assert rows[1] == ["2026-08-13T12:00:00.123", "temperature", "0",
                       "holding_registers", "-39.9"]
    assert rows[2][-1] == "1.2"
    assert logger.rows_written == 2


def test_csv_field_subset_drops_columns(tmp_path: Path) -> None:
    path = tmp_path / "log.csv"
    logger = DataLogger()
    logger.open(LogSettings(path=str(path), fields=frozenset({"timestamp"})))
    logger.write(_sample())
    logger.close()

    rows = _read_csv(path)
    assert rows[0] == ["timestamp", "value"]
    assert rows[1] == ["2026-08-13T12:00:00.123", "-39.9"]


def test_csv_quotes_values_with_commas(tmp_path: Path) -> None:
    path = tmp_path / "log.csv"
    logger = DataLogger()
    logger.open(LogSettings(path=str(path), fields=frozenset()))
    logger.write(_sample(value="0x0001, 0x0002"))  # a hex row, commas inside
    logger.close()

    rows = _read_csv(path)
    assert rows == [["value"], ["0x0001, 0x0002"]]


def test_jsonl_writes_exactly_enabled_keys_plus_value(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    logger = DataLogger()
    logger.open(
        LogSettings(path=str(path), format="jsonl", fields=frozenset({"name", "kind"}))
    )
    logger.write(_sample())
    logger.close()

    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    assert record == {"name": "temperature", "kind": "holding_registers",
                      "value": "-39.9"}
    assert len(lines) == 1  # no header line in jsonl


def test_append_keeps_content_and_skips_header(tmp_path: Path) -> None:
    path = tmp_path / "log.csv"
    logger = DataLogger()
    logger.open(LogSettings(path=str(path), fields=frozenset({"name"})))
    logger.write(_sample())
    logger.close()

    logger.open(LogSettings(path=str(path), fields=frozenset({"name"})))  # append
    logger.write(_sample(value="8.25"))
    logger.close()

    rows = _read_csv(path)
    assert rows == [["name", "value"], ["temperature", "-39.9"],
                    ["temperature", "8.25"]]


def test_overwrite_truncates_and_writes_header(tmp_path: Path) -> None:
    path = tmp_path / "log.csv"
    logger = DataLogger()
    logger.open(LogSettings(path=str(path), fields=frozenset({"name"})))
    logger.write(_sample())
    logger.close()

    logger.open(LogSettings(path=str(path), fields=frozenset({"name"}), append=False))
    logger.write(_sample(value="8.25"))
    logger.close()

    rows = _read_csv(path)
    assert rows == [["name", "value"], ["temperature", "8.25"]]


def test_flush_and_close_are_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "log.csv"
    logger = DataLogger()
    logger.flush()  # no-op before open
    logger.close()  # no-op before open
    logger.open(LogSettings(path=str(path)))
    logger.write(_sample())
    logger.flush()
    logger.close()
    logger.flush()  # no-op after close
    logger.close()  # no-op after close
    logger.write(_sample())  # dropped, not an error
    assert logger.rows_written == 1
    assert not logger.is_open
    assert len(_read_csv(path)) == 2
