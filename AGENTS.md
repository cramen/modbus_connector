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
- `pyqtgraph` (тянет numpy) — живые графики значений регистров
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
                  # decode_register_values(values, fmt, order) — decode до чисел,
                  # format_register_values (поверх decode),
                  # format_scaled_values (x*scale+offset по decoded),
                  # rows_to_csv/rows_from_csv/row_to_csv_record/CSV_COLUMNS —
                  # CSV таблицы регистров (толерантный разбор,
                  # guess_column_mapping/csv_header, subset колонок),
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
                       # state()/set_state(); две строки: настройки сверху,
                       # кнопки (Connect/Device ID…/Diagnostics… +
                       # Scanner…/Graph…/Log от SessionWidget через add_control)
                       # и статус снизу; статус — sizePolicy Ignored, длинный
                       # текст не расширяет окно;
                       # статус трёх цветов: серый (отключён), зелёный (alive),
                       # оранжевый "(idle)" — connected, но backend.connected
                       # упал после таймаута (pymodbus переподключится сам);
                       # цвета — theme.status_colors(), refresh_theme()
                       # перерисовывает при смене темы;
                       # set_connected(ok, message) + слот set_alive(bool);
                       # кнопка "Device ID…" (активна при подключении) — диалог
                       # идентификации устройства (0x2B/0x0E);
                       # кнопка "Diagnostics…" — диалог диагностики (0x08):
                       # loopback, счётчики, clear counters
  registers_panel.py   # таблица регистров: чтение/запись, поллинг по QTimer,
                       # set_bus_enabled(ok) — гейтинг контролов, ходящих на
                       # шину (Read all, split-поллинг, Log to file, mask
                       # write, read/write; _read/_write_table_row молча
                       # игнорируют), по умолчанию выключены; Add/Filter/
                       # Sort/Display…/CSV/⚙ — локальные, всегда доступны,
                       # split-кнопка поллинга (QToolButton MenuButtonPopup,
                       # меню "Start polling" / "Start polling and record",
                       # режим запоминается; выбор пункта на ходу переключает
                       # запись без перезапуска таймеров; во время поллинга
                       # основная кнопка — "Stop polling"),
                       # start_polling(record)/stop_polling/is_polling/
                       # is_recording, сигнал pollStateChanged(polling,
                       # recording) — по нему синхронизируются кнопки панели
                       # и окна графика; история пишется только в режиме
                       # poll+record (ручные чтения без поллинга не пишутся),
                       # колонка Poll, ms — per-row интервал поллинга
                       # (пусто = глобальный тик; у такой строки свой QTimer,
                       # пересоздаётся при правке ячейки на лету),
                       # колонка Unit ID — per-row unit (пусто = глобальный
                       # unit из панели подключения),
                       # колонка Format — формат отображения значений
                       # (dec/hex/s16/u32/s32/f32/u64/s64/f64/ascii, только для
                       # регистровых kind; ascii и hex не масштабируются),
                       # Scale/Offset/Unit/Order — в диалоге "Display…",
                       # хранилище _row_display по токену (RowDisplaySettings,
                       # order None = глобальный Order-комбо над таблицей,
                       # сохраняется как registers_options в session state,
                       # там же column_widths — ширины колонок таблицы,
                       # clamp 30..2000, толерантный разбор),
                       # выпадающая кнопка CSV — import (заменяет таблицу,
                       # диалог сопоставления колонок) / export (диалог выбора
                       # и порядка колонок, +колонка value при экспорте,
                       # при импорте пропускается),
                       # логирование значений в файл: checkable-кнопка
                       # "Log to file" + кнопка "⚙" (диалог настроек);
                       # start_logging при выключенном поллинге запускает его
                       # (start_polling(_record_mode)); старт без пути открывает
                       # диалог; stop_logging не останавливает поллинг;
                       # запись — в handle_read_finished при is_open (и ручные
                       # чтения тоже), flush по QTimer 1 с; _log_value(index,
                       # values) — числа со scale/offset без единиц, биты 0/1,
                       # multi-value через ";", hex/ascii как есть;
                       # флаг log на строку (RowDisplaySettings.log, ключ "log"
                       # в state строки, default True) — чек-лист строк в
                       # диалоге настроек, выключенные строки пропускаются
                       # в _log_read;
                       # logging_state()/set_logging_state() — настройки в
                       # session state (как registers_options), без on/off
  datalogger.py     # без Qt: LogSettings (path/format csv|jsonl/fields —
                    # subset timestamp/name/address/kind, value всегда/
                    # append), LogSample, DataLogger — open/write/flush/close/
                    # is_open/rows_written; CSV: заголовок только в новый/пустой
                    # файл; JSONL — объект на строку только с включёнными ключами
  datalogger_dialog.py  # LoggingSettingsDialog — файл (+Browse…, подсказка
                        # ~/modbus_log_YYYYMMDD_HHMMSS), формат (синхронизация
                        # расширения), чекбоксы полей, append/overwrite,
                        # чек-лист логируемых строк (group box "Rows to log",
                        # Select all/none, Space/Enter)
  csv_dialogs.py    # ExportColumnsDialog (чек-лист колонок, Space/Ctrl+стрелки,
                    # Enter) и ImportMappingDialog (таблица сопоставления
                    # колонок файла полям, валидация обязательных)
  timeseries.py     # TimeSeries — кольцевой буфер (t, value) для графиков:
                    # append/clear/len/points/stats(t0, t1), без Qt;
                    # MAX_SAMPLES=10000 (~2.7 ч при поллинге 1 Гц)
  graph_window.py   # GraphWindow (pyqtgraph, отдельное окно на сессию):
                    # чек-лист рядов (по токенам), Follow/Full/Manual + zoom
                    # rect, цвета кривых по теме (_curve_color: intColor на
                    # тёмной, tab10-подобный LIGHT_CURVE_COLORS на светлой;
                    # update_theme перекрашивает существующие кривые), маркеры A/B с min/max/avg и Δt (размещение внутри
                    # видимых данных), hover-кроссхейр (SignalProxy 60 Гц —
                    # отдаёт КОРТЕЖ аргументов сигнала; hover-move без кнопки
                    # требует mouse tracking на view и viewport, оба включены
                    # явно → _update_crosshair(view_x): серая пунктирная
                    # вертикаль 2 px,
                    # TextItem справа сверху со значениями видимых рядов —
                    # ближайший отсчёт через np.searchsorted, «—» вне диапазона,
                    # точки на кривых; скрывается вне области графика;
                    # TextItem — GraphicsObject без anchor-миксина легенды,
                    # поэтому _pin_readout переставляет его в угол view по
                    # sigRangeChanged (иначе сдвигался при панорамировании)), Clear — сброс истории и оси времени,
                    # кнопка-дублёр "Start polling and record"/"Stop polling"
                    # (управляет панелью, следит за pollStateChanged),
                    # set_bus_enabled(ok) — гейтинг этой кнопки,
                    # update_theme() — перекраска фона/осей/кроссхейра/легенды
                    # под тему (pg.setConfigOptions + setBackground; у легенды
                    # label.setText(label.text) — цвет запечён в HTML; вызывается
                    # из __init__ и MainWindow при переключении темы),
                    # QTimer 500 мс
                    # читает панель (row_tokens/row_label/series, rowsChanged)
                       # колонки таблицы ресайзятся (Interactive),
                       # колонка Trend — SparklineWidget (QPainter) по ряду
                       # _series[token] (TimeSeries; пишется primary-значение
                       # при чтении, hex/ascii не захватываются), clear_series(),
                       # изменившееся при чтении значение подсвечивается
                       # зелёным на ~2 с (theme.flash_color(), по токену строки,
                       # с генерацией),
                       # фильтр по имени/типу/адресу (QLineEdit) и кнопка
                       # "Sort by address" — физическая перестановка строк,
                       # токены и pending-запросы сохраняются,
                       # "Mask write (0x16)…" — диалог mask write, после успеха
                       # перечитываются строки, покрывающие адрес,
                       # "Read/Write (0x17)…" — диалог read/write registers,
                       # прочитанные значения пишутся в лог,
                       # Enter в колонке New value = запись, Ctrl/Cmd+R = чтение
                       # текущей строки, удаление строки — иконка-крестик,
                       # быстрые действия над текущей строкой (Ctrl+C копия
                       # значения, Ctrl+0/1 запись 0/1, Ctrl+± шаг по
                       # последнему RAW-значению (_last_values по токену,
                       # clamp 0..0xFFFF / 0..1 для coils, молча), Ctrl+T
                       # toggle; coils пишутся bool, input/discrete —
                       # logLine "read-only area"; общий emit — _emit_write)
                       # и контекстное меню таблицы с теми же действиями
                       # (правый клик сначала выбирает строку; "Clear history"
                       # — только на колонке Trend),
  scanner_panel.py     # сканер unit-адресов и адресов регистров (секция
                       # Registers scan); открывается отдельным окном;
                       # set_bus_enabled(ok) — гейтинг Start-кнопок (Stop
                       # доступны всегда), по умолчанию выключены; двойной
                       # клик по найденному unit выбирает его в панели
                       # подключения (unitSelected); probes-таблица — текстовые
                       # ячейки (dec/0x-hex), невалидные строки пропускаются;
                       # state()/set_state() — диапазон, probes и параметры
                       # Registers scan сохраняются в настройках
  log_panel.py         # панель лога внизу главного окна, скрываемая кнопкой Log;
                       # чекбокс Raw (выкл. по умолчанию) показывает raw-кадры
                       # шины (append_raw), буфер (is_raw, текст) по 5000 строк
                       # каждого вида, перерисовка при переключении;
                       # Save… — выгрузка всего лога (нормальный + raw) в файл
  settings_store.py    # load_settings()/save_settings() — JSON в ~/.modbus_connector/
  session_widget.py # SessionWidget — одна Modbus-сессия: ConnectionPanel +
                    # RegistersPanel + LogPanel + ScannerPanel (окно) +
                    # ModbusWorker в QThread, вся проводка сигналов внутри;
                    # connectionChanged → set_bus_enabled(ok) панели/сканера/
                    # окна графика, при разрыве — stop_logging + stop_polling;
                    # state()/set_state() (connection+registers+
                    # registers_options+logging+scanner,
                    # backward compat со старым плоским форматом),
                    # shutdown() — stop_logging + корректная остановка
                    # worker/потока;
                    # сигналы statsUpdated(object) и titleChanged(str)
                    # ("New connection" → описание соединения), last_stats()
  main_window.py  # главное окно: QTabWidget с SessionWidget'ами (кнопка "+"
                  # в углу, последнюю вкладку закрыть нельзя), меню File
                  # (save/load всех вкладок), меню View — радио-переключатель
                  # темы (System/Light/Dark, QActionGroup), статус-бар следует
                  # активной вкладке; настройки: {"theme": str,
                  # "tabs": [...], "active_tab": i} (theme отсутствует в старых
                  # файлах → "system"),
                  # старый односессионный формат читается как одна вкладка
  theme.py        # тема (pyqtdarktheme): THEMES, apply_theme(name) —
                  # system→"auto" (вызывать после QApplication),
                  # current_theme(), is_dark() (system → QStyleHints
                  # .colorScheme, откат на яркость палитры);
                  # темо-зависимые цвета: graph_colors() (bg/fg для
                  # pyqtgraph), crosshair_color(), flash_color(),
                  # status_colors(); pyqtgraph НЕ импортируется (ленивая
                  # загрузка numpy сохранена)
  app.py          # QApplication, apply_theme(из настроек), entry point main()
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
  test_datalogger.py  # DataLogger: csv/jsonl, subset полей, append/overwrite
  test_registers_panel.py  # offscreen Qt тесты таблицы регистров
  test_session_widget.py   # smoke: state round-trip + shutdown сессии
  test_main_window_tabs.py # вкладки: round-trip настроек, старый формат,
                           # закрытие вкладок
  test_scanner_panel.py    # probes-таблица (текстовые ячейки, пропуск
                           # невалидных), round-trip настроек сканера
  test_timeseries.py       # TimeSeries: буфер, вытеснение, stats
  test_theme.py            # apply_theme (stylesheet меняется), round-trip
                           # ключа "theme", меню View (эксклюзивность)
  test_graph_window.py     # чек-лист рядов, маркеры stats, Follow, zoom→Manual
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
                          # иконка: assets/icon.icns (macOS) / icon.ico (Windows) / icon.png (Linux),
                          # источник — assets/icon.png (генерируется скриптом, см. git history)
build.bat                 # то же под Windows (cmd): dist\ModbusConnector\ModbusConnector.exe
```

CI: `.github/workflows/build.yml` — push в main / ручной запуск; матрица
macOS/Windows/Linux: pytest, затем сборка через build.sh/build.bat,
артефакты в upload-artifact (macOS — dmg, Windows/Linux — каталог dist).

## Соглашения

- Python 3.11+ синтаксис, полная типизация, минимум комментариев, ruff line-length 100.
- models.py, backend.py и datalogger.py — чистый Python без Qt, покрываются
  тестами без GUI.
- Вся работа с pymodbus — только в backend.py; Qt-код общается с ним через
  сигналы/слоты ModbusWorker.
- `stop_scan` подключён с `Qt.DirectConnection`: слот должен исполниться в
  GUI-потоке немедленно, т.к. worker-поток занят циклом start_scan и не может
  обрабатывать queued-вызовы. Аналогично `disconnect` при закрытии — через
  `QMetaObject.invokeMethod(..., BlockingQueuedConnection)`.
- Тесты backend используют реальный тестовый Modbus TCP сервер (фикстура
  `modbus_server`), запущенный в отдельном потоке.
- При изменении структуры/команд/модулей обновлять этот файл и README.md.
