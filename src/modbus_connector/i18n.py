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
