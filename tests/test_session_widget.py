import os

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
    assert session.state()["registers_options"] == {"order": "CDAB"}
    session.set_state({"registers_options": {"order": "junk"}})  # invalid: keep
    assert session.state()["registers_options"] == {"order": "CDAB"}
    session.set_state({"registers": []})  # missing key: keep current value
    assert session.state()["registers_options"] == {"order": "CDAB"}
    session.shutdown()


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
