import sys

from PySide6.QtWidgets import QApplication

from modbus_connector.main_window import MainWindow
from modbus_connector.settings_store import load_settings
from modbus_connector.theme import apply_theme


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("modbus-connector")
    # qdarktheme needs a QApplication; apply before the window is shown
    theme = load_settings().get("theme", "system")
    apply_theme(theme if isinstance(theme, str) else "system")
    window = MainWindow()
    window.showMaximized()  # resize() in MainWindow stays as the restored geometry
    return app.exec()
