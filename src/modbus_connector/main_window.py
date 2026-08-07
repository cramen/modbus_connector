import itertools
from collections.abc import Iterator

from PySide6.QtCore import QMetaObject, Qt, QThread
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QPushButton, QVBoxLayout, QWidget

from modbus_connector.connection_panel import ConnectionPanel
from modbus_connector.log_panel import LogPanel
from modbus_connector.models import ConnectionParams
from modbus_connector.registers_panel import RegistersPanel
from modbus_connector.scanner_panel import ScannerPanel
from modbus_connector.settings_store import load_settings, save_settings
from modbus_connector.worker import ModbusWorker


class MainWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modbus Connector")
        self.resize(1100, 750)

        self._request_ids: Iterator[int] = itertools.count(1)

        self.connection_panel = ConnectionPanel()
        self.connection_panel.set_state(load_settings())
        self.registers_panel = RegistersPanel(lambda: next(self._request_ids))
        self.log_panel = LogPanel()

        self.scanner_panel = ScannerPanel(self)
        self.scanner_panel.setWindowFlags(Qt.WindowType.Window)
        self.scanner_panel.setWindowTitle("Modbus Scanner")
        self.scanner_panel.resize(700, 500)

        self._scanner_button = QPushButton("Scanner…")
        self._scanner_button.clicked.connect(self._show_scanner)
        self._log_button = QPushButton("Log")
        self._log_button.setCheckable(True)
        self._log_button.setChecked(True)
        self._log_button.toggled.connect(self.log_panel.setVisible)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.connection_panel, 1)
        top.addWidget(self._scanner_button)
        top.addWidget(self._log_button)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(top)
        layout.addWidget(self.registers_panel, 1)
        layout.addWidget(self.log_panel)
        self.setCentralWidget(central)

        self._thread = QThread(self)
        self._worker = ModbusWorker()
        self._worker.moveToThread(self._thread)

        self.connection_panel.connectRequested.connect(self._on_connect_requested)
        self.connection_panel.connectRequested.connect(self._worker.connect_to)
        self.connection_panel.disconnectRequested.connect(self._worker.disconnect)
        self._worker.connectionChanged.connect(self.connection_panel.set_connected)

        self.registers_panel.readRequested.connect(self._worker.read)
        self.registers_panel.writeRequested.connect(self._worker.write)
        self._worker.readFinished.connect(self.registers_panel.handle_read_finished)
        self._worker.writeFinished.connect(self.registers_panel.handle_write_finished)

        self.scanner_panel.scanStarted.connect(self.registers_panel.stop_polling)
        self.scanner_panel.scanRequested.connect(self._worker.start_scan)
        # DirectConnection: stop_scan must run immediately in the GUI thread,
        # the worker thread is busy inside start_scan and cannot process queued slots.
        self.scanner_panel.scanStopRequested.connect(
            self._worker.stop_scan, Qt.ConnectionType.DirectConnection
        )
        self._worker.scanProgress.connect(self.scanner_panel.handle_scan_progress)
        self._worker.scanHit.connect(self.scanner_panel.handle_scan_hit)
        self._worker.scanFinished.connect(self.scanner_panel.handle_scan_finished)

        self._worker.logLine.connect(self.log_panel.append)
        self.registers_panel.logLine.connect(self.log_panel.append)

        self._thread.start()

    def _show_scanner(self) -> None:
        self.scanner_panel.show()
        self.scanner_panel.raise_()
        self.scanner_panel.activateWindow()

    def _on_connect_requested(self, params: ConnectionParams, unit_id: int) -> None:
        self.registers_panel.set_unit_id(unit_id)

    def closeEvent(self, event: QCloseEvent) -> None:
        save_settings(self.connection_panel.state())
        self.registers_panel.stop_polling()
        self._worker.stop_scan()
        QMetaObject.invokeMethod(
            self._worker, "disconnect", Qt.ConnectionType.BlockingQueuedConnection
        )
        self._thread.quit()
        self._thread.wait(3000)
        super().closeEvent(event)
