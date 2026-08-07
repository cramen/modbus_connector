import sys

from PySide6.QtWidgets import QApplication

from modbus_connector.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("modbus-connector")
    window = MainWindow()
    window.show()
    return app.exec()
