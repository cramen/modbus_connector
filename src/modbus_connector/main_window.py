import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Slot
from PySide6.QtGui import QActionGroup, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QTabWidget,
    QToolButton,
    QWidget,
)

from modbus_connector.i18n import (
    LANGUAGES,
    current_language,
    languageChanged,
    set_language,
    tr,
)
from modbus_connector.models import StatsSnapshot
from modbus_connector.session_widget import SessionWidget
from modbus_connector.settings_store import load_settings, save_settings
from modbus_connector.theme import THEMES, apply_theme, current_theme


class MainWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modbus Connector")
        self.resize(1100, 750)

        self._tabs = QTabWidget()
        self._tabs.setMovable(True)
        add_button = QToolButton()
        add_button.setText("+")
        add_button.setToolTip("New connection tab")
        add_button.clicked.connect(lambda: self._add_session())
        self._tabs.setCornerWidget(add_button)
        self._tabs.tabCloseRequested.connect(self._close_tab)
        self._tabs.currentChanged.connect(self._on_current_changed)
        self.setCentralWidget(self._tabs)

        self._stats_label = QLabel()
        self._update_stats(StatsSnapshot())
        self.statusBar().addPermanentWidget(self._stats_label)

        file_menu = self.menuBar().addMenu(tr("File"))
        self._file_menu = file_menu
        self._save_action = file_menu.addAction(
            tr("Save Settings to File…"), self._save_to_file
        )
        self._save_action.setShortcut(QKeySequence.StandardKey.Save)
        self._load_action = file_menu.addAction(
            tr("Load Settings from File…"), self._load_from_file
        )
        self._load_action.setShortcut(QKeySequence.StandardKey.Open)

        view_menu = self.menuBar().addMenu(tr("View"))
        self._view_menu = view_menu
        self._theme_menu = view_menu.addMenu(tr("Theme"))
        self._theme_actions = {}
        theme_group = QActionGroup(self)
        labels = {"system": "System", "light": "Light", "dark": "Dark"}
        for key in THEMES:
            action = self._theme_menu.addAction(labels[key])
            action.setCheckable(True)
            theme_group.addAction(action)
            action.triggered.connect(lambda checked=False, name=key: self._on_theme_selected(name))
            self._theme_actions[key] = action
        self._sync_theme_menu()

        self._language_menu = view_menu.addMenu(tr("Language"))
        self._language_actions = {}
        language_group = QActionGroup(self)
        for key in LANGUAGES:  # language names stay native on purpose
            action = self._language_menu.addAction(
                "Русский" if key == "ru" else "English"
            )
            action.setCheckable(True)
            language_group.addAction(action)
            action.triggered.connect(
                lambda checked=False, name=key: self._on_language_selected(name)
            )
            self._language_actions[key] = action
        self._sync_language_menu()
        languageChanged.connect(self._on_language_changed)

        self._apply_state(load_settings())

    def _add_session(self, state: dict[str, Any] | None = None) -> SessionWidget:
        session = SessionWidget()
        if state:
            session.set_state(state)
        session.titleChanged.connect(
            lambda title, s=session: self._update_tab_title(s, title)
        )
        session.statsUpdated.connect(self._on_session_stats)
        index = self._tabs.addTab(session, session.title())
        self._tabs.setCurrentIndex(index)
        self._update_close_state()
        return session

    def _close_tab(self, index: int) -> None:
        if self._tabs.count() <= 1:
            return  # the last tab stays open
        session = self._tabs.widget(index)
        self._tabs.removeTab(index)
        session.shutdown()
        session.deleteLater()
        self._update_close_state()

    def _update_close_state(self) -> None:
        self._tabs.setTabsClosable(self._tabs.count() > 1)

    def _update_tab_title(self, session: SessionWidget, title: str) -> None:
        index = self._tabs.indexOf(session)
        if index >= 0:
            self._tabs.setTabText(index, title)

    def _clear_sessions(self) -> None:
        while self._tabs.count():
            session = self._tabs.widget(0)
            self._tabs.removeTab(0)
            session.shutdown()
            session.deleteLater()
        self._update_close_state()

    def _shutdown_sessions(self) -> None:
        for index in range(self._tabs.count()):
            self._tabs.widget(index).shutdown()

    def _on_session_stats(self, snapshot: StatsSnapshot) -> None:
        if self.sender() is self._tabs.currentWidget():
            self._update_stats(snapshot)

    def _on_current_changed(self, index: int) -> None:
        session = self._tabs.widget(index)
        if session is not None:
            self._update_stats(session.last_stats())

    @Slot(object)
    def _update_stats(self, snapshot: StatsSnapshot) -> None:
        text = tr(
            "Tx: {total}  Err: {errors} ({percent:.1f}%)  Avg: {avg:.0f} ms",
            total=snapshot.total,
            errors=snapshot.errors,
            percent=snapshot.error_percent,
            avg=snapshot.avg_ms,
        )
        top = snapshot.top_error_kind
        if top is not None:
            text += tr("  top: {kind}", kind=top.removeprefix("exception:"))
        self._stats_label.setText(text)
        tooltip = "\n".join(
            f"{kind}: {count}"
            for kind, count in sorted(
                snapshot.error_kinds.items(), key=lambda item: -item[1]
            )
        )
        self._stats_label.setToolTip(tooltip or tr("no errors yet"))

    def _collect_state(self) -> dict[str, Any]:
        return {
            "theme": current_theme(),
            "language": current_language(),
            "tabs": [self._tabs.widget(i).state() for i in range(self._tabs.count())],
            "active_tab": self._tabs.currentIndex(),
        }

    def _sync_theme_menu(self) -> None:
        self._theme_actions[current_theme()].setChecked(True)

    def _sync_language_menu(self) -> None:
        self._language_actions[current_language()].setChecked(True)

    def _on_language_selected(self, name: str) -> None:
        set_language(name)  # emits languageChanged → _on_language_changed

    @Slot(str)
    def _on_language_changed(self, _name: str) -> None:
        self._retranslate()
        for index in range(self._tabs.count()):
            self._tabs.widget(index).retranslate()
        self._sync_language_menu()

    def _retranslate(self) -> None:
        self._file_menu.setTitle(tr("File"))
        self._save_action.setText(tr("Save Settings to File…"))
        self._load_action.setText(tr("Load Settings from File…"))
        self._view_menu.setTitle(tr("View"))
        self._theme_menu.setTitle(tr("Theme"))
        self._language_menu.setTitle(tr("Language"))
        for key, label in (("system", "System"), ("light", "Light"), ("dark", "Dark")):
            self._theme_actions[key].setText(tr(label))
        session = self._tabs.currentWidget()
        self._update_stats(session.last_stats() if session else StatsSnapshot())

    def _on_theme_selected(self, name: str) -> None:
        apply_theme(name)
        # re-tint what stylesheets don't reach: status label colors and
        # already-open graph windows (sparklines read the palette at paint time)
        for index in range(self._tabs.count()):
            session = self._tabs.widget(index)
            session.connection_panel.refresh_theme()
            if session._graph_window is not None:
                session._graph_window.update_theme()

    def _apply_state(self, state: dict[str, Any]) -> None:
        theme = state.get("theme") if isinstance(state, dict) else None
        if isinstance(theme, str):
            self._on_theme_selected(theme)  # also re-tints open sessions
        self._sync_theme_menu()
        language = state.get("language") if isinstance(state, dict) else None
        if isinstance(language, str):
            set_language(language)  # before tabs: they build in this language
        self._sync_language_menu()
        tabs = state.get("tabs") if isinstance(state, dict) else None
        if isinstance(tabs, list):
            for entry in tabs:
                if isinstance(entry, dict):
                    self._add_session(entry)
        else:
            # old single-session format: connection/registers/scanner at top level
            # (and the even older flat connection format) become one tab
            self._add_session(state)
        if self._tabs.count() == 0:
            self._add_session()
        active = state.get("active_tab") if isinstance(state, dict) else None
        if isinstance(active, int) and 0 <= active < self._tabs.count():
            self._tabs.setCurrentIndex(active)

    def _save_to_file(self) -> None:
        path_str, _ = QFileDialog.getSaveFileName(
            self, "Save Settings", str(Path.home() / "settings.json"), "JSON (*.json)"
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            path.write_text(json.dumps(self._collect_state(), indent=2), encoding="utf-8")
        except OSError as exc:
            self._log_line(f"✗ failed to save settings to {path}: {exc}")
            return
        self._log_line(f"→ settings saved to {path}")

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
            self._log_line(f"✗ failed to load settings from {path}: {exc}")
            return
        if not isinstance(state, dict):
            self._log_line(f"✗ failed to load settings from {path}: not an object")
            return
        self._clear_sessions()
        self._apply_state(state)
        self._log_line(f"← settings loaded from {path}")

    def _log_line(self, line: str) -> None:
        session = self._tabs.currentWidget()
        if session is not None:
            session.log_panel.append(line)

    def closeEvent(self, event: QCloseEvent) -> None:
        save_settings(self._collect_state())
        self._shutdown_sessions()
        super().closeEvent(event)
