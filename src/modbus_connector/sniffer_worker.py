from PySide6.QtCore import QObject, Signal, Slot

from modbus_connector.i18n import tr
from modbus_connector.models import RtuParams
from modbus_connector.sniffer_backend import (
    SniffedFrame,
    SnifferBackend,
    describe_sniffer,
    format_frame,
)


class SnifferWorker(QObject):
    """QObject-обёртка над SnifferBackend, живёт в отдельном QThread (снаружи).

    Колбэки backend'а вызываются из потока чтения порта — привязаны напрямую
    к сигналам (emit из чужого потока безопасен, доставка queued).
    frameLine — все кадры одной строкой (общий лог сессии), frameForUnit —
    тот же кадр с unit-адресом (per-unit логи вкладок панели).
    """

    sniffingChanged = Signal(bool, str)  # ok + описание или текст ошибки
    valuesChanged = Signal(int, str, int, list)  # unit, kind, address, values
    frameLine = Signal(str)  # все кадры подряд
    frameForUnit = Signal(int, str)  # unit + строка кадра
    logLine = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend = SnifferBackend()
        self._backend.on_values = self.valuesChanged.emit
        self._backend.on_frame = self.frameLine.emit
        self._backend.on_decoded_frame = self._on_decoded_frame
        self._backend.on_error = self._on_error

    def _on_decoded_frame(self, frame: SniffedFrame) -> None:
        self.frameForUnit.emit(frame.unit, format_frame(frame))

    def _on_error(self, message: str) -> None:
        self.logLine.emit(tr("✗ sniffer: {message}", message=message))

    @Slot(object)
    def start_sniffing(self, params: RtuParams) -> None:
        # dataclass-параметры не маршаллятся через Q_ARG — вызывать сигналом
        desc = describe_sniffer(params)
        self.logLine.emit(tr("→ start sniffer {desc}", desc=desc))
        try:
            self._backend.start(params)
        except Exception as exc:
            self.logLine.emit(tr("✗ sniffer start failed: {exc}", exc=exc))
            self.sniffingChanged.emit(False, str(exc))
            return
        self.logLine.emit(tr("← sniffer listening ({desc})", desc=desc))
        self.sniffingChanged.emit(True, tr("Listening ({desc})", desc=desc))

    @Slot()
    def stop_sniffing(self) -> None:
        try:
            self._backend.stop()
        except Exception as exc:
            self.logLine.emit(tr("✗ sniffer stop failed: {exc}", exc=exc))
        self.logLine.emit(tr("→ sniffer stopped"))
        # English key: the panel translates it at render time
        self.sniffingChanged.emit(False, "Stopped")

    @Slot()
    def shutdown(self) -> None:
        """backend.stop() (BlockingQueuedConnection при закрытии сессии)."""
        try:
            self._backend.stop()
        except Exception as exc:
            self.logLine.emit(tr("✗ sniffer stop failed: {exc}", exc=exc))
