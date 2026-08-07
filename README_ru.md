# modbus_connector

[English version: README.md](README.md)

GUI-приложение на PySide6 для отладки шины Modbus и разработки Modbus-устройств.
Работает с Modbus TCP и Modbus RTU через синхронные клиенты pymodbus;
вся Modbus-логика выполняется в отдельном потоке (QThread), GUI не блокируется.

## Возможности

- Подключение по TCP (host, port, timeout) и RTU (порт, baudrate, parity и т.д.;
  RTU по умолчанию) — все настройки задаются в GUI. Настройки соединения и список
  регистров сохраняются между запусками в `~/.modbus_connector/settings.json`,
  а через меню File — в произвольный JSON-файл (и загружаются обратно).
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

## Сборка исполняемого файла

```bash
./build.sh        # macOS / Linux
build.bat         # Windows (cmd, работает и двойным кликом)
```

Скрипт ставит PyInstaller (extra `build`) и собирает standalone-приложение в
`dist/`: на macOS — `ModbusConnector.app` + установочный образ
`ModbusConnector.dmg`, на Windows/Linux — каталог `ModbusConnector/` с
исполняемым файлом внутри (на Windows — `ModbusConnector.exe`; на другую машину
копируется весь каталог). Артефакт не требует установленного Python на целевой
машине.

Заметки для macOS:

- Запуск: двойной клик по `ModbusConnector.app` или `open dist/ModbusConnector.app`.
  Файлы из промежуточного каталога `build/` запускать нельзя (скрипт удаляет его
  после сборки).
- Для переноса на другую машину используйте готовый `dist/ModbusConnector.dmg`
  (самостоятельно переименовывать или упаковывать `.app` в `.pkg` не нужно —
  такой файл не будет валидным установщиком).
- Приложение подписано ad-hoc: при первом запуске на другом Mac Gatekeeper
  предупредит о неустановленном разработчике — открывайте через правый клик →
  «Открыть» или снимите карантин: `xattr -dr com.apple.quarantine ModbusConnector.app`.

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

## CI

GitHub Actions (`.github/workflows/build.yml`) на каждый пуш в `main` гоняет
тесты и собирает артефакты под три ОС на раннерах macOS/Windows/Linux:
`modbus-connector-macos` (DMG), `modbus-connector-windows` и
`modbus-connector-linux` (zip с исполняемым файлом). Готовые файлы скачиваются
со страницы запуска workflow (Actions → выбранный run → Artifacts; хранятся
90 дней); сборку также можно запустить вручную через «Run workflow».

При пуше тега `v*` (например, `git tag v0.1.0 && git push origin v0.1.0`)
те же файлы автоматически прикрепляются к GitHub Release — постоянной странице
скачивания (Releases в репозитории).
