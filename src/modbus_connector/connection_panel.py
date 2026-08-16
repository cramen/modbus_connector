from collections.abc import Callable

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from serial.tools import list_ports

from modbus_connector import icons, theme
from modbus_connector.i18n import tr
from modbus_connector.models import (
    ConnectionParams,
    RtuOverTcpParams,
    RtuOverUdpParams,
    RtuParams,
    TcpParams,
)

BAUDRATES = ["9600", "19200", "38400", "57600", "115200"]
CONNECTION_TYPES = ("TCP", "RTU", "RTU over TCP", "RTU over UDP")  # never translated

DEVICE_ID_NAMES = {0x00: "VendorName", 0x01: "ProductCode", 0x02: "MajorMinorRevision"}


class ConnectionPanel(QWidget):
    connectRequested = Signal(object, int)
    disconnectRequested = Signal()
    deviceIdRequested = Signal(int, int)
    diagLoopbackRequested = Signal(int, int)
    diagCountersRequested = Signal(int, int)
    diagClearRequested = Signal(int, int)

    def __init__(
        self,
        request_id_provider: Callable[[], int],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._next_request_id = request_id_provider
        self._device_id_request = -1
        self._device_id_list: QListWidget | None = None
        self._diag_loopback_request = -1
        self._diag_counters_request = -1
        self._diag_status: QLabel | None = None
        self._diag_list: QListWidget | None = None
        self._connected = False
        self._alive = False
        self._status_message = "Disconnected"
        self._status_is_error = False
        self._translatable: list[tuple[QWidget, str]] = []  # (widget, English key)
        self._translatable_tips: list[tuple[QWidget, str]] = []

        self._type_combo = theme.FitComboBox()
        for type_name in CONNECTION_TYPES:
            self._type_combo.addItem(tr(type_name), type_name)  # data stays English

        self._tcp_host = QLineEdit("127.0.0.1")
        self._tcp_host.setMaximumWidth(140)
        self._tcp_port = QSpinBox(minimum=1, maximum=65535, value=502)
        network_page = QWidget()  # shared by TCP and both RTU-over-* types
        network_layout = QHBoxLayout(network_page)
        network_layout.setContentsMargins(0, 0, 0, 0)
        network_layout.addWidget(self._label("Host:"))
        network_layout.addWidget(self._tcp_host)
        network_layout.addWidget(self._label("Port:"))
        network_layout.addWidget(self._tcp_port)
        network_layout.addStretch(1)

        self._rtu_port = theme.FitComboBox()
        self._rtu_port.setMinimumWidth(140)
        self._rtu_port.setMaximumWidth(220)  # long device paths must not widen the window
        self._rtu_refresh = icons.make_button(tr("Refresh"), "readwrite")
        self._track(self._rtu_refresh, "Refresh")
        self._rtu_baud = theme.FitComboBox(editable=True)
        self._rtu_baud.addItems(BAUDRATES)
        self._rtu_bytesize = theme.FitComboBox()
        self._rtu_bytesize.addItems(["8", "7"])
        self._rtu_parity = theme.FitComboBox()
        self._rtu_parity.addItems(["N", "E", "O"])
        self._rtu_stopbits = theme.FitComboBox()
        self._rtu_stopbits.addItems(["1", "2"])
        rtu_page = QWidget()
        rtu_layout = QHBoxLayout(rtu_page)
        rtu_layout.setContentsMargins(0, 0, 0, 0)
        for label, widget in (
            ("Port:", self._rtu_port),
            ("", self._rtu_refresh),
            ("Baud:", self._rtu_baud),
            ("Bits:", self._rtu_bytesize),
            ("Parity:", self._rtu_parity),
            ("Stop:", self._rtu_stopbits),
        ):
            if label:
                rtu_layout.addWidget(self._label(label))
            rtu_layout.addWidget(widget)
        rtu_layout.addStretch(1)

        self._stack = QStackedWidget()
        self._stack.addWidget(network_page)
        self._stack.addWidget(rtu_page)
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        self._type_combo.setCurrentIndex(1)  # RTU by default

        self._unit = QSpinBox(minimum=1, maximum=247, value=1)
        self._timeout = QDoubleSpinBox(minimum=0.1, maximum=60.0, value=3.0)
        self._timeout.setSingleStep(0.5)
        self._timeout.setSuffix(" s")

        self._button = icons.make_button(tr("Connect"), "connect")  # text set in _sync_button_text
        self._button.clicked.connect(self._on_button_clicked)
        self._device_id_button = icons.make_button(tr("Device ID…"), "device_id")
        self._track(self._device_id_button, "Device ID…")
        self._device_id_button.setEnabled(False)  # only meaningful while connected
        self._device_id_button.clicked.connect(self._on_device_id_clicked)
        self._diag_button = icons.make_button(tr("Diagnostics…"), "diagnostics")
        self._track(
            self._diag_button,
            "Diagnostics…",
            "Serial-line diagnostics (0x08); some TCP devices answer it too",
        )
        self._diag_button.setEnabled(False)
        self._diag_button.clicked.connect(self._on_diag_clicked)
        self._status = QLabel(tr("Disconnected"))
        self._status.setStyleSheet("color: gray")
        # the status text must never drive the window width: Ignored keeps it
        # out of the layout's size hint, long messages just truncate visually
        self._status.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self._rtu_refresh.clicked.connect(self._refresh_ports)
        self._refresh_ports()

        settings_row = QHBoxLayout()
        settings_row.addWidget(self._type_combo)
        settings_row.addWidget(self._stack, 1)
        settings_row.addWidget(self._label("Unit:"))
        settings_row.addWidget(self._unit)
        settings_row.addWidget(self._label("Timeout:"))
        settings_row.addWidget(self._timeout)
        settings_row.addStretch(1)

        self._controls_row = QHBoxLayout()
        self._controls_row.addWidget(self._button)
        self._controls_row.addWidget(self._device_id_button)
        self._controls_row.addWidget(self._diag_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(settings_row)
        layout.addLayout(self._controls_row)
        # Ignored policy keeps it out of the size hint; stretch factor 1 lets it
        # occupy the spare row space, long text truncates visually
        self._controls_row.addWidget(self._status, 1)

    def add_control(self, widget: QWidget) -> None:
        """Add a button to the panel's second row (before the status label)."""
        self._controls_row.insertWidget(self._controls_row.count() - 1, widget)

    def _label(self, text: str) -> QLabel:
        label = QLabel(tr(text))
        self._translatable.append((label, text))
        return label

    def _track(self, widget: QWidget, text: str, tip: str | None = None) -> None:
        widget.setText(tr(text))
        self._translatable.append((widget, text))
        # icon-only buttons have no visible text: tooltip carries the label
        widget.setToolTip(tr(tip if tip is not None else text))
        self._translatable_tips.append((widget, tip if tip is not None else text))

    def retranslate(self) -> None:
        """Переприменить tr() ко всем строкам панели (по смене языка)."""
        for widget, text in self._translatable:
            widget.setText(tr(text))
        for widget, tip in self._translatable_tips:
            widget.setToolTip(tr(tip))
        for index in range(self._type_combo.count()):
            key = self._type_combo.itemData(index)  # English, set at build time
            self._type_combo.setItemText(index, tr(key))
        self._sync_button_text()
        self._render_status()

    def _sync_button_text(self) -> None:
        text = tr("Disconnect") if self._connected else tr("Connect")
        icon_name = "disconnect" if self._connected else "connect"
        self._button.setText(text)
        self._button.setToolTip(text)
        self._button.setAccessibleName(text)
        self._button.setIcon(icons.icon(icon_name))
        icons.register(self._button, icon_name)

    def unit_id(self) -> int:
        return self._unit.value()

    @Slot(int)
    def set_unit_id(self, unit: int) -> None:
        self._unit.setValue(unit)

    def state(self) -> dict:
        return {
            "type": self._type_combo.currentData(),  # English key, not the display text
            "tcp_host": self._tcp_host.text(),
            "tcp_port": self._tcp_port.value(),
            "rtu_port": self._rtu_port.currentText(),
            "rtu_baud": self._rtu_baud.currentText(),
            "rtu_bytesize": self._rtu_bytesize.currentText(),
            "rtu_parity": self._rtu_parity.currentText(),
            "rtu_stopbits": self._rtu_stopbits.currentText(),
            "unit": self._unit.value(),
            "timeout": self._timeout.value(),
        }

    def set_state(self, state: dict) -> None:
        if not state:
            return
        type_index = self._type_combo.findData(str(state.get("type", "")))
        if type_index >= 0:
            self._type_combo.setCurrentIndex(type_index)
        self._tcp_host.setText(str(state.get("tcp_host", self._tcp_host.text())))
        self._tcp_port.setValue(int(state.get("tcp_port", self._tcp_port.value())))
        rtu_port = str(state.get("rtu_port", ""))
        if rtu_port:
            if self._rtu_port.findText(rtu_port) < 0:
                self._rtu_port.addItem(rtu_port)  # the popup fits itself on show
            self._rtu_port.setCurrentText(rtu_port)
        self._rtu_baud.setCurrentText(str(state.get("rtu_baud", self._rtu_baud.currentText())))
        for combo, key in (
            (self._rtu_bytesize, "rtu_bytesize"),
            (self._rtu_parity, "rtu_parity"),
            (self._rtu_stopbits, "rtu_stopbits"),
        ):
            text = str(state.get(key, ""))
            if text and combo.findText(text) >= 0:
                combo.setCurrentText(text)
        self._unit.setValue(int(state.get("unit", self._unit.value())))
        self._timeout.setValue(float(state.get("timeout", self._timeout.value())))

    def _build_params(self) -> ConnectionParams:
        timeout = self._timeout.value()
        type_index = self._type_combo.currentIndex()
        if type_index == 1:
            return RtuParams(
                port=self._rtu_port.currentText(),
                baudrate=int(self._rtu_baud.currentText()),
                bytesize=int(self._rtu_bytesize.currentText()),
                parity=self._rtu_parity.currentText(),
                stopbits=int(self._rtu_stopbits.currentText()),
                timeout=timeout,
            )
        if type_index == 2:
            return RtuOverTcpParams(
                host=self._tcp_host.text().strip(),
                port=self._tcp_port.value(),
                timeout=timeout,
            )
        if type_index == 3:
            return RtuOverUdpParams(
                host=self._tcp_host.text().strip(),
                port=self._tcp_port.value(),
                timeout=timeout,
            )
        return TcpParams(
            host=self._tcp_host.text().strip(),
            port=self._tcp_port.value(),
            timeout=timeout,
        )

    @Slot(int)
    def _on_type_changed(self, index: int) -> None:
        self._stack.setCurrentIndex(1 if index == 1 else 0)  # serial page or network page

    @Slot()
    def _on_button_clicked(self) -> None:
        if self._connected:
            self.disconnectRequested.emit()
            return
        try:
            params = self._build_params()
        except ValueError:
            self._status_message = "Invalid settings"
            self._status_is_error = True
            self._render_status()
            return
        self.connectRequested.emit(params, self._unit.value())

    @Slot()
    def _refresh_ports(self) -> None:
        current = self._rtu_port.currentText()
        previous = {self._rtu_port.itemText(i) for i in range(self._rtu_port.count())}
        ports = [p.device for p in list_ports.comports()]
        self._rtu_port.clear()
        self._rtu_port.addItems(ports)  # the popup fits itself on show
        new_ports = [p for p in ports if p not in previous]
        if new_ports:
            self._rtu_port.setCurrentText(new_ports[0])
            return
        index = self._rtu_port.findText(current)
        if index >= 0:
            self._rtu_port.setCurrentIndex(index)

    @Slot(bool, str)
    def set_connected(self, ok: bool, message: str) -> None:
        self._connected = ok
        self._alive = ok  # assume alive right after connect; reset on disconnect
        self._status_message = message
        self._status_is_error = False
        self._render_status()
        self._sync_button_text()
        self._device_id_button.setEnabled(ok)
        self._diag_button.setEnabled(ok)
        for widget in (
            self._type_combo,
            self._tcp_host,
            self._tcp_port,
            self._rtu_port,
            self._rtu_refresh,
            self._rtu_baud,
            self._rtu_bytesize,
            self._rtu_parity,
            self._rtu_stopbits,
            self._timeout,
        ):
            widget.setEnabled(not ok)

    @Slot(bool)
    def set_alive(self, alive: bool) -> None:
        self._alive = alive
        self._render_status()

    def _render_status(self) -> None:
        if self._status_is_error:
            self._status.setText(tr(self._status_message))
            self._status.setStyleSheet("color: red")
            return
        colors = theme.status_colors()
        if not self._connected:
            text, color = self._status_message, colors["off"]
        elif self._alive:
            text, color = self._status_message, colors["ok"]
        else:
            # pymodbus drops `connected` after a timeout but silently reconnects
            # on the next transaction — the link is idle/degraded, not dead
            text = f"{self._status_message} {tr('(idle)')}"
            color = colors["idle"]
        self._status.setText(tr(text))  # unknown strings pass through as English
        self._status.setStyleSheet(f"color: {color}")

    def refresh_theme(self) -> None:
        self._render_status()

    @Slot()
    def _on_device_id_clicked(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("Device identification (0x2B/0x0E)"))
        list_widget = QListWidget()
        list_widget.addItem(tr("Reading…"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout = QVBoxLayout(dialog)
        layout.addWidget(list_widget)
        layout.addWidget(buttons)
        self._device_id_list = list_widget
        self._device_id_request = self._next_request_id()
        self.deviceIdRequested.emit(self._device_id_request, self._unit.value())
        dialog.exec()
        self._device_id_list = None
        self._device_id_request = -1

    @Slot(int, bool, dict, str)
    def handle_device_id_finished(
        self, request_id: int, ok: bool, info: dict, error: str
    ) -> None:
        if request_id != self._device_id_request or self._device_id_list is None:
            return
        list_widget = self._device_id_list
        list_widget.clear()
        if not ok:
            list_widget.addItem(f"✗ {error}")
            return
        if not info:
            list_widget.addItem(tr("(device reported no objects)"))
        for object_id, value in sorted(info.items()):
            label = DEVICE_ID_NAMES.get(object_id, f"object 0x{object_id:02X}")
            list_widget.addItem(f"{label}: {value}")

    @Slot()
    def _on_diag_clicked(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("Diagnostics (function 0x08)"))
        loopback_button = QPushButton(tr("Loopback"))
        status_label = QLabel("—")
        counters_list = QListWidget()
        refresh_button = QPushButton(tr("Refresh"))
        clear_button = QPushButton(tr("Clear counters"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        top = QHBoxLayout()
        top.addWidget(loopback_button)
        top.addWidget(status_label)
        top.addStretch(1)
        top.addWidget(refresh_button)
        top.addWidget(clear_button)
        layout = QVBoxLayout(dialog)
        layout.addLayout(top)
        layout.addWidget(counters_list)
        layout.addWidget(buttons)
        self._diag_status = status_label
        self._diag_list = counters_list
        loopback_button.clicked.connect(self._request_diag_loopback)
        refresh_button.clicked.connect(self._request_diag_counters)
        clear_button.clicked.connect(self._request_diag_clear)
        self._request_diag_counters()  # initial load
        dialog.exec()
        self._diag_status = None
        self._diag_list = None
        self._diag_loopback_request = -1
        self._diag_counters_request = -1

    def _request_diag_loopback(self) -> None:
        if self._diag_status is None:
            return
        self._diag_status.setText("…")
        self._diag_loopback_request = self._next_request_id()
        self.diagLoopbackRequested.emit(self._diag_loopback_request, self._unit.value())

    def _request_diag_counters(self) -> None:
        if self._diag_list is None:
            return
        self._diag_counters_request = self._next_request_id()
        self.diagCountersRequested.emit(self._diag_counters_request, self._unit.value())

    def _request_diag_clear(self) -> None:
        if self._diag_list is None:
            return
        self._diag_counters_request = self._next_request_id()
        self.diagClearRequested.emit(self._diag_counters_request, self._unit.value())

    @Slot(int, bool, str)
    def handle_diag_loopback_finished(
        self, request_id: int, echo_ok: bool, error: str
    ) -> None:
        if request_id != self._diag_loopback_request or self._diag_status is None:
            return
        if error:
            self._diag_status.setText(f"✗ {error}")
        else:
            self._diag_status.setText("OK" if echo_ok else tr("mismatch"))

    @Slot(int, bool, dict, str)
    def handle_diag_counters_finished(
        self, request_id: int, ok: bool, counters: dict, error: str
    ) -> None:
        if request_id != self._diag_counters_request or self._diag_list is None:
            return
        self._diag_list.clear()
        if not ok:
            self._diag_list.addItem(f"✗ {error}")
            return
        for name, value in counters.items():
            self._diag_list.addItem(f"{name}: {value}")
