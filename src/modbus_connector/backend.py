import logging
from collections.abc import Callable, Iterator

from pymodbus.client import ModbusSerialClient, ModbusTcpClient
from pymodbus.exceptions import ConnectionException, ModbusIOException

from .models import (
    ConnectionParams,
    RegisterKind,
    ScanProbe,
    TcpParams,
)

logger = logging.getLogger(__name__)


class ModbusBackend:
    def __init__(self) -> None:
        self._client: ModbusTcpClient | ModbusSerialClient | None = None

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.connected

    def connect(self, params: ConnectionParams) -> None:
        self.disconnect()
        if isinstance(params, TcpParams):
            client = ModbusTcpClient(params.host, port=params.port, timeout=params.timeout)
            description = f"{params.host}:{params.port}"
        else:
            client = ModbusSerialClient(
                params.port,
                baudrate=params.baudrate,
                bytesize=params.bytesize,
                parity=params.parity,
                stopbits=params.stopbits,
                timeout=params.timeout,
            )
            description = f"{params.port} ({params.baudrate}/{params.parity})"
        try:
            ok = client.connect()
        except Exception as exc:
            raise ConnectionError(f"Не удалось подключиться к {description}: {exc}") from exc
        if not ok:
            raise ConnectionError(f"Не удалось подключиться к {description}")
        self._client = client
        logger.info("Подключено к %s", description)

    def disconnect(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                logger.exception("Ошибка при закрытии соединения")
            self._client = None
            logger.info("Соединение закрыто")

    def read(
        self, unit: int, kind: RegisterKind, address: int, count: int
    ) -> list[int | bool]:
        client = self._require_client()
        read = {
            "coils": client.read_coils,
            "discrete_inputs": client.read_discrete_inputs,
            "holding_registers": client.read_holding_registers,
            "input_registers": client.read_input_registers,
        }[kind]
        result = read(address, count=count, slave=unit)
        if result.isError():
            raise ModbusIOException(f"Ошибка чтения {kind}@{address}: {result}")
        if kind in ("coils", "discrete_inputs"):
            return list(result.bits[:count])
        return list(result.registers)

    def write(self, unit: int, kind: RegisterKind, address: int, values: list[int | bool]) -> None:
        client = self._require_client()
        if not values:
            raise ValueError("Нет значений для записи")
        if kind == "coils":
            if len(values) == 1:
                result = client.write_coil(address, bool(values[0]), slave=unit)
            else:
                result = client.write_coils(address, [bool(v) for v in values], slave=unit)
        elif kind == "holding_registers":
            if len(values) == 1:
                result = client.write_register(address, int(values[0]), slave=unit)
            else:
                result = client.write_registers(address, [int(v) for v in values], slave=unit)
        else:
            raise ValueError(f"Запись в {kind} не поддерживается (только coils/holding_registers)")
        if result.isError():
            raise ModbusIOException(f"Ошибка записи {kind}@{address}: {result}")

    def scan(
        self,
        probes: list[ScanProbe],
        start: int,
        end: int,
        should_stop: Callable[[], bool],
    ) -> Iterator[tuple[int, list[int]]]:
        """Сканирует unit-адреса start..end, для каждого отдаёт (unit, hits).

        hits — индексы сработавших probes; пустой список — unit не ответил.
        Отсутствие ответа unit пропускается; обрыв соединения — ConnectionError.
        """
        self._require_client()
        for unit in range(start, end + 1):
            if should_stop():
                return
            hits: list[int] = []
            for index, probe in enumerate(probes):
                if should_stop():
                    return
                try:
                    self.read(unit, probe.kind, probe.address, probe.count)
                except (ConnectionException, OSError) as exc:
                    raise ConnectionError(
                        f"Соединение потеряно при сканировании unit={unit}"
                    ) from exc
                except Exception:
                    continue
                hits.append(index)
            yield unit, hits

    def _require_client(self) -> ModbusTcpClient | ModbusSerialClient:
        # pymodbus закрывает сокет после таймаута, но execute() сам переподключится;
        # «нет подключения» — только когда disconnect() уже вызван.
        if self._client is None:
            raise ConnectionError("Нет подключения к Modbus-устройству")
        return self._client
