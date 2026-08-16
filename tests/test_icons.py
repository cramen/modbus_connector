import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Qt needs system libraries (libEGL etc.) that headless Linux CI runners lack;
# skip the whole module there — it still runs on macOS/Windows CI and dev machines.
try:
    from PySide6.QtCore import Qt  # noqa: E402
    from PySide6.QtGui import QColor, QIcon, QPixmap  # noqa: E402
    from PySide6.QtWidgets import QApplication, QToolButton  # noqa: E402
except ImportError as exc:
    pytest.skip(f"Qt system libraries not available: {exc}", allow_module_level=True)

from modbus_connector import icons, theme  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _restore_theme() -> None:
    yield
    theme.apply_theme("system")  # the theme is app-global: leave no trace


def _opaque_colors(ic: QIcon) -> set[str]:
    """Цвета (rgba-hex) всех непрозрачных пикселей иконки."""
    image = ic.pixmap(32, 32).toImage()
    return {
        c.name(QColor.NameFormat.HexArgb)
        for x in range(image.width())
        for y in range(image.height())
        if (c := image.pixelColor(x, y)).alpha() > 0
    }


def test_icon_renders_for_every_name(qapp: QApplication) -> None:
    assert len(icons.ICON_NAMES) >= 30
    for name in icons.ICON_NAMES:
        ic = icons.icon(name)
        assert not ic.isNull(), name
        pixmap = ic.pixmap(icons.ICON_SIZE, icons.ICON_SIZE)
        assert not pixmap.isNull(), name
        assert _opaque_colors(ic), f"{name}: no painted pixels"


def test_icon_is_hidpi(qapp: QApplication) -> None:
    pixmap = icons._render("graph", icons.ICON_SIZE)
    assert pixmap.devicePixelRatio() == 2.0
    assert pixmap.width() == icons.ICON_SIZE * 2


def test_icon_size_scales(qapp: QApplication) -> None:
    pixmap = icons._render("add", 24)
    assert pixmap.width() == 48  # 24 * devicePixelRatio(2)


def test_unknown_icon_name_raises(qapp: QApplication) -> None:
    with pytest.raises(KeyError, match="nope"):
        icons.icon("nope")


def test_make_button(qapp: QApplication) -> None:
    button = icons.make_button("Connect", "connect")
    assert isinstance(button, QToolButton)
    assert button.toolTip() == "Connect"
    assert button.text() == "Connect"  # tests rely on text() despite IconOnly
    assert button.accessibleName() == "Connect"
    assert button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
    assert not button.autoRaise()
    assert not button.isCheckable()
    assert not button.icon().isNull()
    assert button.iconSize().width() == icons.ICON_SIZE


def test_make_button_checkable(qapp: QApplication) -> None:
    button = icons.make_button("Log to file", "log", checkable=True)
    assert button.isCheckable()


def test_register_custom_button_and_refresh(qapp: QApplication) -> None:
    button = QToolButton()
    icons.register(button, "graph")
    assert button.icon().isNull()
    icons.refresh_icons()
    assert not button.icon().isNull()


def test_refresh_recreates_icons(qapp: QApplication) -> None:
    button = icons.make_button("Settings", "settings")
    before: QPixmap = button.icon().pixmap(icons.ICON_SIZE, icons.ICON_SIZE)
    icons.refresh_icons()
    after = button.icon().pixmap(icons.ICON_SIZE, icons.ICON_SIZE)
    assert before.cacheKey() != after.cacheKey()  # re-rendered, not cached


def test_refresh_recolors_after_theme_change(qapp: QApplication) -> None:
    button = icons.make_button("Write", "write")
    theme.apply_theme("light")
    icons.refresh_icons()
    light_colors = _opaque_colors(button.icon())

    theme.apply_theme("dark")
    icons.refresh_icons()
    dark_colors = _opaque_colors(button.icon())

    assert light_colors != dark_colors  # ButtonText differs between themes


def test_dead_buttons_do_not_break_refresh(qapp: QApplication) -> None:
    import gc

    icons.make_button("Temp", "add")  # dropped right away: no strong refs
    gc.collect()
    icons.refresh_icons()  # must not crash on the pruned weak entry
