from PySide6.QtCore import QObject, QTimer, Signal, Slot

from modbus_connector.i18n import tr
from modbus_connector.models import RegisterKind, RtuParams
from modbus_connector.sim_backend import SimBackend, SimTcpParams

DEFAULT_TICK_MS = 1000


def _describe(params: SimTcpParams | RtuParams) -> str:
    if isinstance(params, SimTcpParams):
        return f"tcp {params.host}:{params.port}"
    return f"rtu {params.port} @ {params.baudrate}"


class SimWorker(QObject):
    """QObject-обёртка над SimBackend, живёт в отдельном QThread (снаружи).

    Колбэки backend'а вызываются из потока pymodbus-сервера — привязаны
    напрямую к сигналам (emit из чужого потока безопасен, доставка queued).
    Правила симуляции считает UI-сторона: воркер — только метроном (ticked),
    по тику UI вычисляет правила и зовёт set_values.
    """

    serverChanged = Signal(bool, str)  # ok + описание или текст ошибки
    masterWrote = Signal(str, int, list)  # kind, address, values
    requestLine = Signal(str)  # проброс on_request
    clientChanged = Signal(bool)
    logLine = Signal(str)
    ticked = Signal()  # после каждого тика — UI обновит значения в таблице

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend = SimBackend()
        self._backend.on_master_write = self.masterWrote.emit
        self._backend.on_request = self.requestLine.emit
        self._backend.on_client = self.clientChanged.emit
        # таймер — ребёнок воркера: moveToThread перенесёт его в поток воркера
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(DEFAULT_TICK_MS)
        self._tick_timer.timeout.connect(self.ticked.emit)

    @Slot(object, object)
    def start_server(self, params: SimTcpParams | RtuParams, unit: int | None) -> None:
        desc = _describe(params)
        self.logLine.emit(tr("→ start simulator {desc}", desc=desc))
        try:
            self._backend.start(params, unit)
        except Exception as exc:
            self.logLine.emit(tr("✗ simulator start failed: {exc}", exc=exc))
            self.serverChanged.emit(False, str(exc))
            return
        self._tick_timer.start()
        self.logLine.emit(tr("← simulator running ({desc})", desc=desc))
        self.serverChanged.emit(True, tr("Simulator running ({desc})", desc=desc))

    @Slot()
    def stop_server(self) -> None:
        self._tick_timer.stop()
        try:
            self._backend.stop()
        except Exception as exc:
            self.logLine.emit(tr("✗ simulator stop failed: {exc}", exc=exc))
        self.logLine.emit(tr("→ simulator stopped"))
        # English key: the panel translates it at render time
        self.serverChanged.emit(False, "Stopped")

    @Slot(str, int, list)
    def set_values(self, kind: RegisterKind, address: int, values: list) -> None:
        try:
            self._backend.set_values(kind, address, values)
        except Exception as exc:
            self.logLine.emit(tr("✗ simulator set values failed: {exc}", exc=exc))

    @Slot(str, int, int, result=list)
    def get_values(self, kind: RegisterKind, address: int, count: int) -> list:
        """Вызывать из UI-потока через invokeMethod(BlockingQueuedConnection)."""
        try:
            return list(self._backend.get_values(kind, address, count))
        except Exception as exc:
            self.logLine.emit(tr("✗ simulator get values failed: {exc}", exc=exc))
            return []

    @Slot(int)
    def set_tick_interval(self, ms: int) -> None:
        self._tick_timer.setInterval(max(1, ms))

    @Slot()
    def shutdown(self) -> None:
        """Стоп тикера + backend.stop() (BlockingQueuedConnection при закрытии)."""
        self._tick_timer.stop()
        try:
            self._backend.stop()
        except Exception as exc:
            self.logLine.emit(tr("✗ simulator stop failed: {exc}", exc=exc))
