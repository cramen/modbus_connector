"""Панель режима «gateway»: прозрачный шлюз listen-сервер -> target-устройство.

Две строки параметров (общий виджет _EndpointRow по паттерну ConnectionPanel):
listen — где шлюз слушает мастеров (TCP / RTU over TCP / RTU), target — куда
транслируются запросы (все типы ConnectionPanel). Поле Units фильтрует
обслуживаемые unit-адреса: пусто = все 1..247, «1, 5, 10-20» — только эти.
"""

from typing import Any

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from serial.tools import list_ports

from modbus_connector import icons, theme
from modbus_connector.connection_panel import BAUDRATES, CONNECTION_TYPES
from modbus_connector.gateway_backend import (
    GatewayListenParams,
    GatewayRtuOverTcpListenParams,
    GatewayTcpListenParams,
    describe_gateway,
)
from modbus_connector.help_dialog import GATEWAY_HELP, make_help_button
from modbus_connector.i18n import tr
from modbus_connector.models import (
    ConnectionParams,
    RtuOverTcpParams,
    RtuOverUdpParams,
    RtuParams,
    TcpParams,
)

LISTEN_TYPES = ("TCP", "RTU over TCP", "RTU")  # never translated

UNITS_TOOLTIP = (
    "Units to serve: empty = all 1..247; comma-separated ids and ranges, "
    "e.g. 1, 5, 10-20; other units get no answer (master times out)"
)


def parse_units(text: str) -> set[int] | None:
    """«1, 5, 10-20» → {1, 5, 10..20}; пустой текст → None (все 1..247).

    Толерантный разбор: мусорные части пропускаются, значения вне 1..247
    отбрасываются, диапазоны обрезаются до 1..247.
    """
    if not text.strip():
        return None
    units: set[int] = set()
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        bounds = part.split("-")
        if len(bounds) == 2:
            try:
                lo, hi = int(bounds[0]), int(bounds[1])
            except ValueError:
                continue
            if lo > hi:
                continue
            units.update(unit for unit in range(lo, hi + 1) if 1 <= unit <= 247)
            continue
        try:
            value = int(part)
        except ValueError:
            continue
        if 1 <= value <= 247:
            units.add(value)
    return units


class _EndpointRow(QWidget):
    """Строка параметров endpoint'а: комбо типа + страницы network/serial.

    Ключи типов лежат в itemData английскими (не переводятся), serial-страница
    показывается для ключа "RTU"; раскладка и формат state() повторяют
    ConnectionPanel (без кнопок Connect/Scanner — только поля параметров).
    """

    def __init__(
        self,
        types: tuple[str, ...],
        host: str,
        port: int,
        with_timeout: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._translatable: list[tuple[QWidget, str]] = []  # (widget, English key)
        self._translatable_tips: list[tuple[QWidget, str]] = []

        self.type_combo = theme.FitComboBox()
        for type_name in types:
            self.type_combo.addItem(tr(type_name), type_name)  # data stays English

        self.tcp_host = QLineEdit(host)
        self.tcp_host.setMaximumWidth(140)
        self.tcp_port = QSpinBox(minimum=1, maximum=65535, value=port)
        network_page = QWidget()
        network_layout = QHBoxLayout(network_page)
        network_layout.setContentsMargins(0, 0, 0, 0)
        network_layout.addWidget(self._label("Host:"))
        network_layout.addWidget(self.tcp_host)
        network_layout.addWidget(self._label("Port:"))
        network_layout.addWidget(self.tcp_port)
        network_layout.addStretch(1)

        self.rtu_port = theme.FitComboBox()
        self.rtu_port.setMinimumWidth(140)
        self.rtu_port.setMaximumWidth(220)  # длинные пути не должны растягивать окно
        self.rtu_refresh = icons.make_button(tr("Refresh"), "readwrite")
        self._track(self.rtu_refresh, "Refresh")
        self.rtu_baud = theme.FitComboBox(editable=True)
        self.rtu_baud.addItems(BAUDRATES)
        self.rtu_bytesize = theme.FitComboBox()
        self.rtu_bytesize.addItems(["8", "7"])
        self.rtu_parity = theme.FitComboBox()
        self.rtu_parity.addItems(["N", "E", "O"])
        self.rtu_stopbits = theme.FitComboBox()
        self.rtu_stopbits.addItems(["1", "2"])
        rtu_page = QWidget()
        rtu_layout = QHBoxLayout(rtu_page)
        rtu_layout.setContentsMargins(0, 0, 0, 0)
        for label, widget in (
            ("Port:", self.rtu_port),
            ("", self.rtu_refresh),
            ("Baud:", self.rtu_baud),
            ("Bits:", self.rtu_bytesize),
            ("Parity:", self.rtu_parity),
            ("Stop:", self.rtu_stopbits),
        ):
            if label:
                rtu_layout.addWidget(self._label(label))
            rtu_layout.addWidget(widget)
        rtu_layout.addStretch(1)

        self._stack = QStackedWidget()
        self._stack.addWidget(network_page)
        self._stack.addWidget(rtu_page)
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.type_combo)
        layout.addWidget(self._stack, 1)
        self.timeout_spin: QDoubleSpinBox | None = None
        if with_timeout:
            self.timeout_spin = QDoubleSpinBox(minimum=0.1, maximum=60.0, value=3.0)
            self.timeout_spin.setSingleStep(0.5)
            self.timeout_spin.setSuffix(" s")
            layout.addWidget(self._label("Timeout:"))
            layout.addWidget(self.timeout_spin)

        self.rtu_refresh.clicked.connect(self._refresh_ports)
        self._refresh_ports()

    def _label(self, text: str) -> QLabel:
        label = QLabel(tr(text))
        self._translatable.append((label, text))
        return label

    def _track(self, widget: QWidget, text: str, tip: str | None = None) -> None:
        widget.setText(tr(text))
        self._translatable.append((widget, text))
        # у иконочных кнопок подпись живёт в тултипе
        widget.setToolTip(tr(tip if tip is not None else text))
        self._translatable_tips.append((widget, tip if tip is not None else text))

    @Slot(int)
    def _on_type_changed(self, index: int) -> None:
        serial = self.type_combo.itemData(index) == "RTU"
        self._stack.setCurrentIndex(1 if serial else 0)

    @Slot()
    def _refresh_ports(self) -> None:
        current = self.rtu_port.currentText()
        previous = {self.rtu_port.itemText(i) for i in range(self.rtu_port.count())}
        ports = [p.device for p in list_ports.comports()]
        self.rtu_port.clear()
        self.rtu_port.addItems(ports)  # попап сам подгонит ширину
        new_ports = [p for p in ports if p not in previous]
        if new_ports:
            self.rtu_port.setCurrentText(new_ports[0])
            return
        index = self.rtu_port.findText(current)
        if index >= 0:
            self.rtu_port.setCurrentIndex(index)

    def is_serial(self) -> bool:
        return self.type_combo.currentData() == "RTU"

    def rtu_params(self, timeout: float = 3.0) -> RtuParams:
        """Serial-параметры; ValueError на мусоре в editable-поле baud."""
        return RtuParams(
            port=self.rtu_port.currentText(),
            baudrate=int(self.rtu_baud.currentText()),
            bytesize=int(self.rtu_bytesize.currentText()),
            parity=self.rtu_parity.currentText(),
            stopbits=int(self.rtu_stopbits.currentText()),
            timeout=timeout,
        )

    def set_controls_enabled(self, enabled: bool) -> None:
        widgets: list[QWidget] = [
            self.type_combo,
            self.tcp_host,
            self.tcp_port,
            self.rtu_port,
            self.rtu_refresh,
            self.rtu_baud,
            self.rtu_bytesize,
            self.rtu_parity,
            self.rtu_stopbits,
        ]
        if self.timeout_spin is not None:
            widgets.append(self.timeout_spin)
        for widget in widgets:
            widget.setEnabled(enabled)

    def state(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "type": self.type_combo.currentData(),  # English key, не display-текст
            "tcp_host": self.tcp_host.text(),
            "tcp_port": self.tcp_port.value(),
            "rtu_port": self.rtu_port.currentText(),
            "rtu_baud": self.rtu_baud.currentText(),
            "rtu_bytesize": self.rtu_bytesize.currentText(),
            "rtu_parity": self.rtu_parity.currentText(),
            "rtu_stopbits": self.rtu_stopbits.currentText(),
        }
        if self.timeout_spin is not None:
            data["timeout"] = self.timeout_spin.value()
        return data

    def set_state(self, state: dict) -> None:
        if not isinstance(state, dict):
            return
        type_index = self.type_combo.findData(str(state.get("type", "")))
        if type_index >= 0:
            self.type_combo.setCurrentIndex(type_index)
        host = state.get("tcp_host")
        if host is not None:
            self.tcp_host.setText(str(host))
        try:
            port = int(state.get("tcp_port", self.tcp_port.value()))
        except (TypeError, ValueError):
            port = self.tcp_port.value()
        self.tcp_port.setValue(min(65535, max(1, port)))
        rtu_port = str(state.get("rtu_port", ""))
        if rtu_port:
            if self.rtu_port.findText(rtu_port) < 0:
                self.rtu_port.addItem(rtu_port)  # попап сам подгонит ширину
            self.rtu_port.setCurrentText(rtu_port)
        baud = state.get("rtu_baud")
        if baud is not None and str(baud):
            self.rtu_baud.setCurrentText(str(baud))
        for combo, key in (
            (self.rtu_bytesize, "rtu_bytesize"),
            (self.rtu_parity, "rtu_parity"),
            (self.rtu_stopbits, "rtu_stopbits"),
        ):
            text = str(state.get(key, ""))
            if text and combo.findText(text) >= 0:
                combo.setCurrentText(text)
        if self.timeout_spin is not None:
            try:
                timeout = float(state.get("timeout", self.timeout_spin.value()))
            except (TypeError, ValueError):
                timeout = self.timeout_spin.value()
            self.timeout_spin.setValue(timeout)  # spinbox сам обрежет до диапазона

    def retranslate(self) -> None:
        for widget, text in self._translatable:
            widget.setText(tr(text))
        for widget, tip in self._translatable_tips:
            widget.setToolTip(tr(tip))
        for index in range(self.type_combo.count()):
            key = self.type_combo.itemData(index)  # English, set at build time
            self.type_combo.setItemText(index, tr(key))


class GatewayPanel(QWidget):
    """Gateway-режим сессии: listen-строка, target-строка и фильтр unit'ов."""

    startRequested = Signal(object, object, object)  # listen, target, units (set | None)
    # dataclass-параметры не маршаллятся через Q_ARG — только сигналом
    stopRequested = Signal()
    logLine = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._running = False
        self._clients = 0
        self._status_message = "Stopped"
        self._status_is_error = False
        self._last_listen: GatewayListenParams | None = None
        self._last_target: ConnectionParams | None = None
        self._translatable: list[tuple[QWidget, str]] = []  # (widget, English key)
        self._translatable_tips: list[tuple[QWidget, str]] = []

        self._listen = _EndpointRow(LISTEN_TYPES, host="0.0.0.0", port=1502,
                                    with_timeout=False)
        self._target = _EndpointRow(CONNECTION_TYPES, host="127.0.0.1", port=502,
                                    with_timeout=True)

        listen_row = QHBoxLayout()
        listen_row.addWidget(self._label("Listen:"))
        listen_row.addWidget(self._listen, 1)
        target_row = QHBoxLayout()
        target_row.addWidget(self._label("Target:"))
        target_row.addWidget(self._target, 1)

        self._units = QLineEdit()
        self._units.setPlaceholderText("1, 5, 10-20")
        self._units.setMaximumWidth(200)
        self._units.setToolTip(tr(UNITS_TOOLTIP))
        self._translatable_tips.append((self._units, UNITS_TOOLTIP))

        self._button = icons.make_button(tr("Start gateway"), "connect")
        self._button.clicked.connect(self._on_button_clicked)
        self._help_button = make_help_button(self, "Gateway — Help", GATEWAY_HELP)
        self._status = QLabel()
        # длинный статус не должен расширять окно (как в ConnectionPanel)
        self._status.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        controls_row = QHBoxLayout()
        controls_row.addWidget(self._label("Units:"))
        controls_row.addWidget(self._units)
        controls_row.addWidget(self._button)
        controls_row.addWidget(self._help_button)
        controls_row.addWidget(self._status, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(listen_row)
        layout.addLayout(target_row)
        layout.addLayout(controls_row)
        layout.addStretch(1)
        self._sync_button()
        self._render_status()

    # --- построение UI ---

    def _label(self, text: str) -> QLabel:
        label = QLabel(tr(text))
        self._translatable.append((label, text))
        return label

    def _sync_button(self) -> None:
        text = tr("Stop gateway") if self._running else tr("Start gateway")
        icon_name = "disconnect" if self._running else "connect"
        self._button.setText(text)
        self._button.setToolTip(text)
        self._button.setAccessibleName(text)
        self._button.setIcon(icons.icon(icon_name))
        icons.register(self._button, icon_name)

    # --- параметры ---

    def _build_listen_params(self) -> GatewayListenParams:
        row = self._listen
        if row.is_serial():
            return row.rtu_params()
        host = row.tcp_host.text().strip()
        if row.type_combo.currentData() == "RTU over TCP":
            return GatewayRtuOverTcpListenParams(host=host, port=row.tcp_port.value())
        return GatewayTcpListenParams(host=host, port=row.tcp_port.value())

    def _build_target_params(self) -> ConnectionParams:
        row = self._target
        timeout = row.timeout_spin.value() if row.timeout_spin is not None else 3.0
        type_name = row.type_combo.currentData()
        if type_name == "RTU":
            return row.rtu_params(timeout)
        host = row.tcp_host.text().strip()
        if type_name == "RTU over TCP":
            return RtuOverTcpParams(host=host, port=row.tcp_port.value(), timeout=timeout)
        if type_name == "RTU over UDP":
            return RtuOverUdpParams(host=host, port=row.tcp_port.value(), timeout=timeout)
        return TcpParams(host=host, port=row.tcp_port.value(), timeout=timeout)

    @Slot()
    def _on_button_clicked(self) -> None:
        if self._running:
            self.stopRequested.emit()
            return
        try:
            listen = self._build_listen_params()
            target = self._build_target_params()
        except ValueError:
            self._status_message = "Invalid settings"
            self._status_is_error = True
            self._render_status()
            return
        units = parse_units(self._units.text())
        if units is not None and not units:
            # непустой текст без единого валидного unit — это ошибка ввода,
            # а не «обслуживать никого»
            self._status_message = "Invalid settings"
            self._status_is_error = True
            self._render_status()
            return
        self._last_listen = listen
        self._last_target = target
        self.startRequested.emit(listen, target, units)

    # --- работа шлюза ---

    @Slot(bool, str)
    def set_running(self, ok: bool, message: str) -> None:
        """Слот на GatewayWorker.gatewayChanged: кнопка, статус, гейтинг."""
        self._running = ok
        if not ok:
            self._clients = 0
        self._status_message = message
        self._status_is_error = not ok and message != "Stopped"
        self._sync_button()
        self._render_status()
        self._listen.set_controls_enabled(not ok)
        self._target.set_controls_enabled(not ok)
        self._units.setEnabled(not ok)

    @Slot(bool)
    def handle_client_changed(self, connected: bool) -> None:
        self._clients = max(0, self._clients + (1 if connected else -1))
        self.logLine.emit(
            tr("← gateway client connected")
            if connected
            else tr("→ gateway client disconnected")
        )
        if self._running:
            self._render_status()

    def gateway_description(self) -> str | None:
        """«gw tcp 0.0.0.0:1502 -> rtu ...» для заголовка вкладки, если запущен."""
        if self._running and self._last_listen is not None and self._last_target is not None:
            return describe_gateway(self._last_listen, self._last_target)
        return None

    def _render_status(self) -> None:
        if self._status_is_error:
            self._status.setText(self._status_message)
            self._status.setStyleSheet("color: red")
            return
        colors = theme.status_colors()
        if self._running:
            text = (
                f"{tr(self._status_message)} — "
                f"{tr('clients: {count}', count=self._clients)}"
            )
            color = colors["ok"]
        else:
            text, color = tr(self._status_message), colors["off"]
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {color}")

    # --- состояние и перевод ---

    def state(self) -> dict[str, Any]:
        return {
            "listen": self._listen.state(),
            "target": self._target.state(),
            "units": self._units.text(),
        }

    def set_state(self, state: dict) -> None:
        if not isinstance(state, dict):
            return
        listen = state.get("listen")
        if isinstance(listen, dict):
            self._listen.set_state(listen)
        target = state.get("target")
        if isinstance(target, dict):
            self._target.set_state(target)
        units = state.get("units")
        if units is not None:
            self._units.setText(str(units))

    def retranslate(self) -> None:
        """Переприменить tr() ко всем строкам панели (по смене языка)."""
        for widget, text in self._translatable:
            widget.setText(tr(text))
        for widget, tip in self._translatable_tips:
            widget.setToolTip(tr(tip))
        self._listen.retranslate()
        self._target.retranslate()
        self._sync_button()
        self._render_status()
