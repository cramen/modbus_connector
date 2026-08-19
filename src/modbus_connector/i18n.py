"""Мини-i18n без .ts/.qm: английский исходник — ключ, перевод — в RU.

Только отображаемые строки: внутренние значения (RegisterKind, форматы,
порядки байт, ключи настроек) никогда не переводятся — они persist'ятся в JSON.
Переводы добавляются инкрементально; отсутствующий ключ → английский.
"""

from PySide6.QtCore import QLocale, QObject, Signal

LANGUAGES = ("en", "ru")

RU: dict[str, str] = {
    # main window
    "File": "Файл",
    "Save Settings to File…": "Сохранить настройки в файл…",
    "Load Settings from File…": "Загрузить настройки из файла…",
    "View": "Вид",
    "Theme": "Тема",
    "Language": "Язык",
    "System": "Системная",
    "Light": "Светлая",
    "Dark": "Тёмная",
    "Save Settings": "Сохранить настройки",
    "Load Settings": "Загрузить настройки",
    "Templates": "Шаблоны",
    "(empty)": "(пусто)",
    "Tx: {total}  Err: {errors} ({percent:.1f}%)  Avg: {avg:.0f} ms":
        "Запросов: {total}  Ошибок: {errors} ({percent:.1f}%)  Среднее: {avg:.0f} мс",
    "  top: {kind}": "  частая: {kind}",
    "no errors yet": "ошибок пока нет",
    # session widget
    "New connection": "Новое подключение",
    "Scanner…": "Сканер…",
    "Graph…": "График…",
    "Log": "Лог",
    "Modbus Scanner": "Сканер Modbus",
    # connection panel
    "Host:": "Хост:",
    "Port:": "Порт:",
    "Baud:": "Скорость:",
    "Bits:": "Биты:",
    "Parity:": "Чётность:",
    "Stop:": "Стоп:",
    "Unit:": "Юнит:",
    "Timeout:": "Таймаут:",
    "Connect": "Подключиться",
    "Disconnect": "Отключиться",
    "Device ID…": "ID устройства…",
    "Diagnostics…": "Диагностика…",
    "Refresh": "Обновить",
    "Serial-line diagnostics (0x08); some TCP devices answer it too":
        "Диагностика последовательной линии (0x08); "
        "некоторые TCP-устройства тоже отвечают",
    "Disconnected": "Отключено",
    "(idle)": "(простой)",
    "Invalid settings": "Неверные настройки",
    "RTU over TCP": "RTU через TCP",
    "RTU over UDP": "RTU через UDP",
    "Device identification (0x2B/0x0E)": "Идентификация устройства (0x2B/0x0E)",
    "Reading…": "Чтение…",
    "(device reported no objects)": "(устройство не вернуло объекты)",
    "Diagnostics (function 0x08)": "Диагностика (функция 0x08)",
    "Loopback": "Эхо-запрос",
    "Clear counters": "Сбросить счётчики",
    "mismatch": "не совпадает",
    # registers panel
    "Name": "Имя",
    "Type": "Тип",
    "Address": "Адрес",
    "Count": "Кол-во",
    "Unit ID": "Unit ID",
    "Poll, ms": "Poll, мс",
    "Format": "Формат",
    "Value": "Значение",
    "New value": "Новое значение",
    "Trend": "Тренд",
    "Enter in 'New value' = write raw values (no scale/offset applied), "
    "Ctrl/Cmd+R = read current row, Ctrl/Cmd+Shift+R = read all rows":
        "Enter в «New value» — запись сырых значений (без scale/offset), "
        "Ctrl/Cmd+R — чтение текущей строки, Ctrl/Cmd+Shift+R — прочитать все",
    "Add register": "Добавить регистр",
    "Read all": "Прочитать все",
    "Read every row once (Ctrl/Cmd+Shift+R)":
        "Прочитать каждую строку один раз (Ctrl/Cmd+Shift+R)",
    "Sort by address": "Сортировать по адресу",
    "Mask write (0x16)…": "Запись по маске (0x16)…",
    "Set or clear individual bits of a holding register without touching "
    "others: result = (value AND and-mask) OR (or-mask AND NOT and-mask). "
    "Typical use: bit fields in PLC configuration registers.":
        "Установить или сбросить отдельные биты holding-регистра, не трогая "
        "остальные: result = (value AND and-mask) OR (or-mask AND NOT and-mask). "
        "Типичное применение — битовые поля в регистрах конфигурации ПЛК.",
    "Read/Write (0x17)…": "Чтение/запись (0x17)…",
    "Atomic transaction: write holding registers and read others in a "
    "single Modbus exchange (function 0x17). Used when a device requires "
    "read-modify-write without a race window.":
        "Атомарная транзакция: запись holding-регистров и чтение других "
        "в одном обмене Modbus (функция 0x17). Нужна, когда устройство требует "
        "read-modify-write без гонок.",
    "Filter…": "Фильтр…",
    "Display…": "Отображение…",
    "Per-row Scale/Offset/Unit and byte order settings":
        "Scale/Offset/Unit и порядок байт для каждой строки",
    "Import/export the register table as CSV":
        "Импорт/экспорт таблицы регистров в CSV",
    "Import table…": "Импорт таблицы…",
    "Export…": "Экспорт…",
    "Log to file": "Запись в файл",
    "Logging settings…": "Настройки записи…",
    "Log read values to a file (CSV or JSON Lines)":
        "Записывать прочитанные значения в файл (CSV или JSON Lines)",
    "Logging to {path} — click to stop":
        "Запись в {path} — нажмите для остановки",
    "Order:": "Порядок:",
    "Default byte order for 32/64-bit formats "
    "(rows without an explicit order inherit it)":
        "Порядок байт по умолчанию для 32/64-битных форматов "
        "(строки без явного порядка наследуют его)",
    "Interval:": "Интервал:",
    "Poll all rows with the Interval period; the dropdown chooses whether "
    "value history is recorded for sparklines and the graph window "
    "(bounded buffer, ~10k samples per row)":
        "Опрос всех строк с заданным интервалом; в выпадающем меню выбирается, "
        "записывать ли историю значений для спарклайнов и окна графика "
        "(буфер ограничен ~10 тыс. отсчётов на строку)",
    "Start polling": "Начать опрос",
    "Start polling and record": "Начать опрос с записью",
    "Stop polling": "Остановить опрос",
    "Modbus unit 1..247, empty = unit from the connection panel":
        "Modbus unit 1..247, пусто = unit из панели подключения",
    "Per-row poll interval in ms, empty = global interval; "
    "a row with its own interval is polled by a dedicated timer":
        "Интервал опроса строки в мс, пусто = общий интервал; "
        "строку со своим интервалом опрашивает отдельный таймер",
    "Display format (registers only; coils/discrete show 0/1)":
        "Формат отображения (только регистры; coils/discrete показывают 0/1)",
    "Delete row": "Удалить строку",
    "Poll this row": "Опрашивать строку",
    # expressions block
    "Expressions": "Выражения",
    "Computed rows over register values ([name] references), "
    "with their own sparklines and graph series":
        "Вычисляемые строки над значениями регистров (ссылки [имя]), "
        "со своими спарклайнами и рядами графика",
    "Add expression": "Добавить выражение",
    "Add a computed row; [name] references a register row's "
    "scaled value, e.g. ([temp] + [flow]) / 2":
        "Добавить вычисляемую строку; [имя] — ссылка на масштабированное "
        "значение строки регистров, например ([temp] + [flow]) / 2",
    "Expression": "Выражение",
    "Delete expression": "Удалить выражение",
    "Expressions compute over scaled row values: [name] is a row "
    "reference, functions abs/sqrt/sin/… and pi/e are available":
        "Выражения вычисляются над масштабированными значениями строк: "
        "[имя] — ссылка на строку, доступны функции abs/sqrt/sin/… и pi/e",
    "Move up": "Переместить вверх",
    "Move down": "Переместить вниз",
    "Copy value": "Копировать значение",
    "Write 0": "Записать 0",
    "Write 1": "Записать 1",
    "Increment": "Увеличить",
    "Decrement": "Уменьшить",
    "Toggle": "Переключить",
    "Clear history": "Очистить историю",
    # registers panel: dialogs and log lines
    "Per-row display settings": "Настройки отображения строк",
    "Scale": "Scale",
    "Offset": "Offset",
    "Unit": "Ед.",
    "default": "по умолчанию",
    "Rows added or deleted while this dialog is open appear after reopening it.":
        "Строки, добавленные или удалённые при открытом диалоге, "
        "появятся после его повторного открытия.",
    "Mask write (function 0x16)": "Mask write (функция 0x16)",
    "empty = global unit": "пусто = общий unit",
    "Set/clear bits of one holding register:\n"
    "result = (value AND and-mask) OR (or-mask AND NOT and-mask).\n"
    "Masks accept decimal or hex (e.g. 0xFF0F).":
        "Установка/сброс битов одного holding-регистра:\n"
        "result = (value AND and-mask) OR (or-mask AND NOT and-mask).\n"
        "Маски принимаются в dec или hex (например, 0xFF0F).",
    "AND mask:": "AND-маска:",
    "OR mask:": "OR-маска:",
    "Read/Write multiple registers (function 0x17)":
        "Read/Write нескольких регистров (функция 0x17)",
    "comma/space separated, hex ok": "через запятую/пробел, можно hex",
    "One atomic exchange: write Values at Write address, then read\n"
    "Read count registers from Read address; read values go to the log.\n"
    "Addresses accept decimal or hex (e.g. 0x10).":
        "Один атомарный обмен: записать Values по Write address, затем прочитать\n"
        "Read count регистров с Read address; прочитанное попадает в лог.\n"
        "Адреса принимаются в dec или hex (например, 0x10).",
    "Write address:": "Адрес записи:",
    "Values:": "Значения:",
    "Read address:": "Адрес чтения:",
    "Read count:": "Кол-во чтения:",
    "Import registers from CSV": "Импорт регистров из CSV",
    "Export registers to CSV": "Экспорт регистров в CSV",
    "✗ failed to read {path}: {exc}": "✗ не удалось прочитать {path}: {exc}",
    "✗ failed to import {path}: {exc}": "✗ не удалось импортировать {path}: {exc}",
    "✗ failed to write {path}: {exc}": "✗ не удалось записать {path}: {exc}",
    "✗ failed to load template {name}: {exc}": "✗ не удалось загрузить шаблон {name}: {exc}",
    "← imported {count} rows from {path}":
        "← импортировано строк: {count} из {path}",
    "→ exported {count} rows to {path}": "→ экспортировано строк: {count} в {path}",
    "✗ export: no columns selected": "✗ экспорт: не выбраны колонки",
    "✗ mask write: invalid address/mask (dec or 0x… hex)":
        "✗ mask write: неверный адрес/маска (dec или 0x… hex)",
    "✗ mask write: address/mask out of range 0..0xFFFF":
        "✗ mask write: адрес/маска вне диапазона 0..0xFFFF",
    "✗ read/write: invalid address (dec or 0x… hex)":
        "✗ read/write: неверный адрес (dec или 0x… hex)",
    "✗ read/write: address out of range 0..0xFFFF":
        "✗ read/write: адрес вне диапазона 0..0xFFFF",
    "✗ parse error: {exc}": "✗ ошибка разбора: {exc}",
    "← read/write read values: {values}": "← read/write прочитано: {values}",
    "✗ row {row}: invalid address/count": "✗ строка {row}: неверный адрес/кол-во",
    "✗ row {row}: {kind} is a read-only area":
        "✗ строка {row}: область {kind} только для чтения",
    "✗ row {row}: read the row before +/-":
        "✗ строка {row}: сначала прочитайте строку (для +/-)",
    "✗ row {row}: read the row before toggling":
        "✗ строка {row}: сначала прочитайте строку (для переключения)",
    "← scanner: added {added} rows to the table":
        "← сканер: добавлено строк в таблицу: {added}",
    ", skipped {skipped} duplicates": ", пропущено дублей: {skipped}",
    "✗ logging: cannot open {path}: {exc}":
        "✗ запись: не удалось открыть {path}: {exc}",
    "→ logging values to {path} ({format})":
        "→ запись значений в {path} ({format})",
    "← logging stopped: {rows} rows written to {path}":
        "← запись остановлена: записано строк: {rows} в {path}",
    # alarms dialog and log lines
    "Alarms…": "Алармы…",
    "Per-row alarm rules: highlight, log and beep when the scaled "
    "value matches a condition":
        "Правила алармов по строкам: подсветка, лог и звук, когда "
        "масштабированное значение совпадает с условием",
    "Alarm rules": "Правила алармов",
    "Condition": "Условие",
    "Value 2": "Значение 2",
    "Color": "Цвет",
    "Sound": "Звук",
    "in range": "в диапазоне",
    "outside range": "вне диапазона",
    "red": "красный",
    "yellow": "жёлтый",
    "Add": "Добавить",
    "Remove": "Удалить",
    "Up": "Вверх",
    "Down": "Вниз",
    "The first matching rule wins; Value 2 is used by the range conditions":
        "Срабатывает первое совпавшее правило; Значение 2 используется "
        "условиями диапазона",
    "Rule value must be a number": "Значение правила должно быть числом",
    "ALARM {label}: {value} {condition}": "АЛАРМ {label}: {value} {condition}",
    "ALARM cleared {label}": "АЛАРМ снят: {label}",
    # snapshot diff
    "Snapshot": "Снапшот",
    "Remember current values of all rows for later comparison":
        "Запомнить текущие значения всех строк для последующего сравнения",
    "Diff…": "Сравнение…",
    "Compare the snapshot with the current values":
        "Сравнить снапшот с текущими значениями",
    "Snapshot taken: {count} rows": "Снапшот снят: строк: {count}",
    "Snapshot diff": "Сравнение со снапшотом",
    "Snapshot taken at {time}": "Снапшот снят в {time}",
    "Only differences": "Только различия",
    "Take new snapshot": "Снять новый снапшот",
    "Current": "Текущее",
    "(removed)": "(удалена)",
    # CSV dialogs
    "Export CSV": "Экспорт CSV",
    "Choose columns to export and their order":
        "Выберите колонки для экспорта и их порядок",
    "Select all": "Выбрать все",
    "Select none": "Снять выбор",
    "Import CSV": "Импорт CSV",
    "Match file columns to register fields":
        "Сопоставьте колонки файла полям регистров",
    "File column": "Колонка файла",
    "Maps to": "Соответствует",
    "— skip —": "— пропустить —",
    "Map the essential fields: {fields}":
        "Сопоставьте обязательные поля: {fields}",
    # logging settings dialog
    "Logging settings": "Настройки записи",
    "Log file path": "Путь к файлу записи",
    "Browse…": "Обзор…",
    "Timestamp": "Метка времени",
    "Row name": "Имя строки",
    "Register type": "Тип регистра",
    "Append to the file if it exists": "Дописывать в существующий файл",
    "Rows to log": "Записываемые строки",
    "File:": "Файл:",
    "Format:": "Формат:",
    "Fields:": "Поля:",
    "Log values to file": "Запись значений в файл",
    "Choose a log file": "Выберите файл записи",
    # log panel
    "Clear": "Очистить",
    "Raw": "Raw",
    "Save…": "Сохранить…",
    "Save Log": "Сохранить лог",
    "✗ failed to save log to {path}: {exc}":
        "✗ не удалось сохранить лог в {path}: {exc}",
    "→ log saved to {path}": "→ лог сохранён в {path}",
    # worker log lines
    "Connected ({desc})": "Подключено ({desc})",
    "→ connect {desc}": "→ подключение {desc}",
    "✗ connect failed: {exc}": "✗ подключение не удалось: {exc}",
    "← connected": "← подключено",
    "✗ disconnect failed: {exc}": "✗ отключение не удалось: {exc}",
    "→ disconnect": "→ отключение",
    "→ read {kind} unit={unit} addr={address} count={count}":
        "→ чтение {kind} unit={unit} адр={address} кол-во={count}",
    "✗ read failed: {exc}": "✗ чтение не удалось: {exc}",
    "→ write {kind} unit={unit} addr={address} values={values}":
        "→ запись {kind} unit={unit} адр={address} значения={values}",
    "✗ write failed: {exc}": "✗ запись не удалась: {exc}",
    "← ok": "← ok",
    "→ mask write unit={unit} addr={address} and=0x{and_mask:04x} "
    "or=0x{or_mask:04x}":
        "→ mask write unit={unit} адр={address} and=0x{and_mask:04x} "
        "or=0x{or_mask:04x}",
    "✗ mask write failed: {exc}": "✗ mask write не удался: {exc}",
    "→ read/write unit={unit} read@{read_address} x{read_count} "
    "write@{write_address} values={values}":
        "→ read/write unit={unit} чтение@{read_address} x{read_count} "
        "запись@{write_address} значения={values}",
    "✗ read/write failed: {exc}": "✗ read/write не удался: {exc}",
    "→ read device id unit={unit}": "→ чтение ID устройства unit={unit}",
    "✗ read device id failed: {exc}": "✗ чтение ID устройства не удалось: {exc}",
    "← device id: {count} objects": "← ID устройства: объектов: {count}",
    "→ diag loopback unit={unit}": "→ diag эхо unit={unit}",
    "✗ diag loopback failed: {exc}": "✗ diag эхо не удалось: {exc}",
    "← loopback ok": "← эхо ok",
    "← loopback mismatch": "← эхо не совпадает",
    "→ diag counters unit={unit}": "→ diag счётчики unit={unit}",
    "✗ diag counters failed: {exc}": "✗ diag счётчики не удались: {exc}",
    "← diag counters: {counters}": "← diag счётчики: {counters}",
    "→ diag clear counters unit={unit}": "→ diag сброс счётчиков unit={unit}",
    "✗ diag clear counters failed: {exc}":
        "✗ diag сброс счётчиков не удался: {exc}",
    "← counters cleared": "← счётчики сброшены",
    "→ scan units {start}..{end} ({count} probes)":
        "→ скан unit-адресов {start}..{end} (проб: {count})",
    "← scan hit unit={unit} probes={indices}":
        "← найден unit={unit} пробы={indices}",
    "✗ scan failed: {exc}": "✗ скан не удался: {exc}",
    "← scan stopped": "← скан остановлен",
    "← scan finished": "← скан завершён",
    "→ scan addresses {kind} unit={unit} {start}..{end}":
        "→ скан адресов {kind} unit={unit} {start}..{end}",
    "← addr scan hit {kind}@{address} = {values}":
        "← найден адрес {kind}@{address} = {values}",
    "✗ address scan failed: {exc}": "✗ скан адресов не удался: {exc}",
    "← address scan stopped": "← скан адресов остановлен",
    "← address scan finished": "← скан адресов завершён",
    # main window file dialogs and log lines
    "✗ failed to save settings to {path}: {exc}":
        "✗ не удалось сохранить настройки в {path}: {exc}",
    "→ settings saved to {path}": "→ настройки сохранены в {path}",
    "✗ failed to load settings from {path}: {exc}":
        "✗ не удалось загрузить настройки из {path}: {exc}",
    "✗ failed to load settings from {path}: not an object":
        "✗ не удалось загрузить настройки из {path}: не объект",
    "← settings loaded from {path}": "← настройки загружены из {path}",
    # session widget log line
    "→ unit {unit} selected in connection panel":
        "→ unit {unit} выбран в панели подключения",
    # scanner panel
    "Add probe": "Добавить пробу",
    "Start scan": "Начать сканирование",
    "Stop": "Стоп",
    "Delete": "Удалить",
    "Start": "Старт",
    "All": "Все",
    "None": "Ни одного",
    "Add selected to table": "Добавить отмеченные в таблицу",
    "Add one row per checked address to the registers table":
        "Добавить по строке на каждый отмеченный адрес в таблицу регистров",
    "Double-click a unit to select it for the connection":
        "Двойной клик по unit выбирает его для подключения",
    "Read the selected unit's identification (function 0x2B/0x0E)":
        "Прочитать идентификацию выбранного unit (функция 0x2B/0x0E)",
    "Unit range:": "Диапазон unit:",
    "Type:": "Тип:",
    "Addresses:": "Адреса:",
    "Address:": "Адрес:",
    "Results:": "Результаты:",
    "Registers scan:": "Скан регистров:",
    "Unit {unit} — device identification (0x2B/0x0E)":
        "Unit {unit} — идентификация устройства (0x2B/0x0E)",
    "Unit {unit}: {labels}": "Unit {unit}: {labels}",
    "Bool": "Логич.",
    # graph window
    "Graph": "График",
    "Refresh rows": "Обновить строки",
    "Follow": "Следом",
    "Full": "Всё",
    "Manual": "Вручную",
    "Zoom rect": "Зум рамкой",
    "Markers": "Маркеры",
    "Reset view": "Сбросить вид",
    "Clear recorded history and restart the time axis":
        "Очистить записанную историю и перезапустить ось времени",
    "Poll the register table and record value history for this graph":
        "Опрос таблицы регистров с записью истории для этого графика",
    "X scale:": "Шкала X:",
    "Series:": "Ряды:",
    "Series": "Ряды",
    "Min": "Мин",
    "Max": "Макс",
    "Avg": "Сред",
    "time, s (relative)": "время, с (относительное)",
    "Δt = {dt:.4g} s": "Δt = {dt:.4g} с",
    # help dialog
    "Help": "Справка",
    "New connection tab": "Новая вкладка-подключение",
    "Registers — Help": "Таблица регистров — справка",
    "Graph — Help": "График — справка",
    "Scanner — Help": "Сканер — справка",
    "Expressions — Help": "Выражения — справка",
}


class _LanguageNotifier(QObject):
    changed = Signal(str)


_notifier = _LanguageNotifier()
languageChanged = _notifier.changed  # подключается к retranslate() виджетов

_current: str | None = None


def _detect() -> str:
    return "ru" if QLocale.system().name().lower().startswith("ru") else "en"


def current_language() -> str:
    global _current
    if _current is None:
        _current = _detect()
    return _current


def set_language(name: str | None) -> None:
    """Переключить язык; None — определить по системе, неизвестное → "en"."""
    global _current
    if name is None:
        new = _detect()
    else:
        new = name if name in LANGUAGES else "en"
    changed = new != current_language()
    _current = new
    if changed:
        _notifier.changed.emit(new)


def tr(text: str, **fmt: object) -> str:
    """Перевести строку на текущий язык; без перевода остаётся английской."""
    if current_language() == "ru":
        text = RU.get(text, text)
    return text.format(**fmt) if fmt else text
