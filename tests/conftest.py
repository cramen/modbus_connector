import asyncio
import socket
import threading
import time
from collections.abc import Iterator

import pytest
from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusServerContext,
    ModbusSlaveContext,
)
from pymodbus.device import ModbusDeviceIdentification
from pymodbus.framer import Framer
from pymodbus.server import ModbusTcpServer

UNIT_ID = 1


def _serve(framer: Framer) -> Iterator[int]:
    """Тестовый Modbus TCP сервер на 127.0.0.1, возвращает порт.

    holding registers 0..9 = 100..109, input registers 0..4 = 7..11,
    coils 0..7 = True/False чередуя (с True), discrete inputs 0..7 = с False.
    unit_id = 1. Сервер asyncio-based, работает в отдельном потоке
    с собственным event loop; framer — SOCKET (MBAP) или RTU (rtu over tcp).
    """
    slave = ModbusSlaveContext(
        di=ModbusSequentialDataBlock(0, [i % 2 == 1 for i in range(8)]),
        co=ModbusSequentialDataBlock(0, [i % 2 == 0 for i in range(8)]),
        hr=ModbusSequentialDataBlock(0, [100 + i for i in range(10)]),
        ir=ModbusSequentialDataBlock(0, [7 + i for i in range(5)]),
        zero_mode=True,
    )
    context = ModbusServerContext(slaves={UNIT_ID: slave}, single=False)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    loop = asyncio.new_event_loop()
    holder: dict[str, ModbusTcpServer] = {}

    async def serve() -> None:
        identity = ModbusDeviceIdentification(
            info={0x00: "pymodbus", 0x01: "test-server", 0x02: "1.0"}
        )
        server = ModbusTcpServer(
            context, framer=framer, identity=identity, address=("127.0.0.1", port)
        )
        holder["server"] = server
        await server.serve_forever()

    def run() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(serve())

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5.0
    while True:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            if time.monotonic() > deadline:
                loop.call_soon_threadsafe(loop.stop)
                raise
            time.sleep(0.05)
    yield port
    asyncio.run_coroutine_threadsafe(holder["server"].shutdown(), loop).result(timeout=5.0)
    thread.join(timeout=5.0)
    loop.close()


@pytest.fixture()
def modbus_server() -> Iterator[int]:
    yield from _serve(Framer.SOCKET)


@pytest.fixture()
def modbus_rtu_server() -> Iterator[int]:
    yield from _serve(Framer.RTU)
