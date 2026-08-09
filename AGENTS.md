# Agent Guidance: modbus_connector

## Назначение

GUI-приложение на PySide6 для отладки шины Modbus и разработки Modbus-устройств.
Подключение к устройствам по Modbus TCP и RTU (по умолчанию RTU), чтение/запись
регистров из таблицы, поллинг с интервалом, сканер unit-адресов в отдельном окне.
Настройки соединений (вкладки), списки регистров и состояния сканеров
сохраняются между запусками в `~/.modbus_connector/settings.json`
(`settings_store.py`, формат `{"tabs": [...], "active_tab": i}`); через
меню File их же можно сохранить/загрузить в произвольный JSON-файл.

## Архитектура

- Вся Modbus-логика — в `backend.py` на **синхронных** клиентах pymodbus
  (`ModbusTcpClient`, `ModbusSerialClient`). backend.py и models.py не импортируют Qt.
- Qt-слой тонкий: `worker.py` (QObject в отдельном QThread, сигналы/слоты над
  backend) + виджеты (panels, main_window, app). Никакой Modbus-логики в виджетах.
- Async pymodbus не используется.

## Стек

- Python 3.11+, PySide6
- `pymodbus[serial]==3.6.9` — sync-клиенты; методы чтения/записи принимают
  ключевой `slave=`, результат: `.registers` (регистры) / `.bits` (coils,
  discrete inputs), ошибки — `result.isError()`
- dev: `pytest`, `ruff`

## Структура (src-layout)

```
pyproject.toml
src/modbus_connector/
  models.py       # без Qt: RegisterKind, TcpParams/RtuParams,
                  # RtuOverTcpParams/RtuOverUdpParams, ConnectionParams,
                  # describe_connection(params) — "tcp host:port" и т.п.,
                  # RegisterRow, ScanProbe, DEFAULT_SCAN_PROBES, DisplayFormat,
                  # ByteOrder, parse_values(kind, text), format_values(values),
                  # format_register_values(values, fmt, order),
                  # format_scaled_values(values, scale, offset, unit),
                  # EXCEPTION_CODES/describe_exception — имена Modbus-исключений,
                  # Stats/StatsSnapshot — счётчики транзакций (ok/err, avg ms,
                  # разбивка ошибок по видам)
  backend.py      # без Qt: ModbusBackend — connect/disconnect/connected,
                  # read/write (write только coils/holding_registers),
                  # mask_write_register (функция 0x16),
                  # readwrite_registers (функция 0x17),
                  # read_device_identification (0x2B/0x0E, dict[int, str]),
                  # diag_loopback/diag_counters/diag_clear_counters (0x08;
                  # неподдержанные счётчики опускаются),
                  # ModbusExceptionError (.exception_code) для exception
                  # responses, человекочитаемые сообщения об ошибках,
                  # scan(probes, start, end, should_stop) — генератор, отдаёт
                  # (unit, hits) для каждого просканированного unit
                  # (hits пуст, если unit не ответил);
                  # scan_addresses(unit, kind, start, end, should_stop) —
                  # генератор ответивших адресов (count=1), семантика ошибок
                  # как у scan;
                  # traffic_hook — Callable[[str, bytes], None] (tx/rx),
                  # обёртка client.send/recv на время подключения
  worker.py       # Qt: ModbusWorker(QObject) над backend — сигналы
                  # connectionChanged/readFinished/writeFinished/scanProgress/
                  # scanHit/scanFinished/addrScanProgress/addrScanHit/
                  # addrScanFinished/statsUpdated/aliveChanged/trafficLine/
                  # logLine;
                  # слоты connect_to/disconnect/read/write/start_scan/stop_scan/
                  # start_addr_scan/check_alive (локальная проверка
                  # backend.connected, без трафика); один флаг _scan_stop на оба
                  # сканера (одновременно работает только один);
                  # read/write замеряют wall time и пишут в Stats
  connection_panel.py  # параметры подключения (TCP / RTU / RTU over TCP /
                       # RTU over UDP — страницы network/serial),
                       # state()/set_state();
                       # статус трёх цветов: серый (отключён), зелёный (alive),
                       # оранжевый "(idle)" — connected, но backend.connected
                       # упал после таймаута (pymodbus переподключится сам);
                       # set_connected(ok, message) + слот set_alive(bool);
                       # кнопка "Device ID…" (активна при подключении) — диалог
                       # идентификации устройства (0x2B/0x0E);
                       # кнопка "Diagnostics…" — диалог диагностики (0x08):
                       # loopback, счётчики, clear counters
  registers_panel.py   # таблица регистров: чтение/запись, поллинг по QTimer,
                       # колонка Poll, ms — per-row интервал поллинга
                       # (пусто = глобальный тик; у такой строки свой QTimer,
                       # пересоздаётся при правке ячейки на лету),
                       # колонка Unit ID — per-row unit (пусто = глобальный
                       # unit из панели подключения),
                       # колонка Format — формат отображения значений
                       # (dec/hex/s16/u32/s32/f32/u64/s64/f64/ascii, только для
                       # регистровых kind; ascii и hex не масштабируются),
                       # колонка Order — порядок байт 32/64-битных значений
                       # (ABCD/CDAB/BADC/DCBA),
                       # колонки Scale/Offset/Unit — показ scaled-значений
                       # (x*scale+offset, кроме hex; запись всегда raw),
                       # изменившееся при чтении значение подсвечивается
                       # зелёным на ~2 с (по токену строки, с генерацией),
                       # фильтр по имени/типу/адресу (QLineEdit) и кнопка
                       # "Sort by address" — физическая перестановка строк,
                       # токены и pending-запросы сохраняются,
                       # "Mask write…" — диалог mask write (0x16), после успеха
                       # перечитываются строки, покрывающие адрес,
                       # "Read/Write…" — диалог read/write registers (0x17),
                       # прочитанные значения пишутся в лог,
                       # Enter в колонке New value = запись, Ctrl/Cmd+R = чтение
                       # текущей строки, удаление строки — иконка-крестик
  scanner_panel.py     # сканер unit-адресов и адресов регистров (секция
                       # Address scan); открывается отдельным окном; двойной
                       # клик по найденному unit выбирает его в панели
                       # подключения (unitSelected); state()/set_state() —
                       # диапазон, probes и параметры Address scan
                       # сохраняются в настройках
  log_panel.py         # панель лога внизу главного окна, скрываемая кнопкой Log;
                       # чекбокс Raw (выкл. по умолчанию) показывает raw-кадры
                       # шины (append_raw), буфер (is_raw, текст) по 5000 строк
                       # каждого вида, перерисовка при переключении;
                       # Save… — выгрузка всего лога (нормальный + raw) в файл
  settings_store.py    # load_settings()/save_settings() — JSON в ~/.modbus_connector/
  session_widget.py # SessionWidget — одна Modbus-сессия: ConnectionPanel +
                    # RegistersPanel + LogPanel + ScannerPanel (окно) +
                    # ModbusWorker в QThread, вся проводка сигналов внутри;
                    # state()/set_state() (connection+registers+scanner,
                    # backward compat со старым плоским форматом),
                    # shutdown() — корректная остановка worker/потока;
                    # сигналы statsUpdated(object) и titleChanged(str)
                    # ("New connection" → описание соединения), last_stats()
  main_window.py  # главное окно: QTabWidget с SessionWidget'ами (кнопка "+"
                  # в углу, последнюю вкладку закрыть нельзя), меню File
                  # (save/load всех вкладок), статус-бар следует активной
                  # вкладке; настройки: {"tabs": [...], "active_tab": i},
                  # старый односессионный формат читается как одна вкладка
  app.py          # QApplication, entry point main()
  __main__.py     # python -m modbus_connector
tests/
  conftest.py     # фикстура modbus_server (порт): asyncio-ModbusTcpServer на
                  # 127.0.0.1, свободный порт, отдельный поток с собственным
                  # event loop; slaves={1: ...} (single=False), zero_mode=True;
                  # hr 0..9 = 100..109, ir 0..4 = 7..11,
                  # coils 0..7 = True/False чередуя, di 0..7 = с False; unit_id = 1;
                  # identity (0x2B/0x0E): pymodbus/test-server/1.0;
                  # фикстура modbus_rtu_server — то же с framer=RTU
  test_models.py  # parse_values/format_values
  test_backend.py # ModbusBackend против modbus_server: read/write/scan
  test_registers_panel.py  # offscreen Qt тесты таблицы регистров
  test_session_widget.py   # smoke: state round-trip + shutdown сессии
  test_main_window_tabs.py # вкладки: round-trip настроек, старый формат,
                           # закрытие вкладок
```

## Команды

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]     # установка (в т.ч. dev-зависимости)
pytest                    # тесты
ruff check .              # линт
modbus-connector          # запуск GUI (или python -m modbus_connector)
./build.sh                # сборка standalone-приложения PyInstaller'ом в dist/
                          # (macOS: ModbusConnector.app + .dmg; extra `build` в pyproject)
build.bat                 # то же под Windows (cmd): dist\ModbusConnector\ModbusConnector.exe
```

CI: `.github/workflows/build.yml` — push в main / ручной запуск; матрица
macOS/Windows/Linux: pytest, затем сборка через build.sh/build.bat,
артефакты в upload-artifact (macOS — dmg, Windows/Linux — каталог dist).

## Соглашения

- Python 3.11+ синтаксис, полная типизация, минимум комментариев, ruff line-length 100.
- models.py и backend.py — чистый Python без Qt, покрываются тестами без GUI.
- Вся работа с pymodbus — только в backend.py; Qt-код общается с ним через
  сигналы/слоты ModbusWorker.
- `stop_scan` подключён с `Qt.DirectConnection`: слот должен исполниться в
  GUI-потоке немедленно, т.к. worker-поток занят циклом start_scan и не может
  обрабатывать queued-вызовы. Аналогично `disconnect` при закрытии — через
  `QMetaObject.invokeMethod(..., BlockingQueuedConnection)`.
- Тесты backend используют реальный тестовый Modbus TCP сервер (фикстура
  `modbus_server`), запущенный в отдельном потоке.
- При изменении структуры/команд/модулей обновлять этот файл и README.md.
