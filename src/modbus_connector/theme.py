"""Тема оформления приложения (system/light/dark) поверх pyqtdarktheme."""

import qdarktheme
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QComboBox, QStyle

from modbus_connector.models import AlarmColor

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


def graph_colors() -> tuple[str, str]:
    """(background, foreground) для pyqtgraph (config options background/
    foreground и перекраска открытых графиков)."""
    return ("k", "d") if is_dark() else ("w", "k")


def crosshair_color() -> QColor:
    """Нейтральная пунктирная вертикаль кроссхейра (не путать с маркерами)."""
    return QColor(170, 170, 170) if is_dark() else QColor(100, 100, 100)


def flash_color() -> QColor:
    """Подсветка изменившейся при чтении ячейки Value."""
    return QColor(45, 95, 50) if is_dark() else QColor(144, 238, 144)


def alarm_color(color: "AlarmColor") -> QColor:
    """Фон ячейки Value при активном аларме (red/yellow), читаемый в обеих темах."""
    if is_dark():
        return QColor(0x7A, 0x24, 0x1E) if color == "red" else QColor(0x6B, 0x58, 0x1B)
    return QColor(0xF5, 0xB7, 0xB1) if color == "red" else QColor(0xF7, 0xE2, 0x8B)


def diff_color() -> QColor:
    """Подсветка различающихся строк в окне сравнения снапшотов (оранжевый,
    не пересекается с зелёным flash и red/yellow алармов)."""
    return QColor(0x8A, 0x53, 0x1B) if is_dark() else QColor(0xFD, 0xD9, 0xA8)


def sparkline_color() -> QColor:
    """Линия спарклайна в колонке Trend: Highlight qdarktheme (#308cc6) на
    тёмном фоне ячейки почти не виден — на тёмной теме светлый синий."""
    if is_dark():
        return QColor(0x7A, 0xA2, 0xF7)
    return QGuiApplication.palette().color(QPalette.ColorRole.Highlight)


def status_colors() -> dict[str, str]:
    """Цвета метки статуса подключения: off/ok/idle."""
    if is_dark():
        return {"off": "#8a8a8a", "ok": "#6fcf6f", "idle": "#e0a050"}
    return {"off": "gray", "ok": "green", "idle": "orange"}


class FitComboBox(QComboBox):
    """QComboBox с попапом по ширине самого длинного пункта.

    Stylesheet-тема прижимает ширину попапа к закрытому комбо (нативный стиль
    macOS мерил по содержимому), а size-hints делегата под stylesheet на cocoa
    занижены, поэтому ширина считается по fontMetrics в момент показа —
    перезаполнение пунктов (список портов RTU и т.п.) учитывается само.
    """

    def showPopup(self) -> None:
        super().showPopup()
        view = self.view()
        fm = view.fontMetrics()
        longest = max(
            (fm.horizontalAdvance(self.itemText(i)) for i in range(self.count())),
            default=0,
        )
        pad = (
            2 * view.frameWidth()
            + view.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
            + 24  # delegate side margins, measured on cocoa
        )
        container = view.window()
        container.setFixedWidth(max(longest + pad, container.width()))
