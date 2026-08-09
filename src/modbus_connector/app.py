import sys

from PySide6.QtWidgets import QApplication

from modbus_connector.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("modbus-connector")
    window = MainWindow()
    window.showMaximized()  # resize() in MainWindow stays as the restored geometry
    return app.exec()
