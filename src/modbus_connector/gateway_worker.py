from PySide6.QtCore import QObject, Signal, Slot

from modbus_connector.gateway_backend import (
    GatewayBackend,
    GatewayListenParams,
    describe_gateway,
)
from modbus_connector.i18n import tr
from modbus_connector.models import ConnectionParams


class GatewayWorker(QObject):
    """QObject-обёртка над GatewayBackend, живёт в отдельном QThread (снаружи).

    Хуки backend'а вызываются из потока сервера — привязаны напрямую
    к сигналам (emit из чужого потока безопасен, доставка queued).
    logLine несёт и строки транзакций ("-> unit 5 read ..." / "<- ok (N ms)"),
    и служебные сообщения воркера.
    """

    gatewayChanged = Signal(bool, str)  # ok + описание или текст ошибки
    clientChanged = Signal(bool)  # подключился/отключился TCP-клиент
    logLine = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend = GatewayBackend()
        self._backend.on_request = self.logLine.emit
        self._backend.on_error = self._on_error
        self._backend.on_client = self.clientChanged.emit

    def _on_error(self, message: str) -> None:
        self.logLine.emit(tr("✗ gateway: {message}", message=message))

    @Slot(object, object, object)
    def start_gateway(
        self,
        listen: GatewayListenParams,
        target: ConnectionParams,
        units: set[int] | None,
    ) -> None:
        # dataclass-параметры не маршаллятся через Q_ARG — вызывать сигналом
        desc = describe_gateway(listen, target)
        self.logLine.emit(tr("→ start gateway {desc}", desc=desc))
        try:
            self._backend.start(listen, target, units)
        except Exception as exc:
            self.logLine.emit(tr("✗ gateway start failed: {exc}", exc=exc))
            self.gatewayChanged.emit(False, str(exc))
            return
        self.logLine.emit(tr("← gateway running ({desc})", desc=desc))
        self.gatewayChanged.emit(True, tr("Gateway running ({desc})", desc=desc))

    @Slot()
    def stop_gateway(self) -> None:
        try:
            self._backend.stop()
        except Exception as exc:
            self.logLine.emit(tr("✗ gateway stop failed: {exc}", exc=exc))
        self.logLine.emit(tr("→ gateway stopped"))
        # English key: the panel translates it at render time
        self.gatewayChanged.emit(False, "Stopped")

    @Slot()
    def shutdown(self) -> None:
        """backend.stop() (BlockingQueuedConnection при закрытии сессии)."""
        try:
            self._backend.stop()
        except Exception as exc:
            self.logLine.emit(tr("✗ gateway stop failed: {exc}", exc=exc))
