import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Qt needs system libraries (libEGL etc.) that headless Linux CI runners lack;
# skip the whole module there — it still runs on macOS/Windows CI and dev machines.
try:
    from PySide6.QtTest import QSignalSpy  # noqa: E402
    from PySide6.QtWidgets import QApplication  # noqa: E402
except ImportError as exc:
    pytest.skip(f"Qt system libraries not available: {exc}", allow_module_level=True)

from modbus_connector import i18n  # noqa: E402
from modbus_connector.gateway_backend import (  # noqa: E402
    GatewayRtuOverTcpListenParams,
    GatewayTcpListenParams,
)
from modbus_connector.gateway_panel import GatewayPanel, parse_units  # noqa: E402
from modbus_connector.models import RtuParams, TcpParams  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def panel(qapp: QApplication) -> Iterator[GatewayPanel]:
    widget = GatewayPanel()
    yield widget
    widget.deleteLater()
    qapp.processEvents()


# --- парсер фильтра unit'ов ---


def test_parse_units_basic() -> None:
    assert parse_units("1, 5, 10-20") == {1, 5, *range(10, 21)}
    assert parse_units("") is None
    assert parse_units("   ") is None
    assert parse_units("10-12") == {10, 11, 12}
    assert parse_units("5-5") == {5}
    assert parse_units("1;2") == {1, 2}


def test_parse_units_tolerant() -> None:
    assert parse_units("junk") == set()  # мусор игнорируется
    assert parse_units("1, x, 3") == {1, 3}
    assert parse_units("0, 248, 5") == {5}  # вне 1..247 отбрасываются
    assert parse_units("20-10") == set()  # перевёрнутый диапазон
    assert parse_units("-5") == set()
    assert parse_units("1-2-3") == set()
    assert parse_units("245-999") == {245, 246, 247}  # обрезка до 247


# --- состояние ---


def test_state_roundtrip(panel: GatewayPanel) -> None:
    panel.set_state(
        {
            "listen": {"type": "RTU over TCP", "tcp_host": "0.0.0.0", "tcp_port": 5020},
            "target": {
                "type": "RTU",
                "rtu_port": "/dev/ttyFAKE0",
                "rtu_baud": "19200",
                "rtu_parity": "E",
                "timeout": 5.0,
            },
            "units": "1, 5",
        }
    )
    collected = panel.state()
    assert collected["listen"]["type"] == "RTU over TCP"
    assert collected["listen"]["tcp_port"] == 5020
    target = collected["target"]
    assert target["type"] == "RTU"
    assert target["rtu_port"] == "/dev/ttyFAKE0"
    assert target["rtu_baud"] == "19200"
    assert target["rtu_parity"] == "E"
    assert target["timeout"] == 5.0
    assert collected["units"] == "1, 5"


def test_set_state_tolerates_garbage(panel: GatewayPanel) -> None:
    panel.set_state("junk")  # type: ignore[arg-type]
    panel.set_state({"listen": "junk", "target": None})
    panel.set_state(
        {"listen": {"type": "junk", "tcp_port": "x", "rtu_bytesize": "junk"},
         "units": 5}
    )
    collected = panel.state()
    assert collected["listen"]["type"] == "TCP"  # дефолт не изменился
    assert collected["listen"]["tcp_port"] == 1502
    assert collected["units"] == "5"  # нестроковое значение приводится к строке


# --- старт/стоп ---


def test_start_emits_params(panel: GatewayPanel) -> None:
    spy = QSignalSpy(panel.startRequested)
    panel._button.click()
    assert spy.count() == 1
    listen, target, units = spy.at(0)
    assert isinstance(listen, GatewayTcpListenParams)
    assert (listen.host, listen.port) == ("0.0.0.0", 1502)
    assert isinstance(target, TcpParams)
    assert (target.host, target.port) == ("127.0.0.1", 502)
    assert units is None  # пустое поле — все unit 1..247


def test_start_emits_rtu_over_tcp_and_units(panel: GatewayPanel) -> None:
    panel.set_state({"listen": {"type": "RTU over TCP"}, "units": "2, 7-8"})
    spy = QSignalSpy(panel.startRequested)
    panel._button.click()
    assert spy.count() == 1
    listen, target, units = spy.at(0)
    assert isinstance(listen, GatewayRtuOverTcpListenParams)
    assert units == {2, 7, 8}


def test_start_emits_rtu_target(panel: GatewayPanel) -> None:
    panel.set_state(
        {"target": {"type": "RTU", "rtu_port": "/dev/ttyFAKE0", "rtu_baud": "19200"}}
    )
    spy = QSignalSpy(panel.startRequested)
    panel._button.click()
    listen, target, units = spy.at(0)
    assert isinstance(target, RtuParams)
    assert target.port == "/dev/ttyFAKE0"
    assert target.baudrate == 19200


def test_garbage_units_block_start(panel: GatewayPanel) -> None:
    panel._units.setText("junk")
    spy = QSignalSpy(panel.startRequested)
    panel._button.click()
    assert spy.count() == 0  # непустой мусор — ошибка, а не «обслуживать всех»
    assert panel._status_is_error
    assert panel._button.text() == "Start gateway"


def test_stop_emits_stop(panel: GatewayPanel) -> None:
    panel.set_running(True, "Gateway running (gw tcp 0.0.0.0:1502 -> tcp 1.2.3.4:502)")
    spy = QSignalSpy(panel.stopRequested)
    panel._button.click()
    assert spy.count() == 1


# --- set_running / статус ---


def test_set_running_toggles_ui(panel: GatewayPanel) -> None:
    assert panel._button.text() == "Start gateway"
    assert panel._listen.type_combo.isEnabled()
    panel.set_running(True, "Gateway running (gw tcp 0.0.0.0:1502 -> tcp 1.2.3.4:502)")
    assert panel._button.text() == "Stop gateway"
    assert not panel._listen.type_combo.isEnabled()
    assert not panel._target.type_combo.isEnabled()
    assert not panel._units.isEnabled()
    assert not panel._status_is_error
    panel.set_running(False, "Stopped")
    assert panel._button.text() == "Start gateway"
    assert panel._listen.type_combo.isEnabled()
    assert not panel._status_is_error
    panel.set_running(False, "boom")
    assert panel._status_is_error
    assert panel._status.text() == "boom"


def test_clients_counter_in_status(panel: GatewayPanel) -> None:
    panel.set_running(True, "Gateway running (gw tcp 0.0.0.0:1502 -> tcp 1.2.3.4:502)")
    panel.handle_client_changed(True)
    assert "clients: 1" in panel._status.text()
    panel.handle_client_changed(True)
    panel.handle_client_changed(False)
    assert "clients: 1" in panel._status.text()
    panel.handle_client_changed(False)
    panel.handle_client_changed(False)  # не уходит в минус
    assert "clients: 0" in panel._status.text()


def test_gateway_description(panel: GatewayPanel) -> None:
    assert panel.gateway_description() is None
    panel._button.click()  # запоминает last params (startRequested ни к чему не подключён)
    panel.set_running(True, "Gateway running (gw tcp 0.0.0.0:1502 -> tcp 127.0.0.1:502)")
    assert panel.gateway_description() == "gw tcp 0.0.0.0:1502 -> tcp 127.0.0.1:502"
    panel.set_running(False, "Stopped")
    assert panel.gateway_description() is None


# --- перевод ---


def test_retranslate(panel: GatewayPanel) -> None:
    i18n.set_language("ru")
    panel.retranslate()
    assert panel._button.text() == "Запустить шлюз"
    assert panel._listen.type_combo.currentData() == "TCP"  # ключи не переводятся
    panel.set_running(True, "Gateway running (gw tcp 0.0.0.0:1502 -> tcp 1.2.3.4:502)")
    assert panel._button.text() == "Остановить шлюз"
    assert "клиентов: 0" in panel._status.text()
    i18n.set_language("en")
    panel.retranslate()
    assert panel._button.text() == "Stop gateway"
    panel.set_running(False, "Stopped")
    assert panel._status.text() == "Stopped"
