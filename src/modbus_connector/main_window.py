import json
from pathlib import Path

from PySide6.QtCore import Slot
from PySide6.QtGui import QCloseEvent, QKeySequence
from PySide6.QtWidgets import QFileDialog, QLabel, QMainWindow, QWidget

from modbus_connector.models import StatsSnapshot
from modbus_connector.session_widget import SessionWidget
from modbus_connector.settings_store import load_settings, save_settings


class MainWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modbus Connector")
        self.resize(1100, 750)

        self._session = SessionWidget()
        self._session.set_state(load_settings())
        self.setCentralWidget(self._session)

        self._stats_label = QLabel()
        self._update_stats(StatsSnapshot())
        self.statusBar().addPermanentWidget(self._stats_label)
        self._session.statsUpdated.connect(self._update_stats)

        file_menu = self.menuBar().addMenu("File")
        save_action = file_menu.addAction("Save Settings to File…", self._save_to_file)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        load_action = file_menu.addAction("Load Settings from File…", self._load_from_file)
        load_action.setShortcut(QKeySequence.StandardKey.Open)

    @Slot(object)
    def _update_stats(self, snapshot: StatsSnapshot) -> None:
        text = (
            f"Tx: {snapshot.total}  Err: {snapshot.errors} "
            f"({snapshot.error_percent:.1f}%)  Avg: {snapshot.avg_ms:.0f} ms"
        )
        top = snapshot.top_error_kind
        if top is not None:
            text += f"  top: {top.removeprefix('exception:')}"
        self._stats_label.setText(text)
        tooltip = "\n".join(
            f"{kind}: {count}"
            for kind, count in sorted(
                snapshot.error_kinds.items(), key=lambda item: -item[1]
            )
        )
        self._stats_label.setToolTip(tooltip or "no errors yet")

    def _save_to_file(self) -> None:
        path_str, _ = QFileDialog.getSaveFileName(
            self, "Save Settings", str(Path.home() / "settings.json"), "JSON (*.json)"
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            path.write_text(json.dumps(self._session.state(), indent=2), encoding="utf-8")
        except OSError as exc:
            self._session.log_panel.append(f"✗ failed to save settings to {path}: {exc}")
            return
        self._session.log_panel.append(f"→ settings saved to {path}")

    def _load_from_file(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Load Settings", str(Path.home()), "JSON (*.json)"
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self._session.log_panel.append(f"✗ failed to load settings from {path}: {exc}")
            return
        if not isinstance(state, dict):
            self._session.log_panel.append(
                f"✗ failed to load settings from {path}: not an object"
            )
            return
        self._session.set_state(state)
        self._session.log_panel.append(f"← settings loaded from {path}")

    def closeEvent(self, event: QCloseEvent) -> None:
        save_settings(self._session.state())
        self._session.shutdown()
        super().closeEvent(event)
