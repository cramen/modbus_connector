from typing import get_args

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from modbus_connector.models import DEFAULT_SCAN_PROBES, RegisterKind, ScanProbe
from modbus_connector.theme import FitComboBox

KINDS = list(get_args(RegisterKind))

COL_TYPE, COL_ADDRESS, COL_COUNT, COL_ACTIONS = range(4)


class ScannerPanel(QWidget):
    scanStarted = Signal()
    scanRequested = Signal(list, int, int)
    scanStopRequested = Signal()
    addrScanRequested = Signal(int, object, int, int)
    unitSelected = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bus_enabled = False  # no bus access until a connection is up

        self._start = QSpinBox(minimum=1, maximum=247, value=1)
        self._end = QSpinBox(minimum=1, maximum=247, value=247)

        self._probes_table = QTableWidget(0, 4)
        self._probes_table.setHorizontalHeaderLabels(["Type", "Address", "Count", ""])
        self._probes_table.verticalHeader().setVisible(False)
        self._probes_table.setMaximumHeight(140)

        add_probe_button = QPushButton("Add probe")
        add_probe_button.clicked.connect(lambda: self._add_probe())

        self._start_button = QPushButton("Start scan")
        self._start_button.setEnabled(False)
        self._stop_button = QPushButton("Stop")
        self._stop_button.setEnabled(False)
        self._start_button.clicked.connect(self._on_start)
        self._stop_button.clicked.connect(self.scanStopRequested)

        self._progress = QProgressBar()
        self._progress.setValue(0)
        self._results = QListWidget()
        self._results.setToolTip("Double-click a unit to select it for the connection")
        self._results.itemDoubleClicked.connect(self._on_unit_double_clicked)

        self._addr_unit = QSpinBox(minimum=1, maximum=247, value=1)
        self._addr_kind = FitComboBox()
        self._addr_kind.addItems(KINDS)
        self._addr_from = QSpinBox(minimum=0, maximum=65535, value=0)
        self._addr_to = QSpinBox(minimum=0, maximum=65535, value=99)
        self._addr_start_button = QPushButton("Start")
        self._addr_start_button.setEnabled(False)
        self._addr_stop_button = QPushButton("Stop")
        self._addr_stop_button.setEnabled(False)
        self._addr_start_button.clicked.connect(self._on_addr_start)
        self._addr_stop_button.clicked.connect(self.scanStopRequested)
        self._addr_progress = QProgressBar()
        self._addr_progress.setValue(0)
        self._addr_results = QListWidget()

        range_layout = QHBoxLayout()
        range_layout.addWidget(QLabel("Unit range:"))
        range_layout.addWidget(self._start)
        range_layout.addWidget(QLabel("–"))
        range_layout.addWidget(self._end)
        range_layout.addStretch(1)
        range_layout.addWidget(add_probe_button)
        range_layout.addWidget(self._start_button)
        range_layout.addWidget(self._stop_button)

        addr_layout = QHBoxLayout()
        addr_layout.addWidget(QLabel("Unit:"))
        addr_layout.addWidget(self._addr_unit)
        addr_layout.addWidget(QLabel("Type:"))
        addr_layout.addWidget(self._addr_kind)
        addr_layout.addWidget(QLabel("Addresses:"))
        addr_layout.addWidget(self._addr_from)
        addr_layout.addWidget(QLabel("–"))
        addr_layout.addWidget(self._addr_to)
        addr_layout.addStretch(1)
        addr_layout.addWidget(self._addr_start_button)
        addr_layout.addWidget(self._addr_stop_button)

        layout = QVBoxLayout(self)
        layout.addLayout(range_layout)
        layout.addWidget(self._probes_table)
        layout.addWidget(self._progress)
        layout.addWidget(QLabel("Results:"))
        layout.addWidget(self._results)
        self._addr_section_label = QLabel("Registers scan:")
        layout.addWidget(self._addr_section_label)
        layout.addLayout(addr_layout)
        layout.addWidget(self._addr_progress)
        layout.addWidget(self._addr_results)

        for probe in DEFAULT_SCAN_PROBES:
            self._add_probe(probe)

    def _add_probe(self, probe: ScanProbe | None = None) -> None:
        probe = probe or ScanProbe(kind="holding_registers", address=0, count=1)
        index = self._probes_table.rowCount()
        self._probes_table.insertRow(index)

        type_combo = FitComboBox()
        type_combo.addItems(KINDS)
        type_combo.setCurrentText(probe.kind)
        self._probes_table.setCellWidget(index, COL_TYPE, type_combo)
        # plain text items (dec or 0x-hex), keyboard-friendly like the main table
        self._probes_table.setItem(
            index, COL_ADDRESS, QTableWidgetItem(str(probe.address))
        )
        self._probes_table.setItem(index, COL_COUNT, QTableWidgetItem(str(probe.count)))

        delete_button = QPushButton("Delete")
        delete_button.clicked.connect(self._on_delete_probe)
        self._probes_table.setCellWidget(index, COL_ACTIONS, delete_button)

    @Slot()
    def _on_delete_probe(self) -> None:
        button = self.sender()
        for index in range(self._probes_table.rowCount()):
            if self._probes_table.cellWidget(index, COL_ACTIONS) is button:
                self._probes_table.removeRow(index)
                return

    def _probes(self) -> list[ScanProbe]:
        probes = []
        for index in range(self._probes_table.rowCount()):
            type_combo = self._probes_table.cellWidget(index, COL_TYPE)
            address_item = self._probes_table.item(index, COL_ADDRESS)
            count_item = self._probes_table.item(index, COL_COUNT)
            try:
                address = int(address_item.text().strip(), 0) if address_item else -1
                count = int(count_item.text().strip(), 0) if count_item else -1
            except ValueError:
                continue  # rows with invalid/empty numbers are skipped
            if not 0 <= address <= 65535 or not 1 <= count <= 125:
                continue
            probes.append(
                ScanProbe(
                    kind=type_combo.currentText(),
                    address=address,
                    count=count,
                )
            )
        return probes

    def state(self) -> dict:
        return {
            "start": self._start.value(),
            "end": self._end.value(),
            "probes": [
                {"kind": probe.kind, "address": probe.address, "count": probe.count}
                for probe in self._probes()
            ],
            "addr_unit": self._addr_unit.value(),
            "addr_kind": self._addr_kind.currentText(),
            "addr_from": self._addr_from.value(),
            "addr_to": self._addr_to.value(),
        }

    def set_state(self, state: dict) -> None:
        if not state:
            return
        for spin, key in (
            (self._start, "start"),
            (self._end, "end"),
            (self._addr_unit, "addr_unit"),
            (self._addr_from, "addr_from"),
            (self._addr_to, "addr_to"),
        ):
            try:
                spin.setValue(int(state.get(key, spin.value())))
            except (TypeError, ValueError):
                continue
        if state.get("addr_kind") in KINDS:
            self._addr_kind.setCurrentText(str(state["addr_kind"]))
        probes_entry = state.get("probes")
        if not isinstance(probes_entry, list):
            return
        probes = []
        for entry in probes_entry:
            try:
                probes.append(
                    ScanProbe(
                        kind=(
                            entry.get("kind")
                            if entry.get("kind") in KINDS
                            else "holding_registers"
                        ),
                        address=int(entry["address"]),
                        count=int(entry["count"]),
                    )
                )
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
        if not probes:
            probes = list(DEFAULT_SCAN_PROBES)
        while self._probes_table.rowCount():
            self._probes_table.removeRow(0)
        for probe in probes:
            self._add_probe(probe)

    @Slot()
    def _on_start(self) -> None:
        probes = self._probes()
        if not probes:
            return
        self._results.clear()
        self._progress.setValue(0)
        self._progress.setMaximum(1)
        self._start_button.setEnabled(False)
        self._stop_button.setEnabled(True)
        self.scanStarted.emit()
        self.scanRequested.emit(probes, self._start.value(), self._end.value())

    @Slot(int, int)
    def handle_scan_progress(self, done: int, total: int) -> None:
        self._progress.setMaximum(max(1, total))
        self._progress.setValue(done)

    @Slot(int, list)
    def handle_scan_hit(self, unit: int, probe_indices: list) -> None:
        probes = self._probes()
        labels = []
        for i in probe_indices:
            if 0 <= i < len(probes):
                probe = probes[i]
                labels.append(f"{probe.kind}@{probe.address} x{probe.count}")
            else:
                labels.append(f"probe#{i}")
        item = QListWidgetItem(f"Unit {unit}: {', '.join(labels)}")
        item.setData(Qt.ItemDataRole.UserRole, unit)
        self._results.addItem(item)

    @Slot(QListWidgetItem)
    def _on_unit_double_clicked(self, item: QListWidgetItem) -> None:
        unit = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(unit, int):
            self.unitSelected.emit(unit)

    def set_bus_enabled(self, ok: bool) -> None:
        """Включить/выключить кнопки Start по connectionChanged (Stop — всегда)."""
        self._bus_enabled = ok
        self._start_button.setEnabled(ok and not self._stop_button.isEnabled())
        self._addr_start_button.setEnabled(
            ok and not self._addr_stop_button.isEnabled()
        )

    @Slot()
    def handle_scan_finished(self) -> None:
        self._start_button.setEnabled(self._bus_enabled)
        self._stop_button.setEnabled(False)

    @Slot()
    def _on_addr_start(self) -> None:
        if self._addr_from.value() > self._addr_to.value():
            self._addr_from.setValue(self._addr_to.value())  # clamp inverted range
        self._addr_results.clear()
        self._addr_progress.setValue(0)
        self._addr_progress.setMaximum(1)
        self._addr_start_button.setEnabled(False)
        self._addr_stop_button.setEnabled(True)
        self.scanStarted.emit()
        self.addrScanRequested.emit(
            self._addr_unit.value(),
            self._addr_kind.currentText(),
            self._addr_from.value(),
            self._addr_to.value(),
        )

    @Slot(int, int)
    def handle_addr_scan_progress(self, done: int, total: int) -> None:
        self._addr_progress.setMaximum(max(1, total))
        self._addr_progress.setValue(done)

    @Slot(int)
    def handle_addr_scan_hit(self, address: int) -> None:
        self._addr_results.addItem(f"0x{address:04X} ({address})")

    @Slot()
    def handle_addr_scan_finished(self) -> None:
        self._addr_start_button.setEnabled(self._bus_enabled)
        self._addr_stop_button.setEnabled(False)

    def is_scanning(self) -> bool:
        return self._stop_button.isEnabled() or self._addr_stop_button.isEnabled()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.is_scanning():
            self.scanStopRequested.emit()
        super().closeEvent(event)
