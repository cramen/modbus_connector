"""Тесты консольной утилиты cli.py: read/write/poll/scan/simulate/gateway/sniff.

Без Qt: отдельный тест проверяет, что вызов main() не импортирует PySide6.
Потоковые команды запускаются в потоке с подменённым cli._wait_until_interrupt
(пауза + KeyboardInterrupt — симуляция Ctrl+C), у poll подменяется cli._sleep.
"""

import json
import socket
import sys
import threading
import time
from typing import Any

import pytest

from modbus_connector import cli
from modbus_connector.backend import ModbusBackend
from modbus_connector.models import TcpParams
from modbus_connector.sniffer_backend import RtuFrameParser, crc16


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _tcp(port: int) -> list[str]:
    return ["--tcp", f"127.0.0.1:{port}"]


def _stdout_json(capsys: pytest.CaptureFixture[str]) -> Any:
    return json.loads(capsys.readouterr().out)


def _stdout_lines(capsys: pytest.CaptureFixture[str]) -> list[Any]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]


def _interrupt_after(monkeypatch: pytest.MonkeyPatch, seconds: float) -> None:
    """Подменить ожидание Ctrl+C: реальная пауза, затем KeyboardInterrupt."""

    def fake_wait() -> None:
        time.sleep(seconds)
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_wait_until_interrupt", fake_wait)


def _run_in_thread(argv: list[str]) -> tuple[threading.Thread, list[int]]:
    codes: list[int] = []
    thread = threading.Thread(target=lambda: codes.append(cli.main(argv)), daemon=True)
    thread.start()
    return thread, codes


def _connect_with_retry(backend: ModbusBackend, port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            backend.connect(TcpParams("127.0.0.1", port, timeout=0.5))
            return
        except ConnectionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)


def test_read_all_areas(modbus_server: int, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["read", "hr", "0", "3", *_tcp(modbus_server)]) == 0
    data = _stdout_json(capsys)
    assert data == {
        "unit": 1,
        "kind": "holding_registers",
        "address": 0,
        "count": 3,
        "raw": [100, 101, 102],
        "values": [100, 101, 102],
    }
    assert cli.main(["read", "ir", "0", "2", *_tcp(modbus_server)]) == 0
    assert _stdout_json(capsys)["raw"] == [7, 8]
    assert cli.main(["read", "coils", "0", "4", *_tcp(modbus_server)]) == 0
    assert _stdout_json(capsys)["raw"] == [True, False, True, False]
    assert cli.main(["read", "di", "0", "4", *_tcp(modbus_server)]) == 0
    assert _stdout_json(capsys)["raw"] == [False, True, False, True]


def test_read_full_kind_names_and_formats(
    modbus_server: int, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["read", "holding_registers", "0", "2", *_tcp(modbus_server)]) == 0
    assert _stdout_json(capsys)["kind"] == "holding_registers"
    assert cli.main(["read", "hr", "0", "2", "--format", "hex", *_tcp(modbus_server)]) == 0
    assert _stdout_json(capsys)["values"] == "0x0064, 0x0065"
    assert cli.main(
        ["read", "hr", "0", "2", "--scale", "0.1", *_tcp(modbus_server)]
    ) == 0
    assert _stdout_json(capsys)["values"] == pytest.approx([10.0, 10.1])
    assert cli.main(["read", "hr", "0", "2", "--format", "f32", *_tcp(modbus_server)]) == 0
    values = _stdout_json(capsys)["values"]
    assert len(values) == 1 and isinstance(values[0], float)


def test_read_text_mode(modbus_server: int, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["read", "hr", "0", "2", "--text", *_tcp(modbus_server)]) == 0
    assert capsys.readouterr().out.strip() == "100, 101"


def test_write_coil_and_register(modbus_server: int, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["write", "coils", "0", "false", *_tcp(modbus_server)]) == 0
    confirm = _stdout_json(capsys)
    assert confirm["written"] == 1 and confirm["kind"] == "coils"
    assert cli.main(["read", "coils", "0", "1", *_tcp(modbus_server)]) == 0
    assert _stdout_json(capsys)["raw"] == [False]
    assert cli.main(["write", "hr", "5", "0x1234", *_tcp(modbus_server)]) == 0
    assert _stdout_json(capsys)["written"] == 1
    assert cli.main(["read", "hr", "5", "1", *_tcp(modbus_server)]) == 0
    assert _stdout_json(capsys)["raw"] == [0x1234]


def test_read_out_of_map_is_modbus_exception(
    modbus_server: int, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["read", "hr", "50", "1", *_tcp(modbus_server)]) == 3
    err = capsys.readouterr().err
    assert "0x02" in err and "Illegal Data Address" in err


def test_unreachable_target_is_connection_error(capsys: pytest.CaptureFixture[str]) -> None:
    port = _free_port()  # порт свободен — соединение будет отклонено
    assert cli.main(["read", "hr", "0", "1", "--tcp", f"127.0.0.1:{port}",
                     "--timeout", "0.5"]) == 2
    assert "error:" in capsys.readouterr().err


def test_bad_arguments_exit_4(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:  # нет COUNT
        cli.main(["read", "hr", "0"])
    assert exc.value.code == 4
    with pytest.raises(SystemExit) as exc:  # неизвестная область
        cli.main(["read", "wat", "0", "1", "--tcp", "127.0.0.1"])
    assert exc.value.code == 4
    with pytest.raises(SystemExit) as exc:  # нет транспорта
        cli.main(["read", "hr", "0", "1"])
    assert exc.value.code == 4
    with pytest.raises(SystemExit) as exc:  # два транспорта сразу
        cli.main(["read", "hr", "0", "1", "--tcp", "127.0.0.1", "--rtu", "/dev/x"])
    assert exc.value.code == 4
    # запись во входные регистры — ошибка аргументов (без подключения)
    assert cli.main(["write", "ir", "0", "1", "--tcp", "127.0.0.1:1"]) == 4
    # несуществующий map-файл
    assert cli.main(["poll", "--map", "/nonexistent/map.json", "--tcp", "127.0.0.1:1"]) == 4
    # битый listen-spec шлюза
    assert cli.main(["gateway", "--listen", "bogus", "--target", "tcp:127.0.0.1:1"]) == 4
    capsys.readouterr()


def test_poll_until_ctrl_c(
    modbus_server: int, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_sleep(seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_sleep", fake_sleep)
    argv = ["poll", "hr", "0", "2", "--interval", "100", *_tcp(modbus_server)]
    assert cli.main(argv) == 0
    lines = _stdout_lines(capsys)
    assert len(lines) == 1
    assert lines[0]["raw"] == [100, 101]
    assert lines[0]["kind"] == "holding_registers"
    assert "timestamp" in lines[0]


def test_poll_with_map_and_log(
    modbus_server: int,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    map_file = tmp_path / "map.csv"
    map_file.write_text(
        "name,kind,address,count,format\n"
        "temperature,holding_registers,0,2,dec\n"
        "flags,coils,0,4,dec\n"
    )
    log_file = tmp_path / "values.csv"
    monkeypatch.setattr(
        cli, "_sleep", lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt)
    )
    argv = [
        "poll", "--map", str(map_file), "--interval", "100",
        "--log", str(log_file), *_tcp(modbus_server),
    ]
    assert cli.main(argv) == 0
    lines = _stdout_lines(capsys)
    assert [line["name"] for line in lines] == ["temperature", "flags"]
    assert lines[0]["raw"] == [100, 101]
    assert lines[1]["raw"] == [True, False, True, False]
    log_lines = log_file.read_text().splitlines()  # файл корректно закрыт
    assert log_lines[0] == "timestamp,name,address,kind,value"
    assert len(log_lines) == 3
    assert "temperature" in log_lines[1]


def test_scan_units_finds_fixture_unit(
    modbus_server: int, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["scan", "units", "1-2", "--timeout", "0.3", *_tcp(modbus_server)]
    assert cli.main(argv) == 0
    data = _stdout_json(capsys)
    assert [hit["unit"] for hit in data["hits"]] == [1]
    assert data["hits"][0]["probes"]


def test_scan_addresses(modbus_server: int, capsys: pytest.CaptureFixture[str]) -> None:
    argv = ["scan", "addresses", "hr", "0-2", *_tcp(modbus_server)]
    assert cli.main(argv) == 0
    data = _stdout_json(capsys)
    assert [hit["address"] for hit in data["hits"]] == [0, 1, 2]
    assert data["hits"][0]["values"] == [100, 101]


def test_simulate(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    map_file = tmp_path / "map.json"
    map_file.write_text(
        json.dumps({"registers": [{"name": "setpoint", "address": 0, "count": 1,
                                   "values": [42]}]})
    )
    port = _free_port()
    _interrupt_after(monkeypatch, 2.5)
    thread, codes = _run_in_thread(["simulate", "--map", str(map_file), "--tcp", str(port)])
    backend = ModbusBackend()
    try:
        _connect_with_retry(backend, port)
        assert backend.read(1, "holding_registers", 0, 1) == [42]  # начальные из map
        backend.write(1, "coils", 0, [True])
    finally:
        backend.disconnect()
    thread.join(timeout=10)
    assert codes == [0]
    writes = [line for line in _stdout_lines(capsys) if line.get("event") == "write"]
    assert any(
        w["kind"] == "coils" and w["address"] == 0 and w["values"] == [True] for w in writes
    )


def test_gateway_passthrough(
    modbus_server: int, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    port = _free_port()
    _interrupt_after(monkeypatch, 2.5)
    argv = [
        "gateway", "--listen", f"tcp:127.0.0.1:{port}",
        "--target", f"tcp:127.0.0.1:{modbus_server}",
    ]
    thread, codes = _run_in_thread(argv)
    backend = ModbusBackend()
    try:
        _connect_with_retry(backend, port)
        assert backend.read(1, "holding_registers", 0, 3) == [100, 101, 102]
        backend.write(1, "holding_registers", 5, [555])
        assert backend.read(1, "holding_registers", 5, 1) == [555]
    finally:
        backend.disconnect()
    thread.join(timeout=10)
    assert codes == [0]
    lines = _stdout_lines(capsys)
    assert any(
        line.get("event") == "request" and "read holding_registers@0" in line["line"]
        for line in lines
    )


def test_gateway_dead_target(capsys: pytest.CaptureFixture[str]) -> None:
    argv = [
        "gateway", "--listen", f"tcp:{_free_port()}",
        "--target", f"tcp:127.0.0.1:{_free_port()}", "--timeout", "0.5",
    ]
    assert cli.main(argv) == 2
    assert "error:" in capsys.readouterr().err


class _FakeSniffer:
    """Замена SnifferBackend: start() сразу отдаёт синтетические кадр и значения."""

    def __init__(self) -> None:
        self.on_frame: Any = None
        self.on_values: Any = None
        self.on_error: Any = None

    def start(self, params: Any) -> None:
        self.on_frame("→ read holding_registers unit=1 @0 x2")
        self.on_values(1, "holding_registers", 0, [100, 101])

    def stop(self) -> None:
        pass


def test_sniff_events(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "SnifferBackend", _FakeSniffer)
    _interrupt_after(monkeypatch, 0.2)
    assert cli.main(["sniff", "--rtu", "FAKE-PORT"]) == 0
    lines = _stdout_lines(capsys)
    assert lines[0]["event"] == "frame"
    assert lines[1] == {
        "timestamp": lines[1]["timestamp"],
        "event": "values",
        "unit": 1,
        "kind": "holding_registers",
        "address": 0,
        "values": [100, 101],
    }


def test_rtu_frame_parser_decodes_request() -> None:
    payload = bytes([1, 3, 0, 0, 0, 2])
    frame_bytes = payload + crc16(payload).to_bytes(2, "little")
    frames = RtuFrameParser().feed(frame_bytes)
    assert len(frames) == 1
    frame = frames[0]
    assert frame.direction == "tx"
    assert frame.function_code == 3
    assert frame.address == 0 and frame.count == 2


@pytest.mark.parametrize(
    "argv",
    [
        ["--help"],
        ["read", "--help"],
        ["write", "--help"],
        ["poll", "--help"],
        ["scan", "--help"],
        ["scan", "units", "--help"],
        ["scan", "addresses", "--help"],
        ["simulate", "--help"],
        ["gateway", "--help"],
        ["sniff", "--help"],
    ],
)
def test_help_exits_zero(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip()


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "modbus-connector-cli" in capsys.readouterr().out


def test_cli_does_not_import_qt(
    modbus_server: int, capsys: pytest.CaptureFixture[str]
) -> None:
    before = set(sys.modules)
    assert cli.main(["read", "hr", "0", "1", *_tcp(modbus_server)]) == 0
    capsys.readouterr()
    new_modules = set(sys.modules) - before
    assert not [m for m in new_modules if m.startswith(("PySide", "PyQt", "shiboken"))]
