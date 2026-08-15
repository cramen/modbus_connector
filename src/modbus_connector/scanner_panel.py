import itertools
from collections.abc import Callable
from typing import get_args

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
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

from modbus_connector.connection_panel import DEVICE_ID_NAMES
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
    rowsAddRequested = Signal(list)  # registers-scan hits → the registers table
    deviceIdRequested = Signal(int, int)  # request id, unit

    def __init__(
        self,
        request_id_provider: Callable[[], int] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        # tests construct the panel bare: a private counter is fine there;
        # SessionWidget passes the shared session counter
        self._next_request_id = request_id_provider or itertools.count(10_000).__next__
        self._bus_enabled = False  # no bus access until a connection is up
        self._addr_scan_unit = 1
        self._addr_scan_kind = "holding_registers"
        self._device_id_request = -1
        self._device_id_list: QListWidget | None = None

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
        self._results.itemSelectionChanged.connect(self._sync_device_id_button)
        self._device_id_button = QPushButton("Device ID…")
        self._device_id_button.setEnabled(False)
        self._device_id_button.setToolTip(
            "Read the selected unit's identification (function 0x2B/0x0E)"
        )
        self._device_id_button.clicked.connect(self._on_device_id_clicked)

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
        self._addr_results.itemChanged.connect(self._sync_add_rows_button)
        self._addr_all_button = QPushButton("All")
        self._addr_all_button.clicked.connect(
            lambda: self._set_all_hits(Qt.CheckState.Checked)
        )
        self._addr_none_button = QPushButton("None")
        self._addr_none_button.clicked.connect(
            lambda: self._set_all_hits(Qt.CheckState.Unchecked)
        )
        self._add_rows_button = QPushButton("Add selected to table")
        self._add_rows_button.setEnabled(False)
        self._add_rows_button.setToolTip(
            "Add one row per checked address to the registers table"
        )
        self._add_rows_button.clicked.connect(self._on_add_rows_clicked)

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
        unit_buttons = QHBoxLayout()
        unit_buttons.addStretch(1)
        unit_buttons.addWidget(self._device_id_button)
        layout.addLayout(unit_buttons)
        self._addr_section_label = QLabel("Registers scan:")
        layout.addWidget(self._addr_section_label)
        layout.addLayout(addr_layout)
        layout.addWidget(self._addr_progress)
        layout.addWidget(self._addr_results)
        addr_buttons = QHBoxLayout()
        addr_buttons.addWidget(self._addr_all_button)
        addr_buttons.addWidget(self._addr_none_button)
        addr_buttons.addStretch(1)
        addr_buttons.addWidget(self._add_rows_button)
        layout.addLayout(addr_buttons)

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
        self._sync_add_rows_button()
        self._sync_device_id_button()

    @Slot()
    def handle_scan_finished(self) -> None:
        self._start_button.setEnabled(self._bus_enabled)
        self._stop_button.setEnabled(False)

    @Slot()
    def _on_addr_start(self) -> None:
        if self._addr_from.value() > self._addr_to.value():
            self._addr_from.setValue(self._addr_to.value())  # clamp inverted range
        self._addr_results.clear()
        self._addr_scan_unit = self._addr_unit.value()  # "Add to table" uses these
        self._addr_scan_kind = self._addr_kind.currentText()
        self._sync_add_rows_button()
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
        item = QListWidgetItem(f"0x{address:04X} ({address})")
        item.setData(Qt.ItemDataRole.UserRole, address)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)  # new hits join selected
        self._addr_results.addItem(item)
        self._sync_add_rows_button()  # addItem does not emit itemChanged

    def _checked_hits(self) -> list[int]:
        hits = []
        for row in range(self._addr_results.count()):
            item = self._addr_results.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                hits.append(int(item.data(Qt.ItemDataRole.UserRole)))
        return hits

    def _set_all_hits(self, state: Qt.CheckState) -> None:
        for row in range(self._addr_results.count()):
            self._addr_results.item(row).setCheckState(state)

    def _sync_add_rows_button(self) -> None:
        self._add_rows_button.setEnabled(
            self._bus_enabled and bool(self._checked_hits())
        )

    @Slot()
    def _on_add_rows_clicked(self) -> None:
        rows = [
            {
                "kind": self._addr_scan_kind,
                "address": address,
                "count": 1,
                "unit_id": self._addr_scan_unit,
            }
            for address in self._checked_hits()
        ]
        self.rowsAddRequested.emit(rows)

    def _sync_device_id_button(self) -> None:
        self._device_id_button.setEnabled(
            self._bus_enabled
            and self._results.currentItem() is not None
            and self._device_id_request < 0  # one query at a time
        )

    @Slot()
    def _on_device_id_clicked(self) -> None:
        item = self._results.currentItem()
        unit = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(unit, int) or not self._bus_enabled:
            return
        dialog = QDialog(self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setWindowTitle(f"Unit {unit} — device identification (0x2B/0x0E)")
        list_widget = QListWidget()
        list_widget.addItem("Reading…")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout = QVBoxLayout(dialog)
        layout.addWidget(list_widget)
        layout.addWidget(buttons)
        dialog.finished.connect(self._on_device_id_dialog_closed)
        self._device_id_list = list_widget
        self._device_id_request = self._next_request_id()
        self.deviceIdRequested.emit(self._device_id_request, unit)
        self._sync_device_id_button()
        dialog.show()  # non-modal: the scanner stays usable while reading

    def _on_device_id_dialog_closed(self, _result: int) -> None:
        self._device_id_list = None
        self._device_id_request = -1
        self._sync_device_id_button()

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
        elif not info:
            list_widget.addItem("(device reported no objects)")
        else:
            for object_id, value in sorted(info.items()):
                label = DEVICE_ID_NAMES.get(object_id, f"object 0x{object_id:02X}")
                list_widget.addItem(f"{label}: {value}")
        self._device_id_request = -1  # answered; the dialog stays open
        self._sync_device_id_button()

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
