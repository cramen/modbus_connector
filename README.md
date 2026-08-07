# modbus_connector

GUI-приложение на PySide6 для отладки шины Modbus и разработки Modbus-устройств.
Работает с Modbus TCP и Modbus RTU через синхронные клиенты pymodbus;
вся Modbus-логика выполняется в отдельном потоке (QThread), GUI не блокируется.

## Возможности

- Подключение по TCP (host, port, timeout) и RTU (порт, baudrate, parity и т.д.;
  RTU по умолчанию) — все настройки задаются в GUI и сохраняются между запусками
  в `~/.modbus_connector/settings.json`.
- Таблица регистров: строки с именем, типом области (coils, discrete inputs,
  holding/input registers), адресом и количеством; чтение и запись значений.
  Enter в колонке «New value» отправляет команду записи, Ctrl+R (Cmd+R на macOS)
  читает текущую строку; вся таблица доступна с клавиатуры.
- Поллинг всех строк с настраиваемым интервалом.
- Сканер адресов (кнопка «Scanner…», отдельное окно): перебор unit id в заданном
  диапазоне с настраиваемыми пробами, показывает устройства, ответившие хотя бы
  на одну пробу.
- Панель лога внизу окна, скрывается кнопкой «Log».

## Требования

- Python 3.11+
- `PySide6`, `pymodbus[serial]==3.6.9`

## Установка

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

## Запуск

```bash
modbus-connector
# или
python -m modbus_connector
```

## Структура проекта

```
src/modbus_connector/
  models.py       # типы данных без Qt: TcpParams/RtuParams, RegisterRow,
                  # ScanProbe, parse_values()/format_values()
  backend.py      # ModbusBackend — синхронная обёртка над pymodbus (без Qt)
  worker.py       # ModbusWorker (QObject) — сигналы/слоты над backend, QThread
  connection_panel.py  # панель подключения (TCP/RTU, state/set_state)
  registers_panel.py   # таблица регистров, поллинг, Enter = запись
  scanner_panel.py     # сканер адресов (отдельное окно)
  log_panel.py         # панель лога (скрываемая)
  settings_store.py    # сохранение настроек в ~/.modbus_connector/settings.json
  main_window.py  # главное окно
  app.py          # создание QApplication и запуск
  __main__.py     # python -m modbus_connector
tests/
  conftest.py     # фикстура modbus_server: тестовый Modbus TCP сервер на 127.0.0.1
  test_models.py  # parse_values()/format_values()
  test_backend.py # ModbusBackend против тестового сервера
```

## Разработка

```bash
pytest          # тесты
ruff check .    # линт
```

Тесты backend поднимают реальный Modbus TCP сервер (pymodbus) на 127.0.0.1
со свободным портом и гоняют чтение/запись/сканирование через `ModbusBackend`.
