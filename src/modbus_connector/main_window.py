import itertools
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from PySide6.QtCore import QMetaObject, Qt, QThread, QTimer, Slot
from PySide6.QtGui import QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from modbus_connector.connection_panel import ConnectionPanel
from modbus_connector.log_panel import LogPanel
from modbus_connector.models import ConnectionParams, StatsSnapshot
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
        self.registers_panel = RegistersPanel(lambda: next(self._request_ids))
        self.log_panel = LogPanel()
        self._apply_state(load_settings())

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

        file_menu = self.menuBar().addMenu("File")
        save_action = file_menu.addAction("Save Settings to File…", self._save_to_file)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        load_action = file_menu.addAction("Load Settings from File…", self._load_from_file)
        load_action.setShortcut(QKeySequence.StandardKey.Open)

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

        self.scanner_panel.addrScanRequested.connect(self._worker.start_addr_scan)
        self._worker.addrScanProgress.connect(self.scanner_panel.handle_addr_scan_progress)
        self._worker.addrScanHit.connect(self.scanner_panel.handle_addr_scan_hit)
        self._worker.addrScanFinished.connect(self.scanner_panel.handle_addr_scan_finished)

        self.scanner_panel.unitSelected.connect(self.connection_panel.set_unit_id)
        self.scanner_panel.unitSelected.connect(
            lambda unit: self.log_panel.append(f"→ unit {unit} selected in connection panel")
        )

        self._worker.logLine.connect(self.log_panel.append)
        self._worker.trafficLine.connect(self.log_panel.append_raw)
        self.registers_panel.logLine.connect(self.log_panel.append)

        self._stats_label = QLabel()
        self._update_stats(StatsSnapshot())
        self.statusBar().addPermanentWidget(self._stats_label)
        self._worker.statsUpdated.connect(self._update_stats)

        self._worker.aliveChanged.connect(self.connection_panel.set_alive)
        # queued to the worker thread: check_alive runs where the backend lives
        self._alive_timer = QTimer(self)
        self._alive_timer.setInterval(2000)
        self._alive_timer.timeout.connect(self._worker.check_alive)
        self._alive_timer.start()

        self._thread.start()

    @Slot(object)
    def _update_stats(self, snapshot: StatsSnapshot) -> None:
        self._stats_label.setText(
            f"Tx: {snapshot.total}  Err: {snapshot.errors} "
            f"({snapshot.error_percent:.1f}%)  Avg: {snapshot.avg_ms:.0f} ms"
        )

    def _collect_state(self) -> dict[str, Any]:
        return {
            "connection": self.connection_panel.state(),
            "registers": self.registers_panel.state(),
        }

    def _apply_state(self, state: dict[str, Any]) -> None:
        if not state:
            return
        # backward compatibility: the old format stored connection keys at the top level
        connection = state.get("connection") if isinstance(state.get("connection"), dict) else state
        self.connection_panel.set_state(connection)
        registers = state.get("registers")
        if isinstance(registers, list):
            self.registers_panel.set_state(registers)

    def _save_to_file(self) -> None:
        path_str, _ = QFileDialog.getSaveFileName(
            self, "Save Settings", str(Path.home() / "settings.json"), "JSON (*.json)"
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            path.write_text(json.dumps(self._collect_state(), indent=2), encoding="utf-8")
        except OSError as exc:
            self.log_panel.append(f"✗ failed to save settings to {path}: {exc}")
            return
        self.log_panel.append(f"→ settings saved to {path}")

    def _load_from_file(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Load Settings", str(Path.home()), "JSON (*.json)"
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.log_panel.append(f"✗ failed to load settings from {path}: {exc}")
            return
        if not isinstance(state, dict):
            self.log_panel.append(f"✗ failed to load settings from {path}: not an object")
            return
        self._apply_state(state)
        self.log_panel.append(f"← settings loaded from {path}")

    def _show_scanner(self) -> None:
        self.scanner_panel.show()
        self.scanner_panel.raise_()
        self.scanner_panel.activateWindow()

    def _on_connect_requested(self, params: ConnectionParams, unit_id: int) -> None:
        self.registers_panel.set_unit_id(unit_id)

    def closeEvent(self, event: QCloseEvent) -> None:
        save_settings(self._collect_state())
        self.registers_panel.stop_polling()
        self._worker.stop_scan()
        QMetaObject.invokeMethod(
            self._worker, "disconnect", Qt.ConnectionType.BlockingQueuedConnection
        )
        self._thread.quit()
        self._thread.wait(3000)
        super().closeEvent(event)
