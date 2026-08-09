import time

from PySide6.QtCore import QObject, Signal, Slot

from modbus_connector.backend import ModbusBackend
from modbus_connector.models import (
    ConnectionParams,
    RegisterRow,
    ScanProbe,
    Stats,
    TcpParams,
    format_values,
)


def _describe(params: ConnectionParams) -> str:
    if isinstance(params, TcpParams):
        return f"tcp {params.host}:{params.port}"
    return f"rtu {params.port} @ {params.baudrate}"


class ModbusWorker(QObject):
    connectionChanged = Signal(bool, str)
    readFinished = Signal(int, bool, list, str)
    writeFinished = Signal(int, bool, str)
    scanProgress = Signal(int, int)
    scanHit = Signal(int, list)
    scanFinished = Signal()
    statsUpdated = Signal(object)
    aliveChanged = Signal(bool)
    logLine = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend = ModbusBackend()
        self._scan_stop = False
        self._stats = Stats()

    def _record_stats(self, ok: bool, started: float) -> None:
        self._stats.record(ok, (time.monotonic() - started) * 1000)
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
            self._record_stats(False, started)
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
            self._record_stats(False, started)
            self.logLine.emit(f"✗ write failed: {exc}")
            self.writeFinished.emit(request_id, False, str(exc))
            return
        self._record_stats(True, started)
        self.logLine.emit("← ok")
        self.writeFinished.emit(request_id, True, "")

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
