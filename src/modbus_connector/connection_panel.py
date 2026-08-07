from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QWidget,
)
from serial.tools import list_ports

from modbus_connector.models import ConnectionParams, RtuParams, TcpParams

BAUDRATES = ["9600", "19200", "38400", "57600", "115200"]


class ConnectionPanel(QWidget):
    connectRequested = Signal(object, int)
    disconnectRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._connected = False

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
        layout.addWidget(self._status)

    def unit_id(self) -> int:
        return self._unit.value()

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
        self._status.setText(message)
        self._status.setStyleSheet(f"color: {'green' if ok else 'gray'}")
        self._button.setText("Disconnect" if ok else "Connect")
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
