"""Темо-зависимые line-иконки кнопок: рисуются QPainter'ом в момент запроса.

Монохромные иконки в стиле Lucide/Feather: контур одной толщины (~1.6 px на
сцене 16x16, RoundCap/RoundJoin), без заливок (кроме парочки точек/бит).
Цвет — theme.is_dark() ? светло-серый : QPalette.ButtonText (pyqtdarktheme
красит stylesheet'ом и палитру не меняет), поэтому после смены темы иконки
нужно перерисовать: refresh_icons() пересоздаёт иконки всех живых кнопок,
зарегистрированных через make_button()/register() (слабый реестр, кнопки
не удерживаются). Рендер идёт в size*2 пикселя с devicePixelRatio=2 (retina).
"""

import math
import weakref
from collections.abc import Callable

from PySide6.QtCore import QLineF, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QToolButton

from modbus_connector import theme

ICON_SIZE = 16
_RENDER_SCALE = 2  # запас для retina: рисуем в size*2, setDevicePixelRatio(2)
_PEN_WIDTH = 1.6

_Drawer = Callable[[QPainter, QColor], None]
_DRAWERS: dict[str, _Drawer] = {}
_REGISTRY: weakref.WeakKeyDictionary[QToolButton, str] = weakref.WeakKeyDictionary()


def _drawer(name: str) -> Callable[[_Drawer], _Drawer]:
    def wrap(fn: _Drawer) -> _Drawer:
        _DRAWERS[name] = fn
        return fn

    return wrap


def _line(p: QPainter, x1: float, y1: float, x2: float, y2: float) -> None:
    p.drawLine(QLineF(x1, y1, x2, y2))


def _poly(p: QPainter, *pts: tuple[float, float], close: bool = False) -> None:
    points = [QPointF(x, y) for x, y in pts]
    if close:
        points.append(points[0])
    p.drawPolyline(points)


def _dot(p: QPainter, color: QColor, x: float, y: float, r: float = 1.2) -> None:
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawEllipse(QPointF(x, y), r, r)
    p.restore()


@_drawer("connect")
def _connect(p: QPainter, _c: QColor) -> None:
    # звено цепи: два крюка и перемычка
    p.drawArc(QRectF(2.0, 4.5, 7.0, 7.0), 90 * 16, 180 * 16)
    p.drawArc(QRectF(7.0, 4.5, 7.0, 7.0), 270 * 16, 180 * 16)
    _line(p, 5.5, 8.0, 10.5, 8.0)


@_drawer("disconnect")
def _disconnect(p: QPainter, _c: QColor) -> None:
    # то же звено, перечёркнутое
    p.drawArc(QRectF(2.0, 4.5, 7.0, 7.0), 90 * 16, 180 * 16)
    p.drawArc(QRectF(7.0, 4.5, 7.0, 7.0), 270 * 16, 180 * 16)
    _line(p, 4.5, 11.5, 11.5, 4.5)


@_drawer("device_id")
def _device_id(p: QPainter, _c: QColor) -> None:
    # бейдж: карточка с аватаром и строками
    p.drawRoundedRect(QRectF(1.5, 3.5, 13.0, 9.5), 1.5, 1.5)
    p.drawEllipse(QPointF(5.5, 7.0), 1.6, 1.6)
    p.drawArc(QRectF(3.4, 8.6, 4.2, 3.4), 0, 180 * 16)
    _line(p, 9.0, 6.2, 13.0, 6.2)
    _line(p, 9.0, 9.0, 13.0, 9.0)


@_drawer("diagnostics")
def _diagnostics(p: QPainter, _c: QColor) -> None:
    # пульс-линия
    _poly(p, (1.5, 9.0), (4.5, 9.0), (6.5, 4.0), (9.5, 12.0), (11.5, 9.0), (14.5, 9.0))


@_drawer("scanner")
def _scanner(p: QPainter, c: QColor) -> None:
    # радар: окружность, луч и отметка цели
    p.drawEllipse(QPointF(8.0, 8.0), 5.8, 5.8)
    _line(p, 8.0, 8.0, 12.8, 3.2)
    _dot(p, c, 11.0, 6.5, 1.0)
    _dot(p, c, 8.0, 8.0, 0.9)


@_drawer("graph")
def _graph(p: QPainter, _c: QColor) -> None:
    # линия графика
    _poly(p, (2.0, 12.5), (6.0, 7.5), (9.5, 10.5), (14.0, 3.5))


@_drawer("log")
def _log(p: QPainter, _c: QColor) -> None:
    # лист с текстом
    _poly(p, (4.0, 14.0), (4.0, 2.0), (12.0, 2.0), (12.0, 14.0), close=True)
    _line(p, 6.2, 5.5, 9.8, 5.5)
    _line(p, 6.2, 8.0, 9.8, 8.0)
    _line(p, 6.2, 10.5, 8.8, 10.5)


@_drawer("add")
def _add(p: QPainter, _c: QColor) -> None:
    _line(p, 8.0, 3.0, 8.0, 13.0)
    _line(p, 3.0, 8.0, 13.0, 8.0)


@_drawer("remove")
def _remove(p: QPainter, _c: QColor) -> None:
    _line(p, 4.0, 4.0, 12.0, 12.0)
    _line(p, 12.0, 4.0, 4.0, 12.0)


@_drawer("read")
def _read(p: QPainter, _c: QColor) -> None:
    # стрелка вниз в лоток (загрузка с устройства)
    _line(p, 8.0, 2.5, 8.0, 10.5)
    _poly(p, (5.0, 7.5), (8.0, 10.5), (11.0, 7.5))
    _line(p, 2.5, 13.5, 13.5, 13.5)


@_drawer("read_all")
def _read_all(p: QPainter, _c: QColor) -> None:
    # две стрелки вниз
    for x in (5.3, 10.7):
        _line(p, x, 2.5, x, 9.8)
        _poly(p, (x - 2.2, 7.3), (x, 9.8), (x + 2.2, 7.3))
    _line(p, 2.5, 13.5, 13.5, 13.5)


@_drawer("write")
def _write(p: QPainter, _c: QColor) -> None:
    # карандаш по диагонали
    _poly(p, (3.0, 13.0), (3.0, 10.4), (10.4, 3.0), (13.0, 5.6), (5.6, 13.0), close=True)
    _line(p, 8.9, 4.5, 11.5, 7.1)


@_drawer("poll_start")
def _poll_start(p: QPainter, _c: QColor) -> None:
    _poly(p, (5.0, 3.0), (13.0, 8.0), (5.0, 13.0), close=True)


@_drawer("poll_stop")
def _poll_stop(p: QPainter, _c: QColor) -> None:
    p.drawRoundedRect(QRectF(4.0, 4.0, 8.0, 8.0), 1.0, 1.0)


@_drawer("record")
def _record(p: QPainter, c: QColor) -> None:
    # заполненный круг записи
    _dot(p, c, 8.0, 8.0, 4.2)


@_drawer("csv_import")
def _csv_import(p: QPainter, _c: QColor) -> None:
    # стрелка в таблицу
    p.drawRect(QRectF(1.5, 6.0, 13.0, 7.5))
    _line(p, 8.0, 6.0, 8.0, 13.5)
    _line(p, 1.5, 9.75, 14.5, 9.75)
    _line(p, 8.0, 1.5, 8.0, 4.8)
    _poly(p, (6.0, 3.0), (8.0, 5.0), (10.0, 3.0))


@_drawer("csv_export")
def _csv_export(p: QPainter, _c: QColor) -> None:
    # стрелка из таблицы
    p.drawRect(QRectF(1.5, 6.0, 13.0, 7.5))
    _line(p, 8.0, 6.0, 8.0, 13.5)
    _line(p, 1.5, 9.75, 14.5, 9.75)
    _line(p, 8.0, 5.2, 8.0, 1.5)
    _poly(p, (6.0, 3.5), (8.0, 1.5), (10.0, 3.5))


@_drawer("settings")
def _settings(p: QPainter, _c: QColor) -> None:
    # шестерёнка: ступица и 8 спиц
    p.drawEllipse(QPointF(8.0, 8.0), 2.4, 2.4)
    for i in range(8):
        a = math.radians(i * 45.0)
        dx, dy = math.cos(a), math.sin(a)
        _line(p, 8.0 + dx * 3.9, 8.0 + dy * 3.9, 8.0 + dx * 6.2, 8.0 + dy * 6.2)


@_drawer("help")
def _help(p: QPainter, c: QColor) -> None:
    # вопросительный знак: дуга, хвост, точка
    p.drawArc(QRectF(4.8, 2.3, 6.4, 5.4), 200 * 16, -290 * 16)
    _line(p, 8.0, 7.6, 8.0, 10.2)
    _dot(p, c, 8.0, 12.9, 0.9)


@_drawer("save")
def _save(p: QPainter, _c: QColor) -> None:
    # дискета
    _poly(
        p, (2.5, 13.5), (2.5, 2.5), (10.5, 2.5), (13.5, 5.5), (13.5, 13.5), close=True
    )
    p.drawRect(QRectF(5.0, 2.5, 5.5, 3.8))
    _poly(p, (5.0, 13.5), (5.0, 9.0), (11.0, 9.0), (11.0, 13.5))


@_drawer("filter")
def _filter(p: QPainter, _c: QColor) -> None:
    # воронка
    _poly(p, (2.5, 3.0), (13.5, 3.0), (9.5, 8.2), (9.5, 13.0), (6.5, 11.0), (6.5, 8.2),
          close=True)


@_drawer("sort")
def _sort(p: QPainter, _c: QColor) -> None:
    # две встречные стрелки (сортировка)
    _line(p, 5.0, 3.0, 5.0, 13.0)
    _poly(p, (2.8, 10.5), (5.0, 13.0), (7.2, 10.5))
    _line(p, 11.0, 3.0, 11.0, 13.0)
    _poly(p, (8.8, 5.5), (11.0, 3.0), (13.2, 5.5))


@_drawer("mask_write")
def _mask_write(p: QPainter, c: QColor) -> None:
    # битовая маска: сетка битов, один установлен
    p.drawRect(QRectF(2.5, 2.5, 4.6, 4.6))
    p.drawRect(QRectF(8.9, 2.5, 4.6, 4.6))
    p.drawRect(QRectF(8.9, 8.9, 4.6, 4.6))
    p.save()
    p.setBrush(c)
    p.drawRect(QRectF(2.5, 8.9, 4.6, 4.6))
    p.restore()


@_drawer("readwrite")
def _readwrite(p: QPainter, _c: QColor) -> None:
    # двусторонний обмен: встречные горизонтальные стрелки
    _line(p, 3.0, 5.5, 13.0, 5.5)
    _poly(p, (10.5, 3.5), (13.0, 5.5), (10.5, 7.5))
    _line(p, 13.0, 10.5, 3.0, 10.5)
    _poly(p, (5.5, 8.5), (3.0, 10.5), (5.5, 12.5))


@_drawer("display")
def _display(p: QPainter, _c: QColor) -> None:
    # глаз
    eye = QPainterPath(QPointF(1.5, 8.0))
    eye.cubicTo(4.0, 4.2, 12.0, 4.2, 14.5, 8.0)
    eye.cubicTo(12.0, 11.8, 4.0, 11.8, 1.5, 8.0)
    p.drawPath(eye)
    p.drawEllipse(QPointF(8.0, 8.0), 1.8, 1.8)


@_drawer("clear")
def _clear(p: QPainter, _c: QColor) -> None:
    # корзина
    _line(p, 3.0, 4.5, 13.0, 4.5)
    _poly(p, (6.5, 4.5), (6.5, 2.8), (9.5, 2.8), (9.5, 4.5))
    _poly(p, (4.5, 4.5), (5.2, 13.5), (10.8, 13.5), (11.5, 4.5))
    _line(p, 7.0, 7.0, 7.3, 11.0)
    _line(p, 9.0, 7.0, 8.7, 11.0)


@_drawer("follow")
def _follow(p: QPainter, _c: QColor) -> None:
    # якорь
    p.drawEllipse(QPointF(8.0, 4.0), 1.7, 1.7)
    _line(p, 8.0, 5.7, 8.0, 13.5)
    _line(p, 4.5, 8.5, 11.5, 8.5)
    p.drawArc(QRectF(3.5, 6.5, 9.0, 7.0), 180 * 16, 180 * 16)


@_drawer("markers")
def _markers(p: QPainter, _c: QColor) -> None:
    # флажки A/B на базовой линии
    _line(p, 2.0, 13.5, 14.0, 13.5)
    _line(p, 5.0, 13.5, 5.0, 3.0)
    _poly(p, (5.0, 3.0), (8.8, 4.4), (5.0, 5.8))
    _line(p, 11.0, 13.5, 11.0, 6.0)
    _poly(p, (11.0, 6.0), (14.0, 7.2), (11.0, 8.4))


@_drawer("move_up")
def _move_up(p: QPainter, _c: QColor) -> None:
    _line(p, 8.0, 13.0, 8.0, 3.0)
    _poly(p, (4.5, 6.5), (8.0, 3.0), (11.5, 6.5))


@_drawer("move_down")
def _move_down(p: QPainter, _c: QColor) -> None:
    _line(p, 8.0, 3.0, 8.0, 13.0)
    _poly(p, (4.5, 9.5), (8.0, 13.0), (11.5, 9.5))


@_drawer("close_tab")
def _close_tab(p: QPainter, _c: QColor) -> None:
    _line(p, 4.5, 4.5, 11.5, 11.5)
    _line(p, 11.5, 4.5, 4.5, 11.5)


@_drawer("raw")
def _raw(p: QPainter, _c: QColor) -> None:
    # бинарный код: "0" и "1"
    p.drawRoundedRect(QRectF(2.2, 4.0, 4.4, 8.0), 2.0, 2.0)
    _poly(p, (9.0, 6.0), (11.5, 4.0), (11.5, 12.0))
    _line(p, 9.0, 12.0, 14.0, 12.0)


@_drawer("alarm")
def _alarm(p: QPainter, _c: QColor) -> None:
    # колокольчик: купол, раструб и язычок
    _line(p, 8.0, 1.8, 8.0, 2.8)
    p.drawArc(QRectF(4.0, 2.8, 8.0, 8.0), 0, 180 * 16)
    _poly(p, (4.0, 6.8), (4.0, 10.5), (2.6, 12.5))
    _poly(p, (12.0, 6.8), (12.0, 10.5), (13.4, 12.5))
    _line(p, 2.6, 12.5, 13.4, 12.5)
    p.drawArc(QRectF(6.9, 12.5, 2.2, 2.0), 180 * 16, 180 * 16)


ICON_NAMES: tuple[str, ...] = tuple(_DRAWERS)


def _icon_color() -> QColor:
    """Цвет контура иконки.

    pyqtdarktheme красит виджеты stylesheet'ом и НЕ трогает палитру (ButtonText
    остаётся чёрным на обеих темах), поэтому на тёмной теме берём светлый
    контур по theme.is_dark(), на светлой — ButtonText палитры."""
    if theme.is_dark():
        return QColor(0xE0, 0xE0, 0xE0)
    app = QApplication.instance()
    palette = app.palette() if app is not None else QPalette()
    return palette.color(QPalette.ColorRole.ButtonText)


def _render(name: str, size: int) -> QPixmap:
    """Отрисовать иконку в QPixmap (size*2 px, devicePixelRatio=2)."""
    pixmap = QPixmap(size * _RENDER_SCALE, size * _RENDER_SCALE)
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(_RENDER_SCALE)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = _icon_color()
    pen = QPen(color, _PEN_WIDTH)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if size != ICON_SIZE:
        factor = size / ICON_SIZE
        painter.scale(factor, factor)
    _DRAWERS[name](painter, color)
    painter.end()
    return pixmap


def icon(name: str, size: int = ICON_SIZE) -> QIcon:
    """Отрисовать иконку по имени в цветах текущей темы.

    Рендер на прозрачном QPixmap размером size*2 с devicePixelRatio=2.
    Неизвестное имя — KeyError со списком доступных."""
    if name not in _DRAWERS:
        raise KeyError(f"unknown icon {name!r}; available: {', '.join(sorted(_DRAWERS))}")
    return QIcon(_render(name, size))


def register(button: QToolButton, icon_name: str) -> None:
    """Зарегистрировать кнопку в слабом реестре для refresh_icons()."""
    _REGISTRY[button] = icon_name


def make_button(text: str, icon_name: str, *, checkable: bool = False) -> QToolButton:
    """Компактная QToolButton с иконкой: текст не виден (ToolButtonIconOnly),
    но text()/toolTip()/accessibleName() его возвращают."""
    button = QToolButton()
    button.setIcon(icon(icon_name))
    button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
    button.setText(text)
    button.setToolTip(text)
    button.setAccessibleName(text)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    button.setAutoRaise(False)
    button.setCheckable(checkable)
    button.setStyleSheet("QToolButton { padding: 3px; }")
    register(button, icon_name)
    return button


def refresh_icons() -> None:
    """Пересоздать иконки всех живых зарегистрированных кнопок.

    Вызывать после смены темы (theme.apply_theme): цвет контура берётся
    из палитры в момент рендера, поэтому иконки сами не перекрашиваются."""
    for button, name in list(_REGISTRY.items()):
        try:
            size = button.iconSize().width() or ICON_SIZE
            button.setIcon(icon(name, size))
        except RuntimeError:
            # Python-обёртка ещё жива, но C++-объект уже удалён (слабый реестр
            # этого не отслеживает) — такую кнопку просто пропускаем.
            continue
