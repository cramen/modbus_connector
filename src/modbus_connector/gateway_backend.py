import asyncio
import logging
import socket
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from pymodbus.datastore import ModbusBaseSlaveContext, ModbusServerContext
from pymodbus.device import ModbusDeviceIdentification
from pymodbus.framer import Framer
from pymodbus.server import ModbusSerialServer, ModbusTcpServer
from pymodbus.transport import ModbusProtocol

from .backend import ModbusBackend
from .models import ConnectionParams, RegisterKind, RtuParams, describe_connection

logger = logging.getLogger(__name__)

RequestHook = Callable[[str], None]  # человекочитаемая строка транзакции шлюза
ErrorHook = Callable[[str], None]  # сообщение об ошибке сервера
ClientHook = Callable[[bool], None]  # подключился/отключился TCP-клиент


@dataclass(frozen=True)
class GatewayTcpListenParams:
    host: str = "0.0.0.0"
    port: int = 1502


@dataclass(frozen=True)
class GatewayRtuOverTcpListenParams(GatewayTcpListenParams):
    """RTU-фрейминг поверх TCP (MBAP нет): для мастеров «Modbus RTU over TCP»."""


# serial-вариант listen-стороны — models.RtuParams (как у SimBackend)
GatewayListenParams = GatewayTcpListenParams | RtuParams


def describe_gateway(listen: GatewayListenParams, target: ConnectionParams) -> str:
    """Подпись шлюза («gw tcp 0.0.0.0:5020 -> rtu /dev/cu.usb @ 9600»)."""
    if isinstance(listen, GatewayRtuOverTcpListenParams):
        listen_desc = f"rtu over tcp {listen.host}:{listen.port}"
    elif isinstance(listen, GatewayTcpListenParams):
        listen_desc = f"tcp {listen.host}:{listen.port}"
    else:
        listen_desc = f"rtu {listen.port} @ {listen.baudrate}"
    return f"gw {listen_desc} -> {describe_connection(target)}"


# fc -> область: read 1-4, write 5/6/15/16; PDU 0x16 (mask write) и 0x17
# (read/write) раскрываются в get+set на holding, поэтому 22/23 тоже нужны;
# 5/6 участвуют ещё и в read-back записи (PDU перечитывает значение после set)
_FC_TO_KIND: dict[int, RegisterKind] = {
    1: "coils",
    2: "discrete_inputs",
    3: "holding_registers",
    4: "input_registers",
    5: "coils",
    6: "holding_registers",
    15: "coils",
    16: "holding_registers",
    22: "holding_registers",
    23: "holding_registers",
}

_WRITABLE_KINDS: frozenset[RegisterKind] = frozenset({"coils", "holding_registers"})


class _ForwardingContext(ModbusBaseSlaveContext):
    """Datastore-заглушка: каждый запрос транслируется в ModbusBackend.

    PDU вызывают async_getValues/async_setValues; sync-вызов backend выполняется
    в single-thread executor'е — это сериализует запросы к target (у backend
    нет локов) и не блокирует event loop сервера на время таймаута target.
    Любая ошибка backend пробрасывается — сервер отвечает Slave Failure (0x04).
    """

    def __init__(
        self,
        backend: ModbusBackend,
        unit: int,
        executor: ThreadPoolExecutor,
        on_request: RequestHook,
    ) -> None:
        self._backend = backend
        self._unit = unit
        self._executor = executor
        self._on_request = on_request

    def validate(self, fc_as_hex: int, address: int, count: int = 1) -> bool:
        return True  # карта target заранее неизвестна — ошибки ловит backend

    async def async_getValues(
        self, fc_as_hex: int, address: int, count: int = 1
    ) -> list[int | bool]:
        kind = _FC_TO_KIND[fc_as_hex]
        self._on_request(f"-> unit {self._unit} read {kind}@{address} count {count}")
        started = time.monotonic()
        try:
            values = await asyncio.get_running_loop().run_in_executor(
                self._executor, self._backend.read, self._unit, kind, address, count
            )
        except Exception as exc:
            self._on_request(f"<- error: {exc}")
            raise
        self._on_request(f"<- ok ({round((time.monotonic() - started) * 1000)} ms)")
        return values

    async def async_setValues(
        self, fc_as_hex: int, address: int, values: list[int | bool]
    ) -> None:
        kind = _FC_TO_KIND[fc_as_hex]
        if kind not in _WRITABLE_KINDS:
            raise ValueError(f"Запись в {kind} не поддерживается (fc {fc_as_hex})")
        self._on_request(
            f"-> unit {self._unit} write {kind}@{address} values {list(values)}"
        )
        started = time.monotonic()
        try:
            await asyncio.get_running_loop().run_in_executor(
                self._executor,
                self._backend.write,
                self._unit,
                kind,
                address,
                list(values),
            )
        except Exception as exc:
            self._on_request(f"<- error: {exc}")
            raise
        self._on_request(f"<- ok ({round((time.monotonic() - started) * 1000)} ms)")


class GatewayBackend:
    """Modbus-шлюз без Qt: listen-сервер (pymodbus) -> sync-клиент ModbusBackend.

    on_request(line) — строки транзакций "-> unit 5 read ..." / "<- ok (N ms)" /
    "<- error: ..."; on_error(message) — ошибка потока сервера;
    on_client(connected) — подключение/отключение TCP-клиентов.
    Хуки вызываются из потока сервера, их исключения гасятся.
    """

    def __init__(self) -> None:
        self.on_request: RequestHook | None = None
        self.on_error: ErrorHook | None = None
        self.on_client: ClientHook | None = None
        self._backend = ModbusBackend()
        self._executor: ThreadPoolExecutor | None = None
        self._server: ModbusTcpServer | ModbusSerialServer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(
        self,
        listen: GatewayListenParams,
        target: ConnectionParams,
        units: set[int] | None = None,
    ) -> None:
        """Подключиться к target, запустить listen-сервер и дождаться готовности.

        units=None — обслуживать unit 1..247, units={...} — только перечисленные
        (остальным отвечаем Gateway No Response). Повторный start без stop —
        RuntimeError; занятый порт / недоступный target — ConnectionError.
        """
        if self._thread is not None:
            raise RuntimeError("Шлюз уже запущен: сначала вызовите stop()")
        unit_ids = sorted(units) if units is not None else list(range(1, 248))
        for unit in unit_ids:
            if not 1 <= unit <= 247:
                raise ValueError(f"unit вне диапазона 1..247: {unit}")
        description = describe_gateway(listen, target)
        if isinstance(listen, GatewayTcpListenParams):
            self._check_tcp_port(listen)
        self._backend.connect(target)  # ConnectionError с причиной, если target недоступен
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="modbus-gw")
        context = ModbusServerContext(
            slaves={
                unit: _ForwardingContext(self._backend, unit, executor, self._emit_request)
                for unit in unit_ids
            },
            single=False,
        )
        identity = ModbusDeviceIdentification(
            info={
                0x00: "ModbusConnector",  # VendorName
                0x01: "GW",  # ProductCode
                0x05: "ModbusConnector gateway",  # ModelName
            }
        )
        loop = asyncio.new_event_loop()
        self._loop = loop
        self._server = None
        self._executor = executor
        thread = threading.Thread(
            target=self._run, args=(listen, context, identity, loop), daemon=True
        )
        self._thread = thread
        thread.start()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            server = self._server
            if server is not None and server.transport is not None:
                logger.info("Шлюз запущен: %s", description)
                return
            if not thread.is_alive():
                break
            time.sleep(0.02)
        self._stop_server()
        raise ConnectionError(f"Не удалось запустить шлюз ({description})")

    def stop(self) -> None:
        """Остановить сервер, executor и отключиться от target."""
        if self._thread is None:
            return
        self._stop_server()

    def _stop_server(self) -> None:
        server, loop, thread = self._server, self._loop, self._thread
        if server is not None and loop is not None and not loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(server.shutdown(), loop).result(timeout=5.0)
            except Exception:
                logger.exception("Ошибка остановки сервера шлюза")
        if thread is not None:
            thread.join(timeout=5.0)
        self._server = None
        self._loop = None
        self._thread = None
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None
        self._backend.disconnect()

    @staticmethod
    def _check_tcp_port(params: GatewayTcpListenParams) -> None:
        # listen() в pymodbus глотает OSError занятого порта (сервер просто
        # никогда не становится готов), поэтому проверяем bind заранее
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((params.host, params.port))
            except OSError as exc:
                raise ConnectionError(
                    f"Не удалось запустить шлюз на {params.host}:{params.port}: {exc}"
                ) from exc

    def _run(
        self,
        listen: GatewayListenParams,
        context: ModbusServerContext,
        identity: ModbusDeviceIdentification,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        asyncio.set_event_loop(loop)

        async def serve() -> None:
            # сервер надо создавать внутри корутины: ModbusBaseServer.__init__
            # требует running event loop (asyncio.get_running_loop())
            server = self._make_server(listen, context, identity)
            self._server = server
            await server.serve_forever()

        try:
            loop.run_until_complete(serve())
        except Exception as exc:
            logger.exception("Ошибка потока шлюза")
            self._emit_error(str(exc))
        finally:
            loop.close()

    def _make_server(
        self,
        listen: GatewayListenParams,
        context: ModbusServerContext,
        identity: ModbusDeviceIdentification,
    ) -> ModbusTcpServer | ModbusSerialServer:
        if isinstance(listen, GatewayTcpListenParams):
            framer = (
                Framer.RTU if isinstance(listen, GatewayRtuOverTcpListenParams) else Framer.SOCKET
            )
            return _GatewayTcpServer(
                context,
                framer=framer,
                identity=identity,
                address=(listen.host, listen.port),
                owner=self,
            )
        return ModbusSerialServer(
            context,
            framer=Framer.RTU,
            identity=identity,
            port=listen.port,
            baudrate=listen.baudrate,
            bytesize=listen.bytesize,
            parity=listen.parity,
            stopbits=listen.stopbits,
            timeout=listen.timeout,
        )

    def _emit_request(self, line: str) -> None:
        hook = self.on_request
        if hook is None:
            return
        try:
            hook(line)
        except Exception:  # failing hook must never kill the server loop
            logger.exception("Ошибка в on_request")

    def _emit_error(self, message: str) -> None:
        hook = self.on_error
        if hook is None:
            return
        try:
            hook(message)
        except Exception:
            logger.exception("Ошибка в on_error")

    def _emit_client(self, connected: bool) -> None:
        hook = self.on_client
        if hook is None:
            return
        try:
            hook(connected)
        except Exception:
            logger.exception("Ошибка в on_client")


class _GatewayTcpServer(ModbusTcpServer):
    """TCP-сервер с колбэком подключения/отключения клиентов (on_client)."""

    def __init__(self, *args: Any, owner: GatewayBackend, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._owner = owner

    def callback_new_connection(self) -> ModbusProtocol:
        handler = super().callback_new_connection()
        owner = self._owner
        original_connected = handler.callback_connected
        original_disconnected = handler.callback_disconnected

        def connected() -> None:
            original_connected()
            owner._emit_client(True)

        def disconnected(exc: Exception | None) -> None:
            original_disconnected(exc)
            owner._emit_client(False)

        handler.callback_connected = connected
        handler.callback_disconnected = disconnected
        return handler
