import asyncio
import logging
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusServerContext,
    ModbusSlaveContext,
)
from pymodbus.device import ModbusDeviceIdentification
from pymodbus.framer import Framer
from pymodbus.pdu import ModbusPDU
from pymodbus.server import ModbusSerialServer, ModbusTcpServer
from pymodbus.transport import ModbusProtocol

from .models import RegisterKind, RtuParams

logger = logging.getLogger(__name__)

BLOCK_SIZE = 10000  # адресов на область (0..9999), начальные значения 0/False

# область -> функциональный код datastore (ModbusSlaveContext.decode в pymodbus
# маппит fc на блок: 1->coils, 2->discrete inputs, 3->holding, 4->input)
_FX_CODES: dict[RegisterKind, int] = {
    "coils": 1,
    "discrete_inputs": 2,
    "holding_registers": 3,
    "input_registers": 4,
}

MasterWriteHook = Callable[[RegisterKind, int, list], None]  # (kind, address, values)
RequestHook = Callable[[str], None]  # человекочитаемая строка запроса мастера
ClientHook = Callable[[bool], None]  # True = клиент подключился, False = отключился

_FC_NAMES = {
    1: "read coils",
    2: "read discrete_inputs",
    3: "read holding_registers",
    4: "read input_registers",
    5: "write coil",
    6: "write register",
    8: "diagnostics",
    15: "write coils",
    16: "write registers",
    22: "mask write register",
    23: "read/write registers",
    43: "read device id",
}


@dataclass(frozen=True)
class SimTcpParams:
    host: str = "127.0.0.1"
    port: int = 1502


def describe_sim(params: "SimTcpParams | RtuParams") -> str:
    """Короткая подпись симулятора («sim tcp host:port») для заголовка вкладки."""
    if isinstance(params, SimTcpParams):
        return f"sim tcp {params.host}:{params.port}"
    return f"sim rtu {params.port}"


def _format_request(request: ModbusPDU) -> str:
    """Человекочитаемая строка запроса: «read holding_registers unit=1 @0 x4»."""
    fc = int(request.function_code)
    name = _FC_NAMES.get(fc, f"function 0x{fc:02X}")
    parts = [f"{name} unit={request.slave_id}"]
    address = getattr(request, "address", None)
    if address is not None:
        parts.append(f"@{address}")
    count = getattr(request, "count", None)
    if count is not None:
        parts.append(f"x{count}")
    value = getattr(request, "value", None)
    if value is not None:
        parts.append(f"value={value}")
    values = getattr(request, "values", None)
    if values is not None:
        parts.append(f"values={list(values)}")
    return " ".join(parts)


class _SimDataBlock(ModbusSequentialDataBlock):
    """Блок datastore с хуком записи мастера.

    setValues вызывается контекстом на все функции записи (5/6/15/16, а также
    mask write 0x16 и read/write 0x17), поэтому хук видит любую запись мастера.
    """

    def __init__(self, kind: RegisterKind, size: int = BLOCK_SIZE) -> None:
        self.kind = kind
        self.on_write: MasterWriteHook | None = None
        initial: int | bool = False if kind in ("coils", "discrete_inputs") else 0
        super().__init__(0, [initial] * size)

    def setValues(self, address: int, values: list[int | bool]) -> None:
        super().setValues(address, values)
        hook = self.on_write
        if hook is not None:
            hook(self.kind, address, list(values))


class SimBackend:
    """Modbus slave-сервер (TCP или RTU) без Qt, serve_forever в отдельном потоке.

    on_master_write(kind, address, values) — запись мастера в coils/holding;
    on_request(line) — человекочитаемые строки всех запросов мастера
    (request_tracer pymodbus); on_client(connected) — подключение/отключение
    TCP-клиентов. Хуки вызываются из потока сервера, их исключения гасятся.
    """

    def __init__(self) -> None:
        self._blocks: dict[RegisterKind, _SimDataBlock] = {
            kind: _SimDataBlock(kind) for kind in _FX_CODES
        }
        for block in self._blocks.values():
            block.on_write = self._emit_master_write
        self._slave = ModbusSlaveContext(
            di=self._blocks["discrete_inputs"],
            co=self._blocks["coils"],
            hr=self._blocks["holding_registers"],
            ir=self._blocks["input_registers"],
            zero_mode=True,
        )
        self.on_master_write: MasterWriteHook | None = None
        self.on_request: RequestHook | None = None
        self.on_client: ClientHook | None = None
        self._server: ModbusTcpServer | ModbusSerialServer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, params: SimTcpParams | RtuParams, unit: int | None = None) -> None:
        """Запустить сервер и дождаться готовности (прослушивания порта).

        unit=None — отвечать на любой unit id, unit=N — только на N.
        Повторный start без stop — RuntimeError; занятый порт / нет
        serial-порта — ConnectionError.
        """
        if self._thread is not None:
            raise RuntimeError("Симулятор уже запущен: сначала вызовите stop()")
        if unit is not None and not 1 <= unit <= 247:
            raise ValueError(f"unit вне диапазона 1..247: {unit}")
        if unit is None:
            context = ModbusServerContext(slaves=self._slave, single=True)
        else:
            context = ModbusServerContext(slaves={unit: self._slave}, single=False)
        identity = ModbusDeviceIdentification(
            info={
                0x00: "ModbusConnector",  # VendorName
                0x01: "SIM",  # ProductCode
                0x05: "ModbusConnector simulator",  # ModelName
            }
        )
        if isinstance(params, SimTcpParams):
            description = f"tcp {params.host}:{params.port}"
            self._check_tcp_port(params)
        else:
            description = f"rtu {params.port} @ {params.baudrate}"
        loop = asyncio.new_event_loop()
        self._loop = loop
        self._server = None
        thread = threading.Thread(
            target=self._run, args=(params, context, identity, loop), daemon=True
        )
        self._thread = thread
        thread.start()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            server = self._server
            if server is not None and server.transport is not None:
                logger.info("Симулятор запущен: %s", description)
                return
            if not thread.is_alive():
                break
            time.sleep(0.02)
        self._abort_start()
        raise ConnectionError(f"Не удалось запустить симулятор ({description})")

    def _abort_start(self) -> None:
        """Снять недоживший до готовности сервер после неудачного start()."""
        server, loop, thread = self._server, self._loop, self._thread
        if server is not None and loop is not None and not loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(server.shutdown(), loop).result(timeout=5.0)
            except Exception:
                logger.exception("Ошибка остановки недозапущенного симулятора")
        if thread is not None:
            thread.join(timeout=5.0)
        self._server = None
        self._loop = None
        self._thread = None

    def stop(self) -> None:
        """Остановить сервер (shutdown + join потока с таймаутом)."""
        if self._thread is None:
            return
        self._abort_start()

    def set_values(self, kind: RegisterKind, address: int, values: list[int | bool]) -> None:
        """Записать значения в область симулятора (без хука on_master_write)."""
        block = self._require_block(kind)
        self._check_range(address, len(values))
        # базовый метод напрямую: хук on_master_write — только для записей мастера
        ModbusSequentialDataBlock.setValues(block, address, list(values))

    def get_values(self, kind: RegisterKind, address: int, count: int) -> list[int | bool]:
        """Прочитать значения области симулятора."""
        block = self._require_block(kind)
        self._check_range(address, count)
        return list(ModbusSequentialDataBlock.getValues(block, address, count))

    def _require_block(self, kind: RegisterKind) -> _SimDataBlock:
        block = self._blocks.get(kind)
        if block is None:
            allowed = ", ".join(_FX_CODES)
            raise ValueError(f"Неизвестная область {kind!r} (доступны: {allowed})")
        return block

    @staticmethod
    def _check_range(address: int, count: int) -> None:
        if not 0 <= address < BLOCK_SIZE or count < 1 or address + count > BLOCK_SIZE:
            raise ValueError(
                f"Диапазон {address}+{count} выходит за 0..{BLOCK_SIZE - 1}"
            )

    @staticmethod
    def _check_tcp_port(params: SimTcpParams) -> None:
        # listen() в pymodbus глотает OSError занятого порта (сервер просто
        # никогда не становится готов), поэтому проверяем bind заранее
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((params.host, params.port))
            except OSError as exc:
                raise ConnectionError(
                    f"Не удалось запустить TCP-симулятор на "
                    f"{params.host}:{params.port}: {exc}"
                ) from exc

    def _run(
        self,
        params: SimTcpParams | RtuParams,
        context: ModbusServerContext,
        identity: ModbusDeviceIdentification,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        asyncio.set_event_loop(loop)

        async def serve() -> None:
            # сервер надо создавать внутри корутины: ModbusBaseServer.__init__
            # требует running event loop (asyncio.get_running_loop())
            server = self._make_server(params, context, identity)
            self._server = server
            await server.serve_forever()

        try:
            loop.run_until_complete(serve())
        except Exception:
            logger.exception("Ошибка потока симулятора")
        finally:
            loop.close()

    def _make_server(
        self,
        params: SimTcpParams | RtuParams,
        context: ModbusServerContext,
        identity: ModbusDeviceIdentification,
    ) -> ModbusTcpServer | ModbusSerialServer:
        if isinstance(params, SimTcpParams):
            return _SimTcpServer(
                context,
                identity=identity,
                address=(params.host, params.port),
                request_tracer=self._trace_request,
                owner=self,
            )
        return ModbusSerialServer(
            context,
            framer=Framer.RTU,
            identity=identity,
            port=params.port,
            baudrate=params.baudrate,
            bytesize=params.bytesize,
            parity=params.parity,
            stopbits=params.stopbits,
            timeout=params.timeout,
            request_tracer=self._trace_request,
        )

    def _trace_request(self, request: ModbusPDU, *_addr: Any) -> None:
        hook = self.on_request
        if hook is None:
            return
        try:
            hook(_format_request(request))
        except Exception:  # failing hook must never kill the server loop
            logger.exception("Ошибка в on_request")

    def _emit_master_write(self, kind: RegisterKind, address: int, values: list) -> None:
        hook = self.on_master_write
        if hook is None:
            return
        try:
            hook(kind, address, values)
        except Exception:  # иначе мастер получит Slave Device Failure
            logger.exception("Ошибка в on_master_write")

    def _emit_client(self, connected: bool) -> None:
        hook = self.on_client
        if hook is None:
            return
        try:
            hook(connected)
        except Exception:
            logger.exception("Ошибка в on_client")


class _SimTcpServer(ModbusTcpServer):
    """TCP-сервер с колбэком подключения/отключения клиентов (on_client)."""

    def __init__(self, *args: Any, owner: SimBackend, **kwargs: Any) -> None:
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
