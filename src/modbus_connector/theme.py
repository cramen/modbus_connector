"""Тема оформления приложения (system/light/dark) поверх pyqtdarktheme."""

import qdarktheme
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

THEMES = ("system", "light", "dark")
_QDARKTHEME = {"system": "auto", "light": "light", "dark": "dark"}
_current = THEMES[0]


def apply_theme(name: str) -> None:
    """Применить тему по имени; вызывать после создания QApplication.
    Неизвестное имя читается как "system" (старые/чужие settings-файлы)."""
    global _current
    _current = name if name in THEMES else THEMES[0]
    qdarktheme.setup_theme(_QDARKTHEME[_current])


def current_theme() -> str:
    return _current


def is_dark() -> bool:
    """Эффективная тема: при "system" — по QStyleHints.colorScheme (Qt 6.5+),
    с откатом на яркость фона палитры, если схема неизвестна."""
    if _current != "system":
        return _current == "dark"
    scheme = QGuiApplication.styleHints().colorScheme()
    if scheme == Qt.ColorScheme.Unknown:
        return QGuiApplication.palette().window().color().lightness() < 128
    return scheme == Qt.ColorScheme.Dark
