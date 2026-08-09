import time

from pymodbus.exceptions import ConnectionException, ModbusIOException
from PySide6.QtCore import QObject, Signal, Slot

from modbus_connector.backend import ModbusBackend, ModbusExceptionError
from modbus_connector.models import (
    ConnectionParams,
    RegisterKind,
    RegisterRow,
    ScanProbe,
    Stats,
    TcpParams,
    describe_exception,
    format_values,
)


def _describe(params: ConnectionParams) -> str:
    if isinstance(params, TcpParams):
        return f"tcp {params.host}:{params.port}"
    return f"rtu {params.port} @ {params.baudrate}"


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
    addrScanProgress = Signal(int, int)
    addrScanHit = Signal(int)
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
        self.logLine.emit(f"→ connect {_describe(params)}")
        try:
            self._backend.connect(params)
        except Exception as exc:
            self.logLine.emit(f"✗ connect failed: {exc}")
            self.connectionChanged.emit(False, str(exc))
            return
        self.logLine.emit("← connected")
        self.connectionChanged.emit(True, f"Connected ({_describe(params)})")

    @Slot()
    def disconnect(self) -> None:
        self._scan_stop = True
        try:
            self._backend.disconnect()
        except Exception as exc:
            self.logLine.emit(f"✗ disconnect failed: {exc}")
        self.logLine.emit("→ disconnect")
        self.connectionChanged.emit(False, "Disconnected")

    @Slot()
    def check_alive(self) -> None:
        # local client state only — no Modbus traffic on the bus
        self.aliveChanged.emit(self._backend.connected)

    @Slot(int, int, object)
    def read(self, request_id: int, unit: int, row: RegisterRow) -> None:
        self.logLine.emit(f"→ read {row.kind} unit={unit} addr={row.address} count={row.count}")
        started = time.monotonic()
        try:
            values = self._backend.read(unit, row.kind, row.address, row.count)
        except Exception as exc:
            self._record_stats(False, started, exc)
            self.logLine.emit(f"✗ read failed: {exc}")
            self.readFinished.emit(request_id, False, [], str(exc))
            return
        self._record_stats(True, started)
        self.logLine.emit(f"← {format_values(values)}")
        self.readFinished.emit(request_id, True, list(values), "")

    @Slot(int, int, object, list)
    def write(self, request_id: int, unit: int, row: RegisterRow, values: list) -> None:
        self.logLine.emit(
            f"→ write {row.kind} unit={unit} addr={row.address} values={format_values(values)}"
        )
        started = time.monotonic()
        try:
            self._backend.write(unit, row.kind, row.address, values)
        except Exception as exc:
            self._record_stats(False, started, exc)
            self.logLine.emit(f"✗ write failed: {exc}")
            self.writeFinished.emit(request_id, False, str(exc))
            return
        self._record_stats(True, started)
        self.logLine.emit("← ok")
        self.writeFinished.emit(request_id, True, "")

    @Slot(int, int, int, int, int)
    def mask_write(
        self, request_id: int, unit: int, address: int, and_mask: int, or_mask: int
    ) -> None:
        self.logLine.emit(
            f"→ mask write unit={unit} addr={address} "
            f"and=0x{and_mask:04x} or=0x{or_mask:04x}"
        )
        started = time.monotonic()
        try:
            self._backend.mask_write_register(unit, address, and_mask, or_mask)
        except Exception as exc:
            self._record_stats(False, started, exc)
            self.logLine.emit(f"✗ mask write failed: {exc}")
            self.writeFinished.emit(request_id, False, str(exc))
            return
        self._record_stats(True, started)
        self.logLine.emit("← ok")
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
            f"→ read/write unit={unit} read@{read_address} x{read_count} "
            f"write@{write_address} values={format_values(values)}"
        )
        started = time.monotonic()
        try:
            result = self._backend.readwrite_registers(
                unit, read_address, read_count, write_address, values
            )
        except Exception as exc:
            self._record_stats(False, started, exc)
            self.logLine.emit(f"✗ read/write failed: {exc}")
            self.readwriteFinished.emit(request_id, False, [], str(exc))
            return
        self._record_stats(True, started)
        self.logLine.emit(f"← {format_values(result)}")
        self.readwriteFinished.emit(request_id, True, list(result), "")

    @Slot(list, int, int)
    def start_scan(self, probes: list[ScanProbe], start: int, end: int) -> None:
        self._scan_stop = False
        total = max(0, end - start + 1)
        self.logLine.emit(f"→ scan units {start}..{end} ({len(probes)} probes)")
        try:
            for unit, indices in self._backend.scan(probes, start, end, lambda: self._scan_stop):
                self.scanProgress.emit(unit - start + 1, total)
                if indices:
                    self.scanHit.emit(unit, list(indices))
                    self.logLine.emit(f"← scan hit unit={unit} probes={list(indices)}")
        except Exception as exc:
            self.logLine.emit(f"✗ scan failed: {exc}")
        self.scanProgress.emit(total, total)
        self.scanFinished.emit()
        self.logLine.emit("← scan stopped" if self._scan_stop else "← scan finished")

    @Slot()
    def stop_scan(self) -> None:
        self._scan_stop = True

    @Slot(int, object, int, int)
    def start_addr_scan(self, unit: int, kind: RegisterKind, start: int, end: int) -> None:
        # single _scan_stop flag for both scans: only one scan runs at a time,
        # stop_scan stops whichever is active
        self._scan_stop = False
        total = max(0, end - start + 1)
        self.logLine.emit(f"→ scan addresses {kind} unit={unit} {start}..{end}")
        try:
            for address in self._backend.scan_addresses(
                unit, kind, start, end, lambda: self._scan_stop
            ):
                self.addrScanHit.emit(address)
                self.logLine.emit(f"← addr scan hit {kind}@{address}")
                self.addrScanProgress.emit(address - start + 1, total)
        except Exception as exc:
            self.logLine.emit(f"✗ address scan failed: {exc}")
        self.addrScanProgress.emit(total, total)
        self.addrScanFinished.emit()
        self.logLine.emit(
            "← address scan stopped" if self._scan_stop else "← address scan finished"
        )
