import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Qt needs system libraries (libEGL etc.) that headless Linux CI runners lack;
# skip the whole module there — it still runs on macOS/Windows CI and dev machines.
try:
    from PySide6.QtWidgets import QApplication, QSizePolicy  # noqa: E402
except ImportError as exc:
    pytest.skip(f"Qt system libraries not available: {exc}", allow_module_level=True)

from modbus_connector.session_widget import SessionWidget  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _destroy_sessions(qapp: QApplication) -> Iterator[None]:
    yield
    # a leaked session's table can crash a later app-wide stylesheet switch
    # (QTableView.updateEditorGeometries); destroy sessions after each test
    # (shutdown() is idempotent)
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, SessionWidget):
            widget.shutdown()
            widget.close()
            widget.deleteLater()
    qapp.processEvents()


def test_state_roundtrip_and_shutdown(qapp: QApplication) -> None:
    session = SessionWidget()
    session.set_state(
        {
            "connection": {"type": "TCP", "tcp_host": "10.0.0.1", "tcp_port": 1502,
                           "unit": 7},
            "registers": [
                {"name": "temp", "kind": "holding_registers", "address": 5, "count": 2,
                 "format": "f32", "unit_id": "3", "poll_ms": "5000"}
            ],
            "scanner": {"start": 5, "end": 9},
        }
    )
    collected = session.state()
    connection = collected["connection"]
    assert connection["tcp_host"] == "10.0.0.1"
    assert connection["tcp_port"] == 1502
    assert connection["unit"] == 7
    row = collected["registers"][0]
    assert row["name"] == "temp"
    assert row["format"] == "f32"
    assert row["unit_id"] == "3"
    assert row["poll_ms"] == "5000"
    assert collected["scanner"]["start"] == 5
    assert collected["scanner"]["end"] == 9
    session.shutdown()  # must stop the worker thread without hanging


def test_registers_options_roundtrip(qapp: QApplication) -> None:
    session = SessionWidget()
    session.set_state({"registers_options": {"order": "CDAB"}})
    assert session.state()["registers_options"]["order"] == "CDAB"
    session.set_state({"registers_options": {"order": "junk"}})  # invalid: keep
    assert session.state()["registers_options"]["order"] == "CDAB"
    session.set_state({"registers": []})  # missing key: keep current value
    assert session.state()["registers_options"]["order"] == "CDAB"
    session.shutdown()


def test_column_widths_roundtrip(qapp: QApplication) -> None:
    session = SessionWidget()
    session.registers_panel._table.setColumnWidth(0, 222)
    widths = session.state()["registers_options"]["column_widths"]
    assert widths[0] == 222
    assert len(widths) == session.registers_panel._table.columnCount()

    fresh = SessionWidget()
    fresh.set_state({"registers_options": {"column_widths": widths}})
    header = fresh.registers_panel._table.horizontalHeader()
    assert header.sectionSize(0) == 222
    assert header.sectionSize(1) == widths[1]
    session.shutdown()
    fresh.shutdown()


def test_logging_settings_roundtrip(qapp: QApplication) -> None:
    session = SessionWidget()
    session.set_state(
        {"logging": {"path": "/tmp/x.jsonl", "format": "jsonl",
                     "fields": ["name"], "append": False}}
    )
    assert session.state()["logging"] == {"path": "/tmp/x.jsonl", "format": "jsonl",
                                          "fields": ["name"], "append": False}
    session.set_state({"registers": []})  # missing key: keep current value
    assert session.state()["logging"]["format"] == "jsonl"
    session.shutdown()


def test_status_label_never_widens_the_window(qapp: QApplication) -> None:
    session = SessionWidget()
    policy = session.connection_panel._status.sizePolicy()
    assert policy.horizontalPolicy() == QSizePolicy.Policy.Ignored
    session.shutdown()


def test_bus_controls_follow_connection(qapp: QApplication, tmp_path) -> None:
    session = SessionWidget()
    panel = session.registers_panel
    scanner = session.scanner_panel
    assert not panel._read_all_button.isEnabled()  # starts disconnected
    assert not scanner._start_button.isEnabled()
    assert not scanner._addr_start_button.isEnabled()

    session._show_graph()
    graph = session._graph_window
    assert graph is not None
    assert not graph._poll_button.isEnabled()

    session._on_connection_changed(True, "Connected")  # worker signal path
    assert panel._read_all_button.isEnabled()
    assert scanner._start_button.isEnabled()
    assert scanner._addr_start_button.isEnabled()
    assert graph._poll_button.isEnabled()

    # polling + logging active when the connection drops: both must stop
    panel.set_logging_state({"path": str(tmp_path / "bus.csv")})
    panel.start_logging()
    assert panel.is_polling() and panel.is_logging()
    session._on_connection_changed(False, "Disconnected")
    assert not panel.is_polling() and not panel.is_logging()
    assert not panel._poll_button.isEnabled()
    assert not scanner._start_button.isEnabled()
    assert not graph._poll_button.isEnabled()
    session.shutdown()
    # destroy the widget tree (it holds a GraphWindow) so no dangling wrapper
    # survives into later palette/layout passes — see tests/test_graph_window
    session.deleteLater()
    qapp.processEvents()


def test_slave_mode_state_roundtrip(qapp: QApplication) -> None:
    session = SessionWidget()
    assert session.state()["mode"] == "master"  # default / backward compat
    session.set_state(
        {
            "mode": "slave",
            "sim": {
                "server": {"type": "TCP", "host": "0.0.0.0", "port": 1600, "unit": 3},
                "rows": [
                    {"name": "x", "kind": "coils", "address": 0, "count": 1,
                     "values": [True]}
                ],
            },
        }
    )
    collected = session.state()
    assert collected["mode"] == "slave"
    assert collected["sim"]["server"]["port"] == 1600
    assert collected["sim"]["server"]["unit"] == 3
    assert collected["sim"]["rows"][0]["values"] == [True]
    # master-часть state на месте
    assert "connection" in collected and "registers" in collected
    session.set_state({"mode": "junk"})  # неизвестный режим игнорируется
    assert session.state()["mode"] == "slave"
    session.shutdown()


def test_slave_mode_visibility(qapp: QApplication) -> None:
    session = SessionWidget()
    assert session._center_stack.currentWidget() is session.registers_panel
    session.set_state({"mode": "slave"})
    assert session._center_stack.currentWidget() is session.sim_panel
    assert session.connection_panel.isHidden()
    assert session._scanner_button.isHidden()
    assert session._graph_button.isHidden()
    session.set_state({"mode": "master"})
    assert session._center_stack.currentWidget() is session.registers_panel
    assert not session.connection_panel.isHidden()
    assert not session._scanner_button.isHidden()
    session.shutdown()


def test_mode_combo_locking(qapp: QApplication) -> None:
    session = SessionWidget()
    combo = session._mode_combo
    assert combo.isEnabled()
    # master подключён — режим менять нельзя
    session._on_connection_changed(True, "Connected (tcp 127.0.0.1:502)")
    assert not combo.isEnabled()
    session._on_connection_changed(False, "Disconnected")
    assert combo.isEnabled()
    # sim-сервер запущен — тоже нельзя
    session.set_state({"mode": "slave"})
    session._on_sim_server_changed(True, "Simulator running (tcp 127.0.0.1:1502)")
    assert not combo.isEnabled()
    assert session.title() == "Simulator"  # panel не знает params — общий ключ
    session._on_sim_server_changed(False, "Stopped")
    assert combo.isEnabled()
    session.shutdown()


def test_slave_title_follows_server(qapp: QApplication) -> None:
    session = SessionWidget()
    session.set_state({"mode": "slave"})
    assert session.title() == "Simulator"
    # запуск из панели: params известны → заголовок с описанием сервера;
    # startRequested отключён от worker'а, чтобы не поднять реальный сервер
    session.sim_panel.startRequested.disconnect()
    session.sim_panel._button.click()
    session.sim_panel.set_running(True, "Simulator running (tcp 127.0.0.1:1502)")
    session._on_sim_server_changed(True, "Simulator running (tcp 127.0.0.1:1502)")
    assert session.title() == "sim tcp 127.0.0.1:1502"
    session.sim_panel.set_running(False, "Stopped")
    session._on_sim_server_changed(False, "Stopped")
    assert session.title() == "Simulator"
    session.shutdown()


def test_shutdown_in_slave_mode(qapp: QApplication) -> None:
    session = SessionWidget()
    session.set_state({"mode": "slave"})
    session.shutdown()  # оба потока останавливаются без зависания
    session.shutdown()  # идемпотентно


def test_sniffer_mode_state_roundtrip(qapp: QApplication) -> None:
    session = SessionWidget()
    session.set_state(
        {
            "mode": "sniffer",
            "sniffer": {
                "params": {"port": "/dev/ttyFAKE0", "baudrate": 19200, "parity": "E"},
                "units": [
                    {"unit": 3, "rows": [
                        {"address": 5, "kind": "holding_registers", "name": "temp",
                         "format": "f32", "value": [100, 200]},
                    ]},
                ],
            },
        }
    )
    collected = session.state()
    assert collected["mode"] == "sniffer"
    sniffer = collected["sniffer"]
    assert sniffer["params"]["port"] == "/dev/ttyFAKE0"
    assert sniffer["params"]["baudrate"] == 19200
    assert sniffer["params"]["parity"] == "E"
    assert sniffer["units"][0]["unit"] == 3
    assert sniffer["units"][0]["rows"][0]["value"] == [100, 200]
    # master/slave-части state на месте
    assert "connection" in collected and "sim" in collected
    session.shutdown()


def test_sniffer_mode_visibility(qapp: QApplication) -> None:
    session = SessionWidget()
    assert session._center_stack.currentWidget() is session.registers_panel
    session.set_state({"mode": "sniffer"})
    assert session._center_stack.currentWidget() is session.sniffer_panel
    assert session.connection_panel.isHidden()
    assert session._scanner_button.isHidden()
    assert session._graph_button.isHidden()
    session.set_state({"mode": "master"})
    assert session._center_stack.currentWidget() is session.registers_panel
    assert not session.connection_panel.isHidden()
    assert not session._scanner_button.isHidden()
    session.shutdown()


def test_sniffer_mode_lock_and_title(qapp: QApplication) -> None:
    session = SessionWidget()
    session.set_state({"mode": "sniffer"})
    assert session._mode_combo.isEnabled()
    assert session.title() == "Sniffer"  # panel не знает params — общий ключ
    # активный сниффинг — режим менять нельзя, заголовок — описание порта
    session.sniffer_panel.set_state({"params": {"port": "/dev/ttyFAKE0"}})
    session.sniffer_panel.startRequested.disconnect()  # не поднимать реальный порт
    session.sniffer_panel._button.click()
    session.sniffer_panel.set_sniffing(True, "Listening (sniff rtu /dev/ttyFAKE0 @ 9600)")
    session._on_sniffing_changed(True, "Listening (sniff rtu /dev/ttyFAKE0 @ 9600)")
    assert not session._mode_combo.isEnabled()
    assert session.title() == "sniff rtu /dev/ttyFAKE0 @ 9600"
    session.sniffer_panel.set_sniffing(False, "Stopped")
    session._on_sniffing_changed(False, "Stopped")
    assert session._mode_combo.isEnabled()
    assert session.title() == "Sniffer"
    session.shutdown()


def test_shutdown_in_sniffer_mode(qapp: QApplication) -> None:
    session = SessionWidget()
    session.set_state({"mode": "sniffer"})
    session.shutdown()  # все три потока останавливаются без зависания
    session.shutdown()  # идемпотентно
