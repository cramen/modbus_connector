from collections.abc import Callable

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from serial.tools import list_ports

from modbus_connector.models import ConnectionParams, RtuParams, TcpParams

BAUDRATES = ["9600", "19200", "38400", "57600", "115200"]

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

        self._type_combo = QComboBox()
        self._type_combo.addItems(["TCP", "RTU"])

        self._tcp_host = QLineEdit("127.0.0.1")
        self._tcp_host.setMaximumWidth(140)
        self._tcp_port = QSpinBox(minimum=1, maximum=65535, value=502)
        tcp_page = QWidget()
        tcp_layout = QHBoxLayout(tcp_page)
        tcp_layout.setContentsMargins(0, 0, 0, 0)
        tcp_layout.addWidget(QLabel("Host:"))
        tcp_layout.addWidget(self._tcp_host)
        tcp_layout.addWidget(QLabel("Port:"))
        tcp_layout.addWidget(self._tcp_port)
        tcp_layout.addStretch(1)

        self._rtu_port = QComboBox()
        self._rtu_port.setMinimumWidth(140)
        self._rtu_refresh = QPushButton("Refresh")
        self._rtu_baud = QComboBox(editable=True)
        self._rtu_baud.addItems(BAUDRATES)
        self._rtu_bytesize = QComboBox()
        self._rtu_bytesize.addItems(["8", "7"])
        self._rtu_parity = QComboBox()
        self._rtu_parity.addItems(["N", "E", "O"])
        self._rtu_stopbits = QComboBox()
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
                rtu_layout.addWidget(QLabel(label))
            rtu_layout.addWidget(widget)
        rtu_layout.addStretch(1)

        self._stack = QStackedWidget()
        self._stack.addWidget(tcp_page)
        self._stack.addWidget(rtu_page)
        self._type_combo.currentIndexChanged.connect(self._stack.setCurrentIndex)
        self._type_combo.setCurrentIndex(1)  # RTU by default

        self._unit = QSpinBox(minimum=1, maximum=247, value=1)
        self._timeout = QDoubleSpinBox(minimum=0.1, maximum=60.0, value=3.0)
        self._timeout.setSingleStep(0.5)
        self._timeout.setSuffix(" s")

        self._button = QPushButton("Connect")
        self._button.clicked.connect(self._on_button_clicked)
        self._device_id_button = QPushButton("Device ID…")
        self._device_id_button.setEnabled(False)  # only meaningful while connected
        self._device_id_button.clicked.connect(self._on_device_id_clicked)
        self._diag_button = QPushButton("Diagnostics…")
        self._diag_button.setEnabled(False)
        self._diag_button.setToolTip(
            "Serial-line diagnostics (0x08); some TCP devices answer it too"
        )
        self._diag_button.clicked.connect(self._on_diag_clicked)
        self._status = QLabel("Disconnected")
        self._status.setStyleSheet("color: gray")
        self._rtu_refresh.clicked.connect(self._refresh_ports)
        self._refresh_ports()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._type_combo)
        layout.addWidget(self._stack, 1)
        layout.addWidget(QLabel("Unit:"))
        layout.addWidget(self._unit)
        layout.addWidget(QLabel("Timeout:"))
        layout.addWidget(self._timeout)
        layout.addWidget(self._button)
        layout.addWidget(self._device_id_button)
        layout.addWidget(self._diag_button)
        layout.addWidget(self._status)

    def unit_id(self) -> int:
        return self._unit.value()

    @Slot(int)
    def set_unit_id(self, unit: int) -> None:
        self._unit.setValue(unit)

    def state(self) -> dict:
        return {
            "type": self._type_combo.currentText(),
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
        type_index = self._type_combo.findText(str(state.get("type", "")))
        if type_index >= 0:
            self._type_combo.setCurrentIndex(type_index)
        self._tcp_host.setText(str(state.get("tcp_host", self._tcp_host.text())))
        self._tcp_port.setValue(int(state.get("tcp_port", self._tcp_port.value())))
        rtu_port = str(state.get("rtu_port", ""))
        if rtu_port:
            if self._rtu_port.findText(rtu_port) < 0:
                self._rtu_port.addItem(rtu_port)
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
        if self._type_combo.currentIndex() == 0:
            return TcpParams(
                host=self._tcp_host.text().strip(),
                port=self._tcp_port.value(),
                timeout=timeout,
            )
        return RtuParams(
            port=self._rtu_port.currentText(),
            baudrate=int(self._rtu_baud.currentText()),
            bytesize=int(self._rtu_bytesize.currentText()),
            parity=self._rtu_parity.currentText(),
            stopbits=int(self._rtu_stopbits.currentText()),
            timeout=timeout,
        )

    @Slot()
    def _on_button_clicked(self) -> None:
        if self._connected:
            self.disconnectRequested.emit()
            return
        try:
            params = self._build_params()
        except ValueError:
            self._status.setText("Invalid settings")
            self._status.setStyleSheet("color: red")
            return
        self.connectRequested.emit(params, self._unit.value())

    @Slot()
    def _refresh_ports(self) -> None:
        current = self._rtu_port.currentText()
        previous = {self._rtu_port.itemText(i) for i in range(self._rtu_port.count())}
        ports = [p.device for p in list_ports.comports()]
        self._rtu_port.clear()
        self._rtu_port.addItems(ports)
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
        self._render_status()
        self._button.setText("Disconnect" if ok else "Connect")
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
        if not self._connected:
            text, color = self._status_message, "gray"
        elif self._alive:
            text, color = self._status_message, "green"
        else:
            # pymodbus drops `connected` after a timeout but silently reconnects
            # on the next transaction — the link is idle/degraded, not dead
            text, color = f"{self._status_message} (idle)", "orange"
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {color}")

    @Slot()
    def _on_device_id_clicked(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Device identification (0x2B/0x0E)")
        list_widget = QListWidget()
        list_widget.addItem("Reading…")
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
            list_widget.addItem("(device reported no objects)")
        for object_id, value in sorted(info.items()):
            label = DEVICE_ID_NAMES.get(object_id, f"object 0x{object_id:02X}")
            list_widget.addItem(f"{label}: {value}")

    @Slot()
    def _on_diag_clicked(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Diagnostics (function 0x08)")
        loopback_button = QPushButton("Loopback")
        status_label = QLabel("—")
        counters_list = QListWidget()
        refresh_button = QPushButton("Refresh")
        clear_button = QPushButton("Clear counters")
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
            self._diag_status.setText("OK" if echo_ok else "mismatch")

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
