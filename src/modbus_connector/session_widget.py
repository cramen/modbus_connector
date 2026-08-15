import itertools
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QMetaObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from modbus_connector.connection_panel import ConnectionPanel
from modbus_connector.log_panel import LogPanel
from modbus_connector.models import ConnectionParams, StatsSnapshot, describe_connection
from modbus_connector.registers_panel import RegistersPanel
from modbus_connector.scanner_panel import ScannerPanel
from modbus_connector.worker import ModbusWorker

if TYPE_CHECKING:
    from modbus_connector.graph_window import GraphWindow


class SessionWidget(QWidget):
    """Одна Modbus-сессия: панели + worker в QThread + окно сканера."""

    DEFAULT_TITLE = "New connection"

    statsUpdated = Signal(object)
    titleChanged = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._request_ids: Iterator[int] = itertools.count(1)
        self._title = self.DEFAULT_TITLE
        self._params: ConnectionParams | None = None
        self._last_stats = StatsSnapshot()
        self._bus_enabled = False  # app starts disconnected; gates bus controls

        self.connection_panel = ConnectionPanel(lambda: next(self._request_ids))
        self.registers_panel = RegistersPanel(lambda: next(self._request_ids))
        self.log_panel = LogPanel()

        self.scanner_panel = ScannerPanel(lambda: next(self._request_ids), self)
        self.scanner_panel.setWindowFlags(Qt.WindowType.Window)
        self.scanner_panel.setWindowTitle("Modbus Scanner")
        self.scanner_panel.resize(700, 500)

        self._scanner_button = QPushButton("Scanner…")
        self._scanner_button.clicked.connect(self._show_scanner)
        self._graph_button = QPushButton("Graph…")
        self._graph_button.clicked.connect(self._show_graph)
        self._graph_window: GraphWindow | None = None
        self._log_button = QPushButton("Log")
        self._log_button.setCheckable(True)
        self._log_button.setChecked(True)
        self._log_button.toggled.connect(self.log_panel.setVisible)
        self.connection_panel.add_control(self._scanner_button)
        self.connection_panel.add_control(self._graph_button)
        self.connection_panel.add_control(self._log_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.connection_panel)
        layout.addWidget(self.registers_panel, 1)
        layout.addWidget(self.log_panel)

        self._thread = QThread(self)
        self._worker = ModbusWorker()
        self._worker.moveToThread(self._thread)

        self.connection_panel.connectRequested.connect(self._on_connect_requested)
        self.connection_panel.connectRequested.connect(self._worker.connect_to)
        self.connection_panel.disconnectRequested.connect(self._worker.disconnect)
        self.connection_panel.deviceIdRequested.connect(self._worker.read_device_id)
        self._worker.deviceIdFinished.connect(self.connection_panel.handle_device_id_finished)
        self.connection_panel.diagLoopbackRequested.connect(self._worker.diag_loopback)
        self.connection_panel.diagCountersRequested.connect(self._worker.diag_read_counters)
        self.connection_panel.diagClearRequested.connect(self._worker.diag_clear_counters)
        self._worker.diagLoopbackFinished.connect(
            self.connection_panel.handle_diag_loopback_finished
        )
        self._worker.diagCountersFinished.connect(
            self.connection_panel.handle_diag_counters_finished
        )
        self._worker.connectionChanged.connect(self.connection_panel.set_connected)
        self._worker.connectionChanged.connect(self._on_connection_changed)

        self.registers_panel.readRequested.connect(self._worker.read)
        self.registers_panel.writeRequested.connect(self._worker.write)
        self.registers_panel.maskWriteRequested.connect(self._worker.mask_write)
        self.registers_panel.readwriteRequested.connect(self._worker.readwrite)
        self._worker.readFinished.connect(self.registers_panel.handle_read_finished)
        self._worker.writeFinished.connect(self.registers_panel.handle_write_finished)
        self._worker.writeFinished.connect(self.registers_panel.handle_mask_write_finished)
        self._worker.readwriteFinished.connect(self.registers_panel.handle_readwrite_finished)

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

        self.scanner_panel.addrScanRequested.connect(self._worker.start_addr_scan)
        self._worker.addrScanProgress.connect(self.scanner_panel.handle_addr_scan_progress)
        self._worker.addrScanHit.connect(self.scanner_panel.handle_addr_scan_hit)
        self._worker.addrScanFinished.connect(self.scanner_panel.handle_addr_scan_finished)

        self.scanner_panel.unitSelected.connect(self.connection_panel.set_unit_id)
        self.scanner_panel.unitSelected.connect(
            lambda unit: self.log_panel.append(f"→ unit {unit} selected in connection panel")
        )
        self.scanner_panel.rowsAddRequested.connect(self.registers_panel.add_rows)
        self.scanner_panel.deviceIdRequested.connect(self._worker.read_device_id)
        self._worker.deviceIdFinished.connect(
            self.scanner_panel.handle_device_id_finished
        )

        self._worker.logLine.connect(self.log_panel.append)
        self._worker.trafficLine.connect(self.log_panel.append_raw)
        self.registers_panel.logLine.connect(self.log_panel.append)

        self._worker.statsUpdated.connect(self._on_stats_updated)

        self._worker.aliveChanged.connect(self.connection_panel.set_alive)
        # queued to the worker thread: check_alive runs where the backend lives
        self._alive_timer = QTimer(self)
        self._alive_timer.setInterval(2000)
        self._alive_timer.timeout.connect(self._worker.check_alive)
        self._alive_timer.start()

        self._thread.start()

    def state(self) -> dict[str, Any]:
        return {
            "connection": self.connection_panel.state(),
            "registers": self.registers_panel.state(),
            "registers_options": self.registers_panel.options_state(),
            "logging": self.registers_panel.logging_state(),
            "scanner": self.scanner_panel.state(),
        }

    def set_state(self, state: dict[str, Any]) -> None:
        if not state:
            return
        # backward compatibility: the old format stored connection keys at the top level
        connection = state.get("connection") if isinstance(state.get("connection"), dict) else state
        self.connection_panel.set_state(connection)
        registers = state.get("registers")
        if isinstance(registers, list):
            self.registers_panel.set_state(registers)
        options = state.get("registers_options")
        if isinstance(options, dict):
            self.registers_panel.set_options(options)
        logging = state.get("logging")
        if isinstance(logging, dict):
            self.registers_panel.set_logging_state(logging)
        scanner = state.get("scanner")
        if isinstance(scanner, dict):
            self.scanner_panel.set_state(scanner)

    def _show_scanner(self) -> None:
        self.scanner_panel.show()
        self.scanner_panel.raise_()
        self.scanner_panel.activateWindow()

    def _show_graph(self) -> None:
        if self._graph_window is None:
            # lazy import: pyqtgraph/numpy load only when the window opens
            from modbus_connector.graph_window import GraphWindow

            self._graph_window = GraphWindow(self.registers_panel, self)
            self._graph_window.set_bus_enabled(self._bus_enabled)
        self._graph_window.show()
        self._graph_window.raise_()
        self._graph_window.activateWindow()

    def _on_connect_requested(self, params: ConnectionParams, unit_id: int) -> None:
        self._params = params
        self.registers_panel.set_unit_id(unit_id)

    @Slot(bool, str)
    def _on_connection_changed(self, ok: bool, message: str) -> None:
        self._title = (
            describe_connection(self._params)
            if ok and self._params is not None
            else self.DEFAULT_TITLE
        )
        self.titleChanged.emit(self._title)
        self._bus_enabled = ok
        if not ok:  # a dead bus must not keep timers and the logger running
            self.registers_panel.stop_logging()
            self.registers_panel.stop_polling()
        self.registers_panel.set_bus_enabled(ok)
        self.scanner_panel.set_bus_enabled(ok)
        if self._graph_window is not None:
            self._graph_window.set_bus_enabled(ok)

    def title(self) -> str:
        return self._title

    @Slot(object)
    def _on_stats_updated(self, snapshot: StatsSnapshot) -> None:
        self._last_stats = snapshot
        self.statsUpdated.emit(snapshot)

    def last_stats(self) -> StatsSnapshot:
        return self._last_stats

    def shutdown(self) -> None:
        if not self._thread.isRunning():
            return  # already shut down; a blocking invoke would deadlock
        self.registers_panel.stop_logging()  # the logger must not outlive the session
        self.registers_panel.stop_polling()
        self._worker.stop_scan()
        QMetaObject.invokeMethod(
            self._worker, "disconnect", Qt.ConnectionType.BlockingQueuedConnection
        )
        self._thread.quit()
        self._thread.wait(3000)
