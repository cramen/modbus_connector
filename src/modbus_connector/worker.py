import time

from pymodbus.exceptions import ConnectionException, ModbusIOException
from PySide6.QtCore import QObject, Signal, Slot

from modbus_connector.backend import ModbusBackend, ModbusExceptionError
from modbus_connector.i18n import tr
from modbus_connector.models import (
    ConnectionParams,
    RegisterKind,
    RegisterRow,
    ScanProbe,
    Stats,
    describe_connection,
    describe_exception,
    format_values,
)


def _classify_error(exc: Exception) -> str:
    if isinstance(exc, ModbusExceptionError):
        return f"exception:{describe_exception(exc.exception_code)}"
    if isinstance(exc, ModbusIOException):
        return "timeout"
    if isinstance(exc, (ConnectionException, ConnectionError, OSError)):
        return "transport"
    return "other"


class ModbusWorker(QObject):
    connectionChanged = Signal(bool, str)
    readFinished = Signal(int, bool, list, str)
    writeFinished = Signal(int, bool, str)
    scanProgress = Signal(int, int)
    scanHit = Signal(int, list)
    scanFinished = Signal()
    readwriteFinished = Signal(int, bool, list, str)
    deviceIdFinished = Signal(int, bool, dict, str)
    diagLoopbackFinished = Signal(int, bool, str)
    diagCountersFinished = Signal(int, bool, dict, str)
    addrScanProgress = Signal(int, int)
    addrScanHit = Signal(int, list)
    addrScanFinished = Signal()
    statsUpdated = Signal(object)
    aliveChanged = Signal(bool)
    trafficLine = Signal(str)
    logLine = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend = ModbusBackend()
        self._backend.traffic_hook = self._on_traffic
        self._scan_stop = False
        self._stats = Stats()

    def _on_traffic(self, direction: str, data: bytes) -> None:
        hex_bytes = " ".join(f"{byte:02x}" for byte in data)
        arrow = "→" if direction == "tx" else "←"
        self.trafficLine.emit(f"{arrow} {direction} {hex_bytes}")

    def _record_stats(self, ok: bool, started: float, exc: Exception | None = None) -> None:
        error_kind = _classify_error(exc) if not ok and exc is not None else None
        self._stats.record(ok, (time.monotonic() - started) * 1000, error_kind)
        self.statsUpdated.emit(self._stats.snapshot())

    @Slot(object)
    def connect_to(self, params: ConnectionParams) -> None:
        self.logLine.emit(tr("→ connect {desc}", desc=describe_connection(params)))
        try:
            self._backend.connect(params)
        except Exception as exc:
            self.logLine.emit(tr("✗ connect failed: {exc}", exc=exc))
            self.connectionChanged.emit(False, str(exc))
            return
        self.logLine.emit(tr("← connected"))
        self.connectionChanged.emit(
            True, tr("Connected ({desc})", desc=describe_connection(params))
        )

    @Slot()
    def disconnect(self) -> None:
        self._scan_stop = True
        try:
            self._backend.disconnect()
        except Exception as exc:
            self.logLine.emit(tr("✗ disconnect failed: {exc}", exc=exc))
        self.logLine.emit(tr("→ disconnect"))
        # English key: the panel translates it at render time
        self.connectionChanged.emit(False, "Disconnected")

    @Slot()
    def check_alive(self) -> None:
        # local client state only — no Modbus traffic on the bus
        self.aliveChanged.emit(self._backend.connected)

    @Slot(int, int, object)
    def read(self, request_id: int, unit: int, row: RegisterRow) -> None:
        self.logLine.emit(
            tr(
                "→ read {kind} unit={unit} addr={address} count={count}",
                kind=row.kind, unit=unit, address=row.address, count=row.count,
            )
        )
        started = time.monotonic()
        try:
            values = self._backend.read(unit, row.kind, row.address, row.count)
        except Exception as exc:
            self._record_stats(False, started, exc)
            self.logLine.emit(tr("✗ read failed: {exc}", exc=exc))
            self.readFinished.emit(request_id, False, [], str(exc))
            return
        self._record_stats(True, started)
        self.logLine.emit(f"← {format_values(values)}")
        self.readFinished.emit(request_id, True, list(values), "")

    @Slot(int, int, object, list)
    def write(self, request_id: int, unit: int, row: RegisterRow, values: list) -> None:
        self.logLine.emit(
            tr(
                "→ write {kind} unit={unit} addr={address} values={values}",
                kind=row.kind, unit=unit, address=row.address,
                values=format_values(values),
            )
        )
        started = time.monotonic()
        try:
            self._backend.write(unit, row.kind, row.address, values)
        except Exception as exc:
            self._record_stats(False, started, exc)
            self.logLine.emit(tr("✗ write failed: {exc}", exc=exc))
            self.writeFinished.emit(request_id, False, str(exc))
            return
        self._record_stats(True, started)
        self.logLine.emit(tr("← ok"))
        self.writeFinished.emit(request_id, True, "")

    @Slot(int, int, int, int, int)
    def mask_write(
        self, request_id: int, unit: int, address: int, and_mask: int, or_mask: int
    ) -> None:
        self.logLine.emit(
            tr(
                "→ mask write unit={unit} addr={address} and=0x{and_mask:04x} "
                "or=0x{or_mask:04x}",
                unit=unit, address=address, and_mask=and_mask, or_mask=or_mask,
            )
        )
        started = time.monotonic()
        try:
            self._backend.mask_write_register(unit, address, and_mask, or_mask)
        except Exception as exc:
            self._record_stats(False, started, exc)
            self.logLine.emit(tr("✗ mask write failed: {exc}", exc=exc))
            self.writeFinished.emit(request_id, False, str(exc))
            return
        self._record_stats(True, started)
        self.logLine.emit(tr("← ok"))
        self.writeFinished.emit(request_id, True, "")

    @Slot(int, int, int, int, int, list)
    def readwrite(
        self,
        request_id: int,
        unit: int,
        read_address: int,
        read_count: int,
        write_address: int,
        values: list,
    ) -> None:
        self.logLine.emit(
            tr(
                "→ read/write unit={unit} read@{read_address} x{read_count} "
                "write@{write_address} values={values}",
                unit=unit, read_address=read_address, read_count=read_count,
                write_address=write_address, values=format_values(values),
            )
        )
        started = time.monotonic()
        try:
            result = self._backend.readwrite_registers(
                unit, read_address, read_count, write_address, values
            )
        except Exception as exc:
            self._record_stats(False, started, exc)
            self.logLine.emit(tr("✗ read/write failed: {exc}", exc=exc))
            self.readwriteFinished.emit(request_id, False, [], str(exc))
            return
        self._record_stats(True, started)
        self.logLine.emit(f"← {format_values(result)}")
        self.readwriteFinished.emit(request_id, True, list(result), "")

    @Slot(int, int)
    def read_device_id(self, request_id: int, unit: int) -> None:
        self.logLine.emit(tr("→ read device id unit={unit}", unit=unit))
        started = time.monotonic()
        try:
            info = self._backend.read_device_identification(unit)
        except Exception as exc:
            self._record_stats(False, started, exc)
            self.logLine.emit(tr("✗ read device id failed: {exc}", exc=exc))
            self.deviceIdFinished.emit(request_id, False, {}, str(exc))
            return
        self._record_stats(True, started)
        self.logLine.emit(tr("← device id: {count} objects", count=len(info)))
        self.deviceIdFinished.emit(request_id, True, info, "")

    @Slot(int, int)
    def diag_loopback(self, request_id: int, unit: int) -> None:
        self.logLine.emit(tr("→ diag loopback unit={unit}", unit=unit))
        started = time.monotonic()
        try:
            echo_ok = self._backend.diag_loopback(unit)
        except Exception as exc:
            self._record_stats(False, started, exc)
            self.logLine.emit(tr("✗ diag loopback failed: {exc}", exc=exc))
            self.diagLoopbackFinished.emit(request_id, False, str(exc))
            return
        self._record_stats(True, started)
        self.logLine.emit(
            tr("← loopback ok") if echo_ok else tr("← loopback mismatch")
        )
        self.diagLoopbackFinished.emit(request_id, echo_ok, "")

    @Slot(int, int)
    def diag_read_counters(self, request_id: int, unit: int) -> None:
        self.logLine.emit(tr("→ diag counters unit={unit}", unit=unit))
        started = time.monotonic()
        try:
            counters = self._backend.diag_counters(unit)
        except Exception as exc:
            self._record_stats(False, started, exc)
            self.logLine.emit(tr("✗ diag counters failed: {exc}", exc=exc))
            self.diagCountersFinished.emit(request_id, False, {}, str(exc))
            return
        self._record_stats(True, started)
        self.logLine.emit(tr("← diag counters: {counters}", counters=counters))
        self.diagCountersFinished.emit(request_id, True, counters, "")

    @Slot(int, int)
    def diag_clear_counters(self, request_id: int, unit: int) -> None:
        self.logLine.emit(tr("→ diag clear counters unit={unit}", unit=unit))
        started = time.monotonic()
        try:
            self._backend.diag_clear_counters(unit)
            counters = self._backend.diag_counters(unit)  # show the cleared state
        except Exception as exc:
            self._record_stats(False, started, exc)
            self.logLine.emit(tr("✗ diag clear counters failed: {exc}", exc=exc))
            self.diagCountersFinished.emit(request_id, False, {}, str(exc))
            return
        self._record_stats(True, started)
        self.logLine.emit(tr("← counters cleared"))
        self.diagCountersFinished.emit(request_id, True, counters, "")

    @Slot(list, int, int)
    def start_scan(self, probes: list[ScanProbe], start: int, end: int) -> None:
        self._scan_stop = False
        total = max(0, end - start + 1)
        self.logLine.emit(
            tr("→ scan units {start}..{end} ({count} probes)",
               start=start, end=end, count=len(probes))
        )
        try:
            for unit, indices in self._backend.scan(probes, start, end, lambda: self._scan_stop):
                self.scanProgress.emit(unit - start + 1, total)
                if indices:
                    self.scanHit.emit(unit, list(indices))
                    self.logLine.emit(
                        tr("← scan hit unit={unit} probes={indices}",
                           unit=unit, indices=list(indices))
                    )
        except Exception as exc:
            self.logLine.emit(tr("✗ scan failed: {exc}", exc=exc))
        self.scanProgress.emit(total, total)
        self.scanFinished.emit()
        self.logLine.emit(
            tr("← scan stopped") if self._scan_stop else tr("← scan finished")
        )

    @Slot()
    def stop_scan(self) -> None:
        self._scan_stop = True

    @Slot(int, object, int, int)
    def start_addr_scan(self, unit: int, kind: RegisterKind, start: int, end: int) -> None:
        # single _scan_stop flag for both scans: only one scan runs at a time,
        # stop_scan stops whichever is active
        self._scan_stop = False
        total = max(0, end - start + 1)
        self.logLine.emit(
            tr("→ scan addresses {kind} unit={unit} {start}..{end}",
               kind=kind, unit=unit, start=start, end=end)
        )
        try:
            for address, values in self._backend.scan_addresses(
                unit, kind, start, end, lambda: self._scan_stop
            ):
                self.addrScanHit.emit(address, list(values))
                self.logLine.emit(
                    tr("← addr scan hit {kind}@{address} = {values}",
                       kind=kind, address=address, values=format_values(values))
                )
                self.addrScanProgress.emit(address - start + 1, total)
        except Exception as exc:
            self.logLine.emit(tr("✗ address scan failed: {exc}", exc=exc))
        self.addrScanProgress.emit(total, total)
        self.addrScanFinished.emit()
        self.logLine.emit(
            tr("← address scan stopped") if self._scan_stop
            else tr("← address scan finished")
        )
