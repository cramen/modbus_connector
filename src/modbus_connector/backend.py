import logging
from collections.abc import Callable, Iterator

from pymodbus.client import ModbusSerialClient, ModbusTcpClient
from pymodbus.exceptions import ConnectionException, ModbusIOException
from pymodbus.pdu import ExceptionResponse, ModbusResponse

from .models import (
    ConnectionParams,
    RegisterKind,
    ScanProbe,
    TcpParams,
    describe_exception,
)

logger = logging.getLogger(__name__)

TrafficHook = Callable[[str, bytes], None]  # (direction "tx"/"rx", raw frame bytes)


class ModbusExceptionError(ModbusIOException):
    """Операция отклонена устройством: Modbus exception response с кодом."""

    def __init__(self, message: str, exception_code: int) -> None:
        super().__init__(message)
        self.exception_code = exception_code


def _raise_if_error(result: ModbusResponse, prefix: str) -> None:
    if not result.isError():
        return
    if isinstance(result, ExceptionResponse):
        raise ModbusExceptionError(
            f"{prefix}: {describe_exception(result.exception_code)}",
            result.exception_code,
        )
    raise ModbusIOException(f"{prefix}: {result}")


class ModbusBackend:
    def __init__(self) -> None:
        self._client: ModbusTcpClient | ModbusSerialClient | None = None
        self.traffic_hook: TrafficHook | None = None

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
            self._close_client(client)
            raise ConnectionError(f"Не удалось подключиться к {description}: {exc}") from exc
        if not ok:
            self._close_client(client)
            raise ConnectionError(f"Не удалось подключиться к {description}")
        self._client = client
        self._wrap_traffic(client)
        logger.info("Подключено к %s", description)

    def _wrap_traffic(self, client: ModbusTcpClient | ModbusSerialClient) -> None:
        # client.send/recv is the single choke point for raw bytes: every framer
        # (socket for TCP, rtu for serial) funnels frames through these methods
        original_send = client.send
        original_recv = client.recv

        def send(request: bytes) -> int:
            size = original_send(request)
            if request:
                self._emit_traffic("tx", request)
            return size

        def recv(size: int) -> bytes:
            data = original_recv(size)
            if data:
                self._emit_traffic("rx", data)
            return data

        client.send = send
        client.recv = recv

    def _unwrap_traffic(self, client: ModbusTcpClient | ModbusSerialClient) -> None:
        # dropping the instance attributes restores the class methods
        client.__dict__.pop("send", None)
        client.__dict__.pop("recv", None)

    def _emit_traffic(self, direction: str, data: bytes) -> None:
        hook = self.traffic_hook
        if hook is None:
            return
        try:
            hook(direction, data)
        except Exception:  # a failing hook must never kill a transaction
            logger.exception("Ошибка в traffic_hook")

    def disconnect(self) -> None:
        if self._client is not None:
            self._unwrap_traffic(self._client)
            self._close_client(self._client)
            self._client = None
            logger.info("Соединение закрыто")

    @staticmethod
    def _close_client(client: ModbusTcpClient | ModbusSerialClient) -> None:
        try:
            client.close()
        except Exception:
            logger.exception("Ошибка при закрытии соединения")

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
        _raise_if_error(result, f"Ошибка чтения {kind}@{address}")
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
        _raise_if_error(result, f"Ошибка записи {kind}@{address}")

    def mask_write_register(self, unit: int, address: int, and_mask: int, or_mask: int) -> None:
        client = self._require_client()
        for name, mask in (("and_mask", and_mask), ("or_mask", or_mask)):
            if not 0 <= mask <= 0xFFFF:
                raise ValueError(f"{name} вне диапазона 0..65535: {mask}")
        result = client.mask_write_register(
            address, and_mask=and_mask, or_mask=or_mask, slave=unit
        )
        _raise_if_error(result, f"Ошибка mask write @{address}")

    def readwrite_registers(
        self,
        unit: int,
        read_address: int,
        read_count: int,
        write_address: int,
        values: list[int],
    ) -> list[int]:
        client = self._require_client()
        if not values:
            raise ValueError("Нет значений для записи")
        if not 1 <= read_count <= 125:
            raise ValueError(f"read_count вне диапазона 1..125: {read_count}")
        for value in values:
            if not 0 <= value <= 0xFFFF:
                raise ValueError(f"Значение регистра вне диапазона 0..65535: {value}")
        result = client.readwrite_registers(
            read_address=read_address,
            read_count=read_count,
            write_address=write_address,
            values=values,
            slave=unit,
        )
        _raise_if_error(result, f"Ошибка read/write @{read_address}")
        return list(result.registers)

    def read_device_identification(self, unit: int) -> dict[int, str]:
        client = self._require_client()
        result = client.read_device_information(read_code=1, object_id=0, slave=unit)
        _raise_if_error(result, f"Ошибка чтения device id unit={unit}")
        return {
            int(object_id): (
                value.decode("utf-8", errors="replace")
                if isinstance(value, bytes)
                else str(value)
            )
            for object_id, value in result.information.items()
        }

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

    def scan_addresses(
        self,
        unit: int,
        kind: RegisterKind,
        start: int,
        end: int,
        should_stop: Callable[[], bool],
    ) -> Iterator[int]:
        """Читает адреса start..end по одному, отдаёт ответившие без ошибки.

        Семантика ошибок как в scan(): ошибка уровня регистра (исключение
        устройства, нет ответа) пропускается, обрыв транспорта — ConnectionError.
        """
        self._require_client()
        for address in range(start, end + 1):
            if should_stop():
                return
            try:
                self.read(unit, kind, address, 1)
            except (ConnectionException, OSError) as exc:
                raise ConnectionError(
                    f"Соединение потеряно при сканировании адресов unit={unit}"
                ) from exc
            except Exception:
                continue
            yield address

    def _require_client(self) -> ModbusTcpClient | ModbusSerialClient:
        # pymodbus закрывает сокет после таймаута, но execute() сам переподключится;
        # «нет подключения» — только когда disconnect() уже вызван.
        if self._client is None:
            raise ConnectionError("Нет подключения к Modbus-устройству")
        return self._client
