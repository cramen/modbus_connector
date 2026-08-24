# Agent Guidance: modbus_connector

## Назначение

GUI-приложение на PySide6 для отладки шины Modbus и разработки Modbus-устройств.
Каждая вкладка — сессия в режиме Master (опрос устройства), Slave
(встроенный симулятор устройства: Modbus TCP/RTU сервер на pymodbus с
редактируемой картой значений и правилами-выражениями) или Sniffer (пассивное
прослушивание RTU-шины через отдельный serial-адаптер: карта регистров
восстанавливается из подслушанного трафика чужого мастера).
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

- Python 3.11+, PySide6 (полный метапакет — включает Addons; alarm_sound.py
  использует QtMultimedia QSoundEffect, PyInstaller подхватывает импорт сам)
- `pyqtgraph` (тянет numpy) — живые графики значений регистров
- `pyqtdarktheme` — темы System/Light/Dark (theme.py)
- `pymodbus[serial]==3.6.9` — sync-клиенты; методы чтения/записи принимают
  ключевой `slave=`, результат: `.registers` (регистры) / `.bits` (coils,
  discrete inputs), ошибки — `result.isError()`; extra `serial` = pyserial
- dev (extra `dev`): `pytest`, `pytest-asyncio`, `ruff`
- build (extra `build`): `pyinstaller>=6`

## Структура (src-layout)

```
pyproject.toml
src/modbus_connector/
  models.py       # без Qt: RegisterKind, TcpParams/RtuParams,
                  # RtuOverTcpParams/RtuOverUdpParams, ConnectionParams,
                  # describe_connection(params) — "tcp host:port" и т.п.,
                  # RegisterRow, ScanProbe, DEFAULT_SCAN_PROBES, DisplayFormat,
                  # ReadSpec/ReadMember/ReadPlan + plan_grouped_reads(rows,
                  # max_gap=8) — объединение чтений соседних адресов в один
                  # запрос: группировка по (unit, kind), сортировка по адресу,
                  # мердж при зазоре ≤ max_gap (перекрытия тоже; members
                  # помнят offset/count), кап длины плана 125 регистров /
                  # 2000 бит, count<=0/address<0 пропускаются,
                  # ByteOrder, parse_values(kind, text), format_values(values),
                  # decode_register_values(values, fmt, order) — decode до чисел,
                  # encode_register_values(value, fmt, order) — обратный encode
                  # числа в регистры (round+clamp для целых, f32/f64 через
                  # struct.pack, OverflowError на непредставимом; hex/ascii —
                  # ValueError), для правил симулятора,
                  # register_width(fmt) — регистров на значение (1/2/4),
                  # parse_formatted_values(text, fmt, count) — ввод чисел в
                  # формате отображения (поверх encode),
                  # encode_ascii_values(text, count) — текст → регистры
                  # (2 символа на регистр, NUL-pad, не-ASCII → «?»),
                  # format_register_values (поверх decode),
                  # format_scaled_values (x*scale+offset по decoded),
                  # rows_to_csv/rows_from_csv/row_to_csv_record/CSV_COLUMNS —
                  # CSV таблицы регистров (толерантный разбор,
                  # guess_column_mapping/csv_header, subset колонок),
                  # EXCEPTION_CODES/describe_exception — имена Modbus-исключений,
                  # AlarmRule/evaluate_alarm/rule_matches/alarm_rule_to_json/
                  # alarm_rules_from_json — правила алармов (иммутабельные,
                  # приоритет = порядок, границы диапазонов включительные),
                  # Stats/StatsSnapshot — счётчики транзакций (ok/err, avg ms,
                  # разбивка ошибок по видам),
                  # diff_snapshots(old, new) — сравнение двух RAW-снимков
                  # значений строки (None = «нет данных», None/None = нет
                  # различия) для snapshot diff;
                  # parse_expression/Expression (.text/.deps/.evaluate) —
                  # движок выражений: ссылки [имя] на строки, whitelisted
                  # арифметика/функции (AST-валидация, eval без builtins),
                  # ValueError на мусоре, KeyError = нет строки, nan = мат.
                  # ошибка;
                  # RowDisplaySettings.value_names — имена значений (enum,
                  # dict[int, str]); parse_value_names/value_names_to_text —
                  # редактор «значение=имя» по строкам (мусор пропускается,
                  # пустой текст = {}), value_names_to_json/value_names_from_json
                  # — state (JSON-ключи строками, толерантный разбор),
                  # format_named_value — «имя (N)» или None;
                  # RowDisplaySettings.bitmask (state-ключ "bitmask", bool):
                  # value_names именуют биты 0..15 u16-значения (enum по битам);
                  # set_bit_labels — метки установленных битов (имя или «bN»),
                  # bits_to_value — сборка u16 из номеров битов (вне 0..15 игнор),
                  # format_bitmask_value — «Running, Alarm
                  # (0000 0000 1010 0101)» (метки
                  # через ", " + hex в скобках; без установленных — «0x0000»;
                  # пустые names — все биты «bN»)
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
                  # генератор (адрес, значения) для ответивших адресов;
                  # регистровые области читаются по count=2 (пара для
                  # 32-битных форматов сканера), при ошибке пары — повтор
                  # count=1; coils/di — всегда count=1; семантика ошибок
                  # как у scan;
                  # traffic_hook — Callable[[str, bytes], None] (tx/rx),
                  # обёртка client.send/recv на время подключения
  worker.py       # Qt: ModbusWorker(QObject) над backend — сигналы
                  # connectionChanged/readFinished/writeFinished/scanProgress/
                  # scanHit/scanFinished/addrScanProgress/
                  # addrScanHit(int адрес, list значения)/
                  # addrScanFinished/statsUpdated/aliveChanged/trafficLine/
                  # logLine;
                  # слоты connect_to/disconnect/read/write/start_scan/stop_scan/
                  # start_addr_scan/check_alive (локальная проверка
                  # backend.connected, без трафика); один флаг _scan_stop на оба
                  # сканера (одновременно работает только один);
                  # read/write замеряют wall time и пишут в Stats
  sim_backend.py  # без Qt: SimTcpParams(host, port=1502), describe_sim(params)
                  # («sim tcp host:port» для заголовка вкладки), BLOCK_SIZE=10000;
                  # SimRtuOverTcpParams — подкласс SimTcpParams: тот же TCP-сервер,
                  # но framer=RTU (для мастеров «Modbus RTU over TCP»);
                  # SimBackend — Modbus slave-сервер (TCP/RTU) на pymodbus,
                  # serve_forever в своём потоке: start(params, unit=None)/stop/
                  # running/set_values/get_values (set_values без хука записи);
                  # хуки on_master_write(kind, address, values) (все fc записи,
                  # включая 0x16/0x17), on_request(line) (request_tracer),
                  # on_client(connected) (TCP); unit=None — отвечает на любой
  sim_worker.py   # Qt: SimWorker(QObject) над SimBackend (в отдельном QThread):
                  # сигналы serverChanged(bool,str)/masterWrote(str,int,list)/
                  # requestLine(str)/clientChanged(bool)/logLine(str)/ticked();
                  # слоты start_server(params, unit) (вызывать СИГНАЛОМ —
                  # Q_ARG не маршаллит dataclass)/stop_server/set_values/
                  # get_values (invokeMethod BlockingQueuedConnection с
                  # Q_RETURN_ARG("QVariantList"))/set_tick_interval/shutdown;
                  # ticked — метроном правил симуляции (в session_widget
                  # подключён к sim_panel.apply_rules)
  sim_panel.py    # SimPanel — slave-режим сессии: строка параметров сервера
                  # (тип TCP/RTU — страницы network/serial по паттерну
                  # ConnectionPanel, BAUDRATES импортируется оттуда; Unit —
                  # FitComboBox "any"+1..247, data None = любой), кнопка
                  # Start/Stop server (connect/disconnect-иконки, как
                  # ConnectionPanel._sync_button_text), статус (status_colors,
                  # серый/зелёный + «clients: N» из clientChanged);
                  # таблица карты Name|Type|Address|Count|Format|Value|Rule|
                  # Rule text|✕
                  # (те же KINDS/FORMATS из models, значения list[int|bool]
                  # хранятся в UserRole ячейки Name — data() возвращает копию,
                  # обновления пишутся назад через setData; count не может быть
                  # меньше ширины значения формата — register_width, автоподъём
                  # при смене формата/правке count/загрузке state); Value — через
                  # format_register_values/format_values, правка → parse_values
                  # + setValuesRequested (пишется всегда: backend хранит блоки
                  # и до старта); masterWrote → handle_master_write обновляет
                  # покрывающие строки и подсвечивает Value зелёным на ~2 с
                  # (flash_color + parented QTimer, поколения по строке);
                  # value names (enum): dict[int, str] в _VALUE_NAMES_ROLE
                  # ячейки Name, state-ключ "value_names"; кнопка "Value
                  # names…" (иконка display, disabled без строк) — диалог
                  # текущей строки (QPlainTextEdit «0=Stopped»); совпавшее
                  # значение count==1 dec/s16 или бита — «имя (N)»
                  # (_named_key + format_named_value в _render_value), Value
                  # ручной строки с names — комбо «N = имя» (выбор = запись
                  # в datastore через setValuesRequested, комбо остаётся на
                  # записанном значении — activated срабатывает и при
                  # повторном выборе; expression-строки только показывают
                  # имя, комбо нет); bitmask (чекбокс в диалоге «Value
                  # names…», _BITMASK_ROLE ячейки Name, state-ключ
                  # "bitmask"): строка регистра count==1 в dec/s16/hex
                  # (_bitmask_row) показывает Value через format_bitmask_value
                  # (tooltip — полный текст); Value ручной строки — кнопка
                  # QToolButton со сводкой → тот же BitsDialog, OK → запись
                  # в datastore через setValuesRequested; expression-строки —
                  # только отображение, кнопки нет;
                  # покрывающие строки; кнопка Template… (csv_import, QMenu с
                  # подменю производителей, дубли kind+address пропускаются);
                  # help-кнопка (make_help_button → SIMULATOR_HELP) рядом с
                  # Template…;
                  # правила значений (Rule = manual/expression, ключи в
                  # itemData, отображение переводится): expression — Value
                  # readonly, Rule text редактируемый (ExpressionDelegate из
                  # registers_panel с extra_functions/extra_names); движок —
                  # parse_expression с SIM_RULE_FUNCTIONS (rand/randint) и
                  # именами t (секунды от старта сервера, _started_at в
                  # set_running) / prev (предыдущий результат строки, на
                  # первом тике — текущее значение); кэш Expression и prev —
                  # в data-ролях ячейки Name (UserRole+1/+2); apply_rules()
                  # зовётся по SimWorker.ticked: values — primary-числа строк
                  # по именам (decode_register_values[0], биты 1.0/0.0,
                  # hex/ascii не участвуют), результат кодируется обратно
                  # encode_register_values (порядок ABCD фиксирован, dec-clamp,
                  # биты bool(round)) → setValuesRequested + перерисовка;
                  # невалидный текст — «⚠»+tooltip (строка пропускается),
                  # nan/нет dep/OverflowError — «—», в datastore не пишем,
                  # prev не обновляем; тик-интервал — spin "Tick, ms"
                  # (100..10000, default 1000) → setTickIntervalRequested(int)
                  # → sim_worker.set_tick_interval (проводка в session_widget,
                  # там же ticked → apply_rules);
                  # сигналы startRequested(object, object)/stopRequested/
                  # setValuesRequested(str,int,list)/setTickIntervalRequested(int)/logLine;
                  # set_running(ok, message), running_description(),
                  # state()/set_state() = {"server": {...}, "rows": [...],
                  # "tick_ms": int}; строки += "rule"/"rule_text" (толерантный
                  # разбор, default manual, текст хранится только у expression,
                  # невалидный expr грузится как «⚠»)
  sniffer_backend.py  # без Qt: пассивный сниффер RTU-шины: свой разбор кадров
                      # со скользящим окном и ресинхронизацией по CRC16
                      # (декодер pymodbus не используется — заточен под активного
                      # клиента), направление tx/rx физически не различить —
                      # эвристика по структуре кадра + матчер транзакций;
                      # describe_sniffer(params) («sniff rtu port @ baud»),
                      # RtuFrameParser.feed → SniffedFrame, BusModel (карта
                      # unit → kind → address), format_frame(frame);
                      # SnifferBackend — композиция SerialSniffer → parser →
                      # BusModel: start(params)/stop/running, хуки
                      # on_values(unit, kind, address, values)/on_frame(line)/
                      # on_decoded_frame(SniffedFrame)/on_error(message) —
                      # вызываются из потока чтения порта; порт занимается
                      # pyserial эксклюзивно, start сбрасывает парсер и модель
  sniffer_worker.py   # Qt: SnifferWorker(QObject) над SnifferBackend (в
                      # отдельном QThread): сигналы sniffingChanged(bool,str)/
                      # valuesChanged(int,str,int,list)/frameLine(str) (все
                      # кадры, общий лог)/frameForUnit(int,str) (per-unit логи)/
                      # logLine(str); слоты start_sniffing(params) (вызывать
                      # СИГНАЛОМ — Q_ARG не маршаллит dataclass)/stop_sniffing/
                      # shutdown; колбэки backend привязаны к сигналам напрямую
                      # (emit из потока чтения безопасен, доставка queued)
  sniffer_panel.py    # SnifferPanel — sniffer-режим сессии: строка serial-
                      # параметров (port/baud/bits/parity/stop + Refresh,
                      # BAUDRATES из connection_panel), кнопка Start/Stop
                      # sniffing (иконки scanner/poll_stop, как
                      # ConnectionPanel._sync_button_text), статус
                      # (status_colors, серый/зелёный, красный при ошибке),
                      # help-кнопка (SNIFFER_HELP) рядом с Start;
                      # вкладки unit'ов (QTabWidget) — UnitTab создаётся при
                      # первом кадре/значении unit'а: тулбар (Graph… —
                      # GraphWindow этой вкладки, заголовок «unit N» через
                      # set_window_title; Export CSV… — формат master-таблицы:
                      # ExportColumnsDialog + колонки CSV_COLUMNS+value,
                      # count=1, файл читается models.rows_from_csv),
                      # таблица Address|Name|Type|Format|Value|Trend (строки
                      # добавляются автоматически, отсортированы по адресу;
                      # значения — в UserRole ячейки Name, ключ (kind, address)
                      # — в UserRole+1; Name и Format редактируются, flash
                      # изменения ~2 с, тренд — SparklineWidget по TimeSeries
                      # на строку, hex/ascii в тренд не пишутся) + per-unit лог
                      # кадров (QPlainTextEdit); мини-интерфейс для GraphWindow:
                      # row_tokens/row_label («имя» или «kind@addr»)/
                      # row_poll_enabled (всегда True)/series/clear_series +
                      # сигнал rowsChanged (новая строка, переименование по
                      # itemChanged с кэшем имён — setData значений его не
                      # дёргает); сигналы startRequested(object)/stopRequested/
                      # logLine; set_sniffing(ok, message),
                      # sniffing_description(),
                      # state()/set_state() = {"params": {...}, "units": [...]}
                      # (толерантный разбор)
  connection_panel.py  # параметры подключения (TCP / RTU / RTU over TCP /
                       # RTU over UDP — страницы network/serial; тип в комбо
                       # лежит в itemData английским, переводится только
                       # отображение, state() хранит английский ключ),
                       # state()/set_state(); две строки: настройки сверху,
                       # иконочные кнопки с тултипами (icons.make_button:
                       # Connect/Disconnect — одна кнопка со сменой иконки,
                       # Device ID…/Diagnostics…, обновление списка портов +
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
                       # первая колонка — чекбокс «строка в поллинге»
                       # (по умолчанию вкл.; снятые строки пропускаются
                       # поллингом и Read all, per-row таймер строки
                       # останавливается; Ctrl+R по строке и запись не
                       # затрагиваются; state-ключ "poll_enabled", default
                       # True, в CSV не входит),
                       # кнопки тулбара — иконочные QToolButton'ы с тултипами
                       # (icons.make_button; text() сохраняет подпись),
                       # set_bus_enabled(ok) — гейтинг контролов, ходящих на
                       # шину (Read all, split-поллинг, Log to file, mask
                       # write, read/write; _read/_write_table_row молча
                       # игнорируют), по умолчанию выключены; Add/Filter/
                       # Sort/Display…/Alarms…/CSV/настройки логирования —
                       # локальные, всегда доступны,
                       # split-кнопка поллинга (QToolButton MenuButtonPopup,
                       # меню "Start polling" / "Start polling and record",
                       # режим запоминается; выбор пункта на ходу переключает
                       # запись без перезапуска таймеров; во время поллинга
                       # основная кнопка — "Stop polling"),
                       # checkable-кнопка "Group reads" (иконка merge, по
                       # умолчанию ВЫКЛ, registers_options "group_reads",
                       # толерантный разбор) — объединение чтений соседних
                       # адресов в один запрос (models.plan_grouped_reads);
                       # применяется ТОЛЬКО в _poll_global_rows и read_all
                       # (per-row таймеры, Ctrl+R, перечитывание после записи —
                       # поштучно): _read_grouped шлёт на план один
                       # readRequested с RegisterRow окна плана, в
                       # _pending_reads (dict[int, int | ReadPlan]) ложится
                       # план; handle_read_finished раздаёт values членам по
                       # offset/count через общий _apply_read_values
                       # (flash/last_values/series/alarm/expressions/лог);
                       # ошибка плана — лог + фолбэк _handle_plan_finished:
                       # члены перечитываются поштучно (один проход, токены
                       # в _pending_reads, без зацикливания); _read_pending —
                       # проверка «у строки есть запрос» с учётом планов,
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
                       # (dec/hex/s16/u32/s32/f32/u64/s64/f64/ascii/ascii1,
                       # только для регистровых kind; ascii — 2 символа/регистр,
                       # ascii1 — 1 символ/регистр (строки Wiren Board);
                       # ascii/ascii1 и hex не масштабируются),
                       # Scale/Offset/Unit/Order — в диалоге "Display…",
                       # там же внизу редактор Value names выбранной строки
                       # (QPlainTextEdit «0=Stopped» по строкам, применяется
                       # на лету; value_names в RowDisplaySettings, state-ключ
                       # "value_names" {"0": "Stopped"}): совпавшее значение
                       # count==1 dec/s16 или бита показывается как «имя (N)»
                       # (_named_value_text в _display_text), а New value
                       # становится комбо «N = имя» (_sync_value_names_combo):
                       # выбор = немедленная запись через _emit_write ([ключ],
                       # coils — bool), после записи комбо сбрасывается в -1
                       # (повторный выбор пишет снова, как очистка текстового
                       # New value), без шины — молча; скрытие комбо — hide(),
                       # НЕ removeCellWidget (ломает view, см. _swap_rows);
                       # bitmask (чекбокс "Bitmask (16 named bits)" рядом с
                       # редактором value names, live-применение, state-ключ
                       # "bitmask"): строка регистра count==1 в dec/s16/hex
                       # (_bitmask_active) показывает Value через
                       # format_bitmask_value (raw u16, tooltip ячейки — полный
                       # текст), а New value — кнопка QToolButton со сводкой
                       # (обновляется при чтениях), открывающая BitsDialog
                       # (16 чекбоксов столбцом); OK → _emit_write([dialog.value()]),
                       # без шины — молча; переключение режима прячет старый
                       # виджет и ставит новый setCellWidget'ом (Qt прячет
                       # вытесненный, не удаляя); _swap_rows пересинхронизирует
                       # редактор обеих строк,
                       # хранилище _row_display по токену (RowDisplaySettings,
                       # order None = глобальный Order-комбо над таблицей,
                       # сохраняется как registers_options в session state,
                       # там же column_widths — ширины колонок таблицы,
                       # clamp 30..2000, толерантный разбор; ширины хранятся
                       # и применяются по ЛОГИЧЕСКИМ индексам (переживают
                       # перестановку секций)),
                       # раскладка колонок: header movable
                       # (setSectionsMovable — весь код панели работает в
                       # логических индексах COL_*), правый клик по заголовку —
                       # чек-лист видимости (_build_columns_menu): скрывать
                       # можно только колонки данных (DATA_COLUMNS = Name..
                       # Trend; контрольные poll_enabled/actions в меню нет и
                       # скрыть нельзя), последняя видимая колонка данных
                       # disabled; стабильные строковые ключи COLUMN_KEYS по
                       # COL_*, state: "column_order" (визуальный порядок как
                       # список ключей, применяется moveSection; неизвестные/
                       # дубли пропускаются, недостающие — в конец дефолтом) и
                       # "hidden_columns" (по ключам, контрольные и
                       # неизвестные игнор, «скрыть всё» оставляет одну),
                       # выпадающая кнопка CSV — import (заменяет таблицу,
                       # диалог сопоставления колонок) / export (диалог выбора
                       # и порядка колонок, +колонка value при экспорте,
                       # при импорте пропускается),
                       # логирование значений в файл: checkable-кнопка
                       # "Log to file" + иконка settings (диалог настроек);
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
                       # алармы: кнопка "Alarms…" (иконка alarm, локальная)
                       # открывает AlarmsDialog (в список попадают и строки
                       # выражений — «fx имя», см. блок выражений ниже);
                       # правила хранятся в
                       # RowDisplaySettings.alarms (state-ключ "alarms" через
                       # alarm_rule_to_json/alarm_rules_from_json, default [],
                       # в CSV не входят); оценка в _update_alarm по
                       # масштабированному primary-значению (hex/ascii
                       # пропускаются); активное правило красит ячейку Value
                       # (theme.alarm_color, приоритет над flash изменений),
                       # _active_alarms по токену — edge-детекция: лог
                       # "ALARM <label>: value условие" один раз на фронт
                       # None→rule ИЛИ смену активного правила A→B (правила
                       # сравниваются по значению, "ALARM cleared" при смене
                       # не пишется — это не снятие) / "ALARM cleared" на
                       # rule→None (rule.log) и _alarm_sound.play()
                       # (rule.sound; alarm_sound.AlarmSound — QSoundEffect
                       # из QtMultimedia с программным WAV — двухтональная
                       # сирена 880↔1175 Гц, 4 цикла, ~0.9 с — во временном
                       # файле, ленивая инициализация на первом
                       # фронте; без QtMultimedia — откат на
                       # QApplication.beep()); переоценка после правок в
                       # диалоге (_re_evaluate_alarm, silent=True) обновляет
                       # edge-состояние без лога/звука нового правила
                       # снапшот/diff: кнопки "Snapshot"/"Diff…" (иконки
                       # snapshot/diff, локальные, bus не гейтят) — сравнение
                       # «до/после» по RAW-значениям (_last_values);
                       # take_snapshot() перезаписывает снапшот
                       # (dict[token, _SnapshotEntry(name/kind/address/values|
                       # None)] + метка времени, in-memory, в session state НЕ
                       # входит) и пишет "Snapshot taken: N rows" в лог;
                       # Diff… (disabled до первого снапшота) открывает
                       # немодальный SnapshotDiffDialog (один на панель,
                       # повторное нажатие поднимает+обновляет);
                       # snapshot_diff_data() — (подпись, list[DiffRow]) для
                       # окна: diff по models.diff_snapshots (None = «нет
                       # данных»), значения форматируются текущим форматом
                       # строки (_display_text), строки, удалённые после
                       # снапшота, идут в конец с "(removed)" (raw как есть);
                       # выражения: скрываемый блок под таблицей (чекабельная
                       # кнопка Expressions в тулбаре, иконка expression;
                       # видимость — registers_options "expressions_visible",
                       # default False): таблица Name/Expression/Value/Trend/✕
                       # (спарклайн — тот же SparklineWidget); вычисление по
                       # МАСШТАБИРОВАННЫМ primary-значениям строк
                       # (_row_values_by_name, матч dep по текущему имени
                       # строки), пересчёт всех выражений при любом чтении
                       # (handle_read_finished ok) и при правке имени строки;
                       # невалидное выражение — «⚠» в Value (фон
                       # theme.alarm_color("red")) + текст ошибки в toolTip,
                       # предыдущее валидное сбрасывается; нет dep (KeyError)
                       # или nan → «—»; число — f"{v:g}"; история — своя
                       # TimeSeries на выражение, append только при _recording
                       # (poll+record), как у регистров; токены выражений —
                       # из общего _row_token_counter (уникальны в обеих
                       # таблицах); API для графика: expr_tokens()/
                       # expr_label(token)/expr_series(token), rowsChanged
                       # при add/remove/переименовании выражения; state:
                       # expressions_state()/set_expressions_state()
                       # (толерантный разбор, невалидные expr грузятся как «⚠»);
                       # алармы на выражения: правила в _expr_alarms по токену
                       # (в state — ключ "alarms" записи выражения, толерантный
                       # разбор), в AlarmsDialog показаны как «fx имя»
                       # (_expr_alarm_label); оценка в _recalc_expression →
                       # _update_expr_alarm по вычисленному значению
                       # (_expr_last — последнее число, для переоценки после
                       # диалога: _re_evaluate_expr_alarm); «—»/«⚠» не
                       # алармят и снимают активный (семантика снятия как у
                       # регистров: лог "ALARM cleared", подсветка
                       # Value-ячейки выражения);
                       # автодополнение ячейки Expression — ExpressionDelegate
                       # (QStyledItemDelegate, createEditor: QLineEdit +
                       # QCompleter; два режима: внутри [… — имена строк с «]»
                       # на конце, снаружи — функции с «(» и константы pi/e;
                       # модель пересобирается на каждый ввод из
                       # _expression_row_names; ВСТАВКУ по activated делает
                       # сам делегат — QCompleter текст в QLineEdit НЕ
                       # подставляет, он только эмитит сигнал); help-кнопка
                       # в тулбаре блока — EXPRESSIONS_HELP
  snapshot_dialog.py  # SnapshotDiffDialog — немодальное окно сравнения
                      # снапшота с текущими чтениями: таблица Name/Type/
                      # Address/Snapshot/Current, изменённые строки
                      # подсвечены theme.diff_color(), чекбокс
                      # "Only differences" фильтрует, "Refresh" перечитывает
                      # текущие значения, "Take new snapshot" переснимает
                      # снапшот; данных нет — пустые ячейки; окно тянет
                      # данные из data_provider панели (panel не импортируется,
                      # циклического импорта нет), язык — при открытии
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
  bits_dialog.py    # BitsDialog — модальный диалог правки u16-значения как
                    # 16 именованных битов (bitmask-режим value names): grid
                    # 16 QCheckBox в один столбец (подпись — имя бита или «bN»,
                    # чекнутость из значения), value() — сборка через
                    # models.bits_to_value; общий для master (New value)
                    # и slave (Value ручной строки)
  alarms_dialog.py  # AlarmsDialog — модальный диалог правил алармов: слева
                    # список строк регистров и выражений (label по токену,
                    # у выражений — «fx имя»), справа таблица правил
                    # (Condition-комбо с ключом в itemData, Value, Value2 —
                    # только для диапазонов, Color red/yellow, чекбоксы
                    # Log/Sound), Add/Remove/Up/Down (порядок = приоритет);
                    # редактирование через «черновики» (сырые тексты переживают
                    # переключение строк), парсинг в AlarmRule на OK;
                    # нечисловой Value блокирует OK, range без value2 = value
  alarm_sound.py    # AlarmSound — звук фронта аларма: программный WAV
                    # (_alarm_wav_bytes: двухтональная сирена 880↔1175 Гц,
                    # 4 цикла по 110 мс на тон, ~0.88 с, амплитуда 0.9,
                    # огибающая attack/release 8 мс на тоне,
                    # 16-bit mono PCM 44.1 кГц) во временном файле + QtMultimedia
                    # QSoundEffect (лениво на первом play(), неблокирующе,
                    # повторный play переигрывает); без QtMultimedia —
                    # QApplication.beep(); тесты подменяют panel._alarm_sound
  help_dialog.py    # справка по окнам: make_help_button (иконка help,
                    # тултип "Help"/"Справка") +
                    # show_help — немодальный диалог с QTextBrowser
                    # (WA_DeleteOnClose); тексты REGISTERS_HELP/GRAPH_HELP/
                    # SCANNER_HELP/EXPRESSIONS_HELP/SIMULATOR_HELP — HTML со
                    # списком хоткеев, русские версии
                    # в HELP_RU (выбор по текущему языку при открытии);
                    # кнопки стоят в панели регистров, окне графика, сканере,
                    # тулбаре блока выражений и панели симулятора
  csv_dialogs.py    # ExportColumnsDialog (чек-лист колонок, Space/Ctrl+стрелки,
                    # Enter) и ImportMappingDialog (таблица сопоставления
                    # колонок файла полям, валидация обязательных)
  timeseries.py     # TimeSeries — кольцевой буфер (t, value) для графиков:
                    # append/clear/len/points/stats(t0, t1), без Qt;
                    # MAX_SAMPLES=10000 (~2.7 ч при поллинге 1 Гц)
  graph_window.py   # GraphWindow (pyqtgraph, отдельное окно на сессию):
                    # тулбар — иконочные кнопки с тултипами (icons.make_button),
                    # чек-лист рядов (по токенам; показываются строки,
                    # включённые в поллинг, и ВСЕ выражения панели с префиксом
                    # "fx " — expr_tokens/expr_label/expr_series, у них нет
                    # poll-галочки; скрытие строк живое по rowsChanged,
                    # при toggle COL_POLL_ENABLED панель его эмитит; состояние
                    # галочек чек-листа при скрытии/показе строки сохраняется),
                    # Follow/Full/Manual + zoom
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
                    # sigRangeChanged (иначе сдвигался при панорамировании)), Clear — сброс истории
                    # (panel.clear_series: регистры И выражения) и оси времени,
                    # кнопка-дублёр "Start polling and record"/"Stop polling"
                    # (управляет панелью, следит за pollStateChanged) — панель
                    # без start_polling (вкладка сниффера): кнопка скрыта,
                    # pollStateChanged/expr_tokens не трогаются (hasattr/getattr),
                    # set_bus_enabled(ok) — гейтинг этой кнопки,
                    # set_window_title(text) — фиксированный заголовок вместо
                    # «Graph» (окно вкладки сниффера — «unit N»),
                    # update_theme() — перекраска фона/осей/кроссхейра/легенды
                    # под тему (pg.setConfigOptions + setBackground; у легенды
                    # label.setText(label.text) — цвет запечён в HTML; вызывается
                    # из __init__ и MainWindow при переключении темы),
                    # QTimer 500 мс
                    # читает панель (row_tokens/row_label/row_poll_enabled/series,
                    # rowsChanged)
                       # колонки таблицы ресайзятся (Interactive),
                       # колонка Trend — SparklineWidget (QPainter) по ряду
                       # _series[token] (TimeSeries; пишется primary-значение
                       # при чтении, hex/ascii не захватываются),
                       # clear_series() — регистры + выражения (Clear графика),
                       # _clear_register_series() — только регистры,
                       # спарклайн — sizePolicy Expanding/Expanding (min 60x20):
                       # растягивается вслед за шириной колонки, paintEvent
                       # рисует по фактическим width()/height(),
                       # изменившееся при чтении значение подсвечивается
                       # зелёным на ~2 с (theme.flash_color(), по токену строки,
                       # с генерацией),
                       # фильтр по имени/типу/адресу (QLineEdit) и кнопка
                       # "Sort by address" — физическая перестановка строк,
                       # токены и pending-запросы сохраняются,
                       # Ctrl+Up/Down — сдвиг выбранных строк блоком
                       # (_swap_rows — клон пунктов + обмен состоянием
                       # виджетов, БЕЗ removeCellWidget: его отрыв при
                       # перестройке строк роняет позднее updateEditorGeometries
                       # на смене темы; выделение следует за строками;
                       # правый клик по выделенной строке не схлопывает
                       # мультивыделение),
                       # "Mask write (0x16)…" — диалог mask write, после успеха
                       # перечитываются строки, покрывающие адрес,
                       # "Read/Write (0x17)…" — диалог read/write registers,
                       # прочитанные значения пишутся в лог,
                       # Enter в колонке New value = запись (числовые форматы —
                       # ввод в формате отображения через parse_formatted_values,
                       # ascii — обычный текст через encode_ascii_values,
                       # dec/hex и биты — сырые значения), Ctrl/Cmd+R =
                       # чтение
                       # текущей строки, Ctrl/Cmd+Shift+R = Read all, удаление строки — иконка-крестик,
                       # быстрые действия над текущей строкой (Ctrl+C копия
                       # значения, Ctrl+0/1 запись 0/1, Ctrl+= (и Ctrl++ на
                       # нампаде) / Ctrl+- шаг по
                       # последнему RAW-значению (_last_values по токену,
                       # clamp 0..0xFFFF / 0..1 для coils, молча), Ctrl+T
                       # toggle; coils пишутся bool, input/discrete —
                       # logLine "read-only area"; общий emit — _emit_write)
                       # и контекстное меню таблицы с теми же действиями
                       # (правый клик сначала выбирает строку; "Clear history"
                       # — только на колонке Trend, чистит историю регистров
                       # через _clear_register_series, выражения не трогает),
  scanner_panel.py     # сканер unit-адресов и адресов регистров (секция
                       # Registers scan); открывается отдельным окном; кнопки —
                       # иконочные с тултипами (icons.make_button);
                       # set_bus_enabled(ok) — гейтинг Start-кнопок (Stop
                       # доступны всегда), по умолчанию выключены; двойной
                       # клик по найденному unit выбирает его в панели
                       # подключения (unitSelected); probes-таблица — текстовые
                       # ячейки (dec/0x-hex), невалидные строки пропускаются;
                       # кнопка "Add selected to table" — найденные адреса
                       # строками в таблицу (чек-лист, по умолчанию все
                       # отмечены, All/None; rowsAddRequested(list[dict]),
                       # дубли kind+address пропускает add_rows);
                       # результаты Registers scan — QTableWidget с набором
                       # колонок по kind: Address+Bool (coils/di) или
                       # Address+dec/hex/s16/u32/s32/f32/ascii (регистровые;
                       # dec/hex/s16 — по первому регистру, 32-битные — по
                       # паре, откат count=1 на границе карты → «—», порядок
                       # байт глобальный ABCD; 64-битные форматы НЕ показываем
                       # — count=4 на адрес слишком дорого для сканера);
                       # декодирование — _format_scan_values поверх
                       # models.format_register_values, своих конвертеров нет;
                       # чекбокс на Address-ячейке, значения в таблицу
                       # регистров не переносятся (только kind+address);
                       # кнопка "Device ID…" — идентификация выбранного unit
                       # (deviceIdRequested(id, unit), немодальный диалог);
                       # state()/set_state() — диапазон, probes и параметры
                       # Registers scan сохраняются в настройках
  log_panel.py         # панель лога внизу главного окна, скрываемая кнопкой Log;
                       # чекбокс Raw (выкл. по умолчанию) показывает raw-кадры
                       # шины (append_raw), буфер (is_raw, текст) по 5000 строк
                       # каждого вида, перерисовка при переключении;
                       # иконка save — выгрузка всего лога (нормальный + raw)
                       # в файл, иконка clear — очистка
  settings_store.py    # load_settings()/save_settings() — JSON в ~/.modbus_connector/
  templates/      # пакет-каталог шаблонов устройств:
                  # <Manufacturer>/<Device>.json — карта регистров + дефолтные
                  # настройки подключения ({"name", "description", "connection",
                  # "registers": [...]}, адреса 0-based PDU); package data —
                  # в бандл попадают через package-data в pyproject.toml и
                  # --add-data в build.sh/build.bat (spec-файл gitignored и
                  # сборкой не используется). Лоадер — templates/__init__.py
                  # (НЕ templates.py: пакет перекроет одноимённый модуль);
                  # чистый Python без Qt, чтение через importlib.resources
                  # (работает и из PyInstaller-бандла): TemplateInfo(name,
                  # manufacturer, resource, description), list_templates()
                  # (сортировка производитель→имя, битые JSON пропускаются
                  # с warning), load_template(info | "Manufacturer/Device") —
                  # dict, пригодный для SessionWidget.set_state
  session_widget.py # SessionWidget — одна Modbus-сессия: ConnectionPanel +
                    # RegistersPanel + LogPanel + ScannerPanel (окно) +
                    # ModbusWorker в QThread, вся проводка сигналов внутри;
                    # режим Master/Slave/Sniffer: тонкая строка (QLabel "Mode:" +
                    # FitComboBox, ключи master/slave/sniffer в itemData) между
                    # панелью подключения и центральным QStackedWidget
                    # (registers_panel | sim_panel | sniffer_panel); в
                    # slave/sniffer connection_panel и кнопки Scanner…/Graph…
                    # скрываются, LogPanel общая;
                    # комбо режима disabled, пока master подключён,
                    # sim-сервер запущен или активен сниффинг (_sync_mode_lock);
                    # SimPanel + SimWorker во втором QThread, SnifferPanel +
                    # SnifferWorker в третьем (создаются сразу,
                    # shutdown() останавливает все: invokeMethod shutdown
                    # BlockingQueuedConnection);
                    # заголовок вкладки в slave — describe_sim(params) при
                    # запущенном сервере, иначе "Simulator"; в sniffer —
                    # sniffing_description() при активном сниффинге, иначе
                    # "Sniffer";
                    # connectionChanged → set_bus_enabled(ok) панели/сканера/
                    # окна графика, при разрыве — stop_logging + stop_polling;
                    # state()/set_state() (mode+connection+registers+
                    # registers_options+expressions+logging+scanner+sim+sniffer;
                    # mode применяется ДО панелей, старые state без mode —
                    # master, backward compat со старым плоским форматом),
                    # shutdown() — stop_logging + корректная остановка
                    # worker/потока;
                    # сигналы statsUpdated(object) и titleChanged(str)
                    # ("New connection" → описание соединения), last_stats()
  main_window.py  # главное окно: QTabWidget с SessionWidget'ами (кнопка "+"
                  # — иконка add — в углу, последнюю вкладку закрыть нельзя),
                  # меню File
                  # (save/load всех вкладок), меню Templates («Шаблоны») между
                  # File и View — подменю по производителям из list_templates(),
                  # пункт-устройство открывает НОВУЮ вкладку и применяет шаблон
                  # через set_state (заголовок вкладки — имя шаблона до
                  # подключения; пустой каталог → disabled "(empty)"),
                  # меню View — радио-переключатели
                  # темы (System/Light/Dark) и языка (English/Русский,
                  # QActionGroup'ы), languageChanged → _retranslate окна и
                  # сессий, смена темы → icons.refresh_icons() (цвет иконок
                  # запечён при рендере), статус-бар следует
                  # активной вкладке; настройки: {"theme": str,
                  # "language": str,
                  # "tabs": [...], "active_tab": i} (theme/language отсутствуют
                  # в старых файлах → "system"/по локали),
                  # старый односессионный формат читается как одна вкладка
  theme.py        # тема (pyqtdarktheme): THEMES, apply_theme(name) —
                  # system→"auto" (вызывать после QApplication),
                  # current_theme(), is_dark() (system → QStyleHints
                  # .colorScheme, откат на яркость палитры);
                  # темо-зависимые цвета: graph_colors() (bg/fg для
                  # pyqtgraph), crosshair_color(), flash_color(),
                  # sparkline_color() (тёмная — светло-синяя #7aa2f7, светлая —
                  # palette Highlight),
                  # status_colors(); diff_color() — оранжевая подсветка
                  # различающихся строк в окне snapshot diff;
                  # FitComboBox — QComboBox, чей попап
                  # растягивается по самому длинному пункту в showPopup()
                  # (stylesheet-тема прижимает попап к ширине комбо, size-hints
                  # делегата на cocoa занижены → ширина по fontMetrics);
                  # используется для ВСЕХ комбобоксов;
                  # pyqtgraph НЕ импортируется (ленивая
                  # загрузка numpy сохранена)
  icons.py        # темо-зависимые line-иконки тулбар-кнопок (стиль
                  # Lucide/Feather, контур 1.6 px, RoundCap/RoundJoin,
                  # рисуются QPainter'ом на лету — файлов/зависимостей нет,
                  # рендер в size*2 с devicePixelRatio=2 для retina):
                  # icon(name, size), ICON_NAMES;
                  # make_button(text, icon_name, checkable) — компактная
                  # QToolButton ToolButtonIconOnly: иконка на кнопке, подпись
                  # в toolTip/accessibleName, text() сохраняется (тесты на
                  # него опираются); register(btn, name) — слабый реестр
                  # (WeakKeyDictionary, уже удалённые C++-объекты при
                  # refresh пропускаются); refresh_icons() перерисовывает
                  # иконки после смены темы (вызывается из
                  # MainWindow._on_theme_selected); цвет контура: тёмная
                  # тема — #E0E0E0, светлая — QPalette.ButtonText
                  # (pyqtdarktheme красит stylesheet'ом, палитру не меняет)
  i18n.py         # мини-i18n (en/ru) без .ts/.qm: tr(text, **fmt) — английский
                  # текст является ключом, RU dict — перевод (нет ключа →
                  # английский); set_language(None → по QLocale, мусор → "en"),
                  # current_language(), сигнал languageChanged(str);
                  # переводятся ТОЛЬКО display-строки — RegisterKind/форматы/
                  # порядки/значения настроек не переводятся никогда;
                  # у виджетов — retranslate() по сохранённым английским
                  # ключам; диалоги читают язык при открытии; лог —
                  # в момент эмиссии; настройки: "language" рядом с "theme"
  app.py          # QApplication, configure_qt() (контекстные меню показывают
                  # хоткеи и на macOS — AA_DontShowShortcutsInContextMenus
                  # =False до создания app), apply_theme(из настроек),
                  # set_language(из настроек), entry point main()
  __main__.py     # python -m modbus_connector
tests/
  conftest.py     # фикстура modbus_server (порт): asyncio-ModbusTcpServer на
                  # 127.0.0.1, свободный порт, отдельный поток с собственным
                  # event loop; slaves={1: ...} (single=False), zero_mode=True;
                  # hr 0..9 = 100..109, ir 0..4 = 7..11,
                  # coils 0..7 = True/False чередуя, di 0..7 = с False; unit_id = 1;
                  # identity (0x2B/0x0E): pymodbus/test-server/1.0;
                  # фикстура modbus_rtu_server — то же с framer=RTU
  test_models.py  # parse_values/format_values, bitmask-хелперы
                  # (set_bit_labels/bits_to_value/format_bitmask_value)
  test_backend.py # ModbusBackend против modbus_server: read/write/scan
  test_datalogger.py  # DataLogger: csv/jsonl, subset полей, append/overwrite
  test_registers_panel.py  # offscreen Qt тесты таблицы регистров, в т.ч.
                           # bitmask: state round-trip, отображение, кнопка
                           # битов → BitsDialog → запись, live-чекбокс в
                           # Display…, coils не затрагиваются
  test_alarms_dialog.py   # диалог алармов (add/edit/remove/round-trip) и
                          # алармы панели: подсветка, edge-лог, state, hex/ascii
  test_snapshot_diff.py   # snapshot diff: гейтинг кнопок, подсветка изменённых
                          # строк, фильтр Only differences, строки без данных,
                          # Refresh/Take new snapshot, перезапись снапшота,
                          # "(removed)" для удалённых строк
  test_session_widget.py   # smoke: state round-trip + shutdown сессии;
                           # режимы Master/Slave/Sniffer: mode+sim+sniffer в
                           # state round-trip, видимость панелей, блокировка
                           # комбо режима, заголовок вкладки, shutdown в
                           # slave/sniffer
  test_sim_backend.py      # SimBackend: start/stop TCP/RTU, занятый порт,
                           # записи мастера (хук), set/get_values
  test_sim_worker.py       # SimWorker в QThread: start/stop, masterWrote,
                           # requestLine, clientChanged, tick, set/get через
                           # invokeMethod (маршаллинг Q_ARG/Q_RETURN_ARG)
  test_sim_panel.py        # SimPanel: add/remove строк, state round-trip
                           # (server+rows, толерантный разбор), шаблон в карту,
                           # masterWrote → Value, правка Value →
                           # setValuesRequested, start эмитит params+push строк,
                           # bitmask: отображение, кнопка → BitsDialog →
                           # setValuesRequested, expression-строка без кнопки
  test_sniffer_backend.py  # RtuFrameParser/BusModel/SnifferBackend: разбор
                           # кадров, направление tx/rx, карта значений, хуки
  test_sniffer_worker.py   # SnifferWorker в QThread: start/stop, сигналы
                           # valuesChanged/frameLine/frameForUnit
  test_sniffer_panel.py    # SnifferPanel: вкладки unit'ов, таблица (сортировка,
                           # flash, форматы, тренд), state round-trip,
                           # start/stop-сигналы, акцессоры графика +
                           # GraphWindow вкладки (poll-кнопка скрыта),
                           # CSV-экспорт round-trip через rows_from_csv,
                           # help-кнопка
  test_sim_rules.py        # правила SimPanel: rule/rule_text/tick_ms в state,
                           # «⚠» при невалидном, гейтинг редактируемости ячеек,
                           # apply_rules ([a]*2, prev-счётчик, t, rand/randint),
                           # nan/нет dep → «—» без записи, encode f32/u32/s16/coils
  test_main_window_tabs.py # вкладки: round-trip настроек, старый формат,
                           # закрытие вкладок
  test_scanner_panel.py    # probes-таблица (текстовые ячейки, пропуск
                           # невалидных), round-trip настроек сканера,
                           # колонки значений результатов Registers scan
                           # (Bool для coils/di, dec..ascii для регистров,
                           # «—» для 32-бит при одиночном регистре)
  test_timeseries.py       # TimeSeries: буфер, вытеснение, stats
  test_theme.py            # apply_theme (stylesheet меняется), round-trip
                           # ключа "theme", меню View (эксклюзивность)
  test_i18n.py             # tr() fallback, set_language, меню Language
                           # (retranslate окна/панелей), round-trip "language",
                           # тип подключения в state всегда английский,
                           # заголовки/кнопки таблицы, диалоги CSV, шаблоны
                           # лога worker'а, сканер, окно графика (режимы в
                           # itemData), справка на двух языках
  test_graph_window.py     # чек-лист рядов, маркеры stats, Follow, zoom→Manual
  test_icons.py            # рендер всех иконок (непустые пиксели, HiDPI,
                           # масштаб), make_button (text/tooltip/accessibleName),
                           # refresh_icons: перерисовка, перекраска после смены
                           # темы, толерантность к удалённым кнопкам
  test_templates.py        # каталог шаблонов: list/load, сортировка, битые
                           # JSON, round-trip регистров через set_state
  test_templates_menu.py   # меню Templates: структура подменю, открытие
                           # вкладки по шаблону, retranslate заголовка
  test_expressions.py      # блок выражений: add/edit/delete, пересчёт по
                           # scaled primary зависимости, «—» при нет dep и
                           # nan, «⚠» + tooltip при невалидном, series только
                           # в record-режиме, state round-trip (с "alarms"),
                           # видимость в
                           # options, "fx …" в чек-листе графика,
                           # переименование строки-зависимости,
                           # Clear графика чистит expr series (а "Clear
                           # history" таблицы — нет), справка блока, алармы
                           # на выражения (fx-строки в диалоге, подсветка,
                           # edge-лог, «—»/«⚠» не алармят), completer
                           # (модель, переименование, вставка)
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
                          # шаблоны templates/ попадают в бандл: --add-data
                          # в build.sh/build.bat + package-data в pyproject.toml
                          # иконка: assets/icon.icns (macOS) / icon.ico (Windows) / icon.png (Linux),
                          # источник — assets/icon.png (генерируется скриптом, см. git history)
build.bat                 # то же под Windows (cmd): dist\ModbusConnector\ModbusConnector.exe
```

CI: `.github/workflows/build.yml` — push в main / ручной запуск; матрица
macOS/Windows/Linux: pytest (по одному процессу на test-файл — изоляция от
флаки-сегфолтов Qt на CI), затем сборка через build.sh/build.bat,
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


# Coding guide

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.