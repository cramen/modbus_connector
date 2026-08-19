"""Панель slave-режима: параметры симулятора (Modbus slave-сервер) и карта значений."""

from typing import Any, get_args

from PySide6.QtCore import QSize, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from serial.tools import list_ports

from modbus_connector import icons, theme
from modbus_connector.connection_panel import BAUDRATES
from modbus_connector.i18n import tr
from modbus_connector.models import (
    DisplayFormat,
    RegisterKind,
    RtuParams,
    format_register_values,
    format_values,
    parse_values,
)
from modbus_connector.sim_backend import BLOCK_SIZE, SimTcpParams, describe_sim
from modbus_connector.templates import TemplateInfo, list_templates, load_template

KINDS = list(get_args(RegisterKind))
FORMATS = list(get_args(DisplayFormat))
REGISTER_KINDS = ("holding_registers", "input_registers")
SERVER_TYPES = ("TCP", "RTU")  # never translated

(
    COL_NAME,
    COL_TYPE,
    COL_ADDRESS,
    COL_COUNT,
    COL_FORMAT,
    COL_VALUE,
    COL_ACTIONS,
) = range(7)
HEADER_LABELS = ("Name", "Type", "Address", "Count", "Format", "Value", "")


class SimPanel(QWidget):
    """Slave-режим сессии: серверные параметры + редактируемая карта значений.

    Значения строк (list[int|bool]) хранятся в UserRole ячейки Name; ячейка
    Value показывает их через format_register_values (регистры) / format_values
    (битовые области). Запись в backend — сигналом setValuesRequested: backend
    хранит блоки и до старта сервера, поэтому правки применяются всегда.
    """

    startRequested = Signal(object, object)  # params, unit (int | None)
    # dataclass-параметры не маршаллятся через Q_ARG — только сигналом
    stopRequested = Signal()
    setValuesRequested = Signal(str, int, list)  # kind, address, values
    logLine = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._running = False
        self._clients = 0
        self._status_message = "Stopped"
        self._status_is_error = False
        self._last_params: SimTcpParams | RtuParams | None = None
        self._translatable: list[tuple[QWidget, str]] = []  # (widget, English key)
        self._translatable_tips: list[tuple[QWidget, str]] = []

        self._type_combo = theme.FitComboBox()
        for type_name in SERVER_TYPES:
            self._type_combo.addItem(type_name, type_name)  # TCP/RTU не переводятся

        self._tcp_host = QLineEdit("127.0.0.1")
        self._tcp_host.setMaximumWidth(140)
        self._tcp_port = QSpinBox(minimum=1, maximum=65535, value=1502)
        network_page = QWidget()
        network_layout = QHBoxLayout(network_page)
        network_layout.setContentsMargins(0, 0, 0, 0)
        network_layout.addWidget(self._label("Host:"))
        network_layout.addWidget(self._tcp_host)
        network_layout.addWidget(self._label("Port:"))
        network_layout.addWidget(self._tcp_port)
        network_layout.addStretch(1)

        self._rtu_port = theme.FitComboBox()
        self._rtu_port.setMinimumWidth(140)
        self._rtu_port.setMaximumWidth(220)  # длинные пути не должны растягивать окно
        self._rtu_refresh = icons.make_button(tr("Refresh"), "readwrite")
        self._track(self._rtu_refresh, "Refresh")
        self._rtu_baud = theme.FitComboBox(editable=True)
        self._rtu_baud.addItems(BAUDRATES)
        self._rtu_bytesize = theme.FitComboBox()
        self._rtu_bytesize.addItems(["8", "7"])
        self._rtu_parity = theme.FitComboBox()
        self._rtu_parity.addItems(["N", "E", "O"])
        self._rtu_stopbits = theme.FitComboBox()
        self._rtu_stopbits.addItems(["1", "2"])
        rtu_page = QWidget()
        rtu_layout = QHBoxLayout(rtu_page)
        rtu_layout.setContentsMargins(0, 0, 0, 0)
        for label, widget in (
            ("Port:", self._rtu_port),
            ("", self._rtu_refresh),
            ("Baud:", self._rtu_baud),
            ("Bits:", self._rtu_bytesize),
            ("Parity:", self._rtu_parity),
            ("Stop:", self._rtu_stopbits),
        ):
            if label:
                rtu_layout.addWidget(self._label(label))
            rtu_layout.addWidget(widget)
        rtu_layout.addStretch(1)

        self._stack = QStackedWidget()
        self._stack.addWidget(network_page)
        self._stack.addWidget(rtu_page)
        self._type_combo.currentIndexChanged.connect(self._stack.setCurrentIndex)

        self._unit_combo = theme.FitComboBox()
        self._unit_combo.addItem(tr("any"), None)  # None = отвечать на любой unit
        for unit in range(1, 248):
            self._unit_combo.addItem(str(unit), unit)

        self._button = icons.make_button(tr("Start server"), "connect")
        self._button.clicked.connect(self._on_button_clicked)
        self._add_button = icons.make_button(tr("Add row"), "add")
        self._track(self._add_button, "Add row", "Add a row to the register map")
        self._add_button.clicked.connect(lambda: self._add_row())
        self._template_button = icons.make_button(tr("Template…"), "csv_import")
        self._track(self._template_button, "Template…", "Add rows from a device template")
        self._template_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._template_button.setMenu(self._build_templates_menu())
        self._status = QLabel()
        # длинный статус не должен расширять окно (как в ConnectionPanel)
        self._status.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self._rtu_refresh.clicked.connect(self._refresh_ports)
        self._refresh_ports()

        settings_row = QHBoxLayout()
        settings_row.addWidget(self._type_combo)
        settings_row.addWidget(self._stack, 1)
        settings_row.addWidget(self._label("Unit:"))
        settings_row.addWidget(self._unit_combo)
        settings_row.addStretch(1)

        controls_row = QHBoxLayout()
        controls_row.addWidget(self._button)
        controls_row.addWidget(self._add_button)
        controls_row.addWidget(self._template_button)
        controls_row.addWidget(self._status, 1)

        self._table = QTableWidget(0, len(HEADER_LABELS))
        self._sync_header()
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for col, width in (
            (COL_NAME, 160),
            (COL_TYPE, 140),
            (COL_FORMAT, 90),
            (COL_VALUE, 140),
        ):
            self._table.setColumnWidth(col, width)
        self._table.verticalHeader().setVisible(False)
        self._table.itemChanged.connect(self._on_item_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(settings_row)
        layout.addLayout(controls_row)
        layout.addWidget(self._table, 1)
        self._sync_button()
        self._render_status()

    # --- построение UI ---

    def _label(self, text: str) -> QLabel:
        label = QLabel(tr(text))
        self._translatable.append((label, text))
        return label

    def _track(self, widget: QWidget, text: str, tip: str | None = None) -> None:
        widget.setText(tr(text))
        self._translatable.append((widget, text))
        # у иконочных кнопок подпись живёт в тултипе
        widget.setToolTip(tr(tip if tip is not None else text))
        self._translatable_tips.append((widget, tip if tip is not None else text))

    def _sync_header(self) -> None:
        self._table.setHorizontalHeaderLabels([tr(text) for text in HEADER_LABELS])

    def _sync_button(self) -> None:
        text = tr("Stop server") if self._running else tr("Start server")
        icon_name = "disconnect" if self._running else "connect"
        self._button.setText(text)
        self._button.setToolTip(text)
        self._button.setAccessibleName(text)
        self._button.setIcon(icons.icon(icon_name))
        icons.register(self._button, icon_name)

    def _build_templates_menu(self) -> QMenu:
        # каталог статичен (package data), меню строится один раз
        menu = QMenu(self._template_button)
        infos = list_templates()
        if not infos:
            action = menu.addAction(tr("(empty)"))
            action.setEnabled(False)
            return menu
        submenus: dict[str, QMenu] = {}
        for info in infos:
            submenu = submenus.get(info.manufacturer)
            if submenu is None:
                # explicit C++ parent (см. main_window._populate_templates_menu):
                # wrapper addMenu(str) удалит меню при сборке мусора
                submenu = QMenu(info.manufacturer, menu)
                menu.addMenu(submenu)
                submenus[info.manufacturer] = submenu
            action = submenu.addAction(info.name)
            if info.description:
                action.setToolTip(info.description)
            action.triggered.connect(
                lambda checked=False, i=info: self._apply_template(i)
            )
        return menu

    # --- строки карты ---

    @staticmethod
    def _coerce_values(kind: str, raw: object, count: int) -> list[int | bool]:
        default: int | bool = 0 if kind in REGISTER_KINDS else False
        values: list[int | bool] = []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, bool):
                    values.append(int(item) if kind in REGISTER_KINDS else item)
                elif isinstance(item, int) and 0 <= item <= 0xFFFF:
                    values.append(item if kind in REGISTER_KINDS else bool(item))
                # мусор пропускается
        while len(values) < count:
            values.append(default)
        return values

    def _add_row(self, entry: dict | None = None) -> None:
        entry = entry if isinstance(entry, dict) else {}
        kind = entry.get("kind") if entry.get("kind") in KINDS else "holding_registers"
        try:
            address = int(entry.get("address", 0))
        except (TypeError, ValueError):
            address = 0
        try:
            count = max(1, int(entry.get("count", 1)))
        except (TypeError, ValueError):
            count = 1
        fmt = entry.get("format") if entry.get("format") in FORMATS else "dec"
        values = self._coerce_values(kind, entry.get("values"), count)

        index = self._table.rowCount()
        self._table.blockSignals(True)
        self._table.insertRow(index)
        name_item = QTableWidgetItem(str(entry.get("name", "")))
        name_item.setData(Qt.ItemDataRole.UserRole, values)
        self._table.setItem(index, COL_NAME, name_item)

        type_combo = theme.FitComboBox()
        type_combo.addItems(KINDS)
        type_combo.setCurrentText(kind)
        type_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._table.setCellWidget(index, COL_TYPE, type_combo)

        self._table.setItem(index, COL_ADDRESS, QTableWidgetItem(str(address)))
        self._table.setItem(index, COL_COUNT, QTableWidgetItem(str(count)))

        format_combo = theme.FitComboBox()
        format_combo.addItems(FORMATS)
        format_combo.setCurrentText(fmt)
        format_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        format_combo.setToolTip(
            tr("Display format (registers only; coils/discrete show 0/1)")
        )
        self._table.setCellWidget(index, COL_FORMAT, format_combo)

        self._table.setItem(index, COL_VALUE, QTableWidgetItem(""))

        delete_button = QToolButton()
        delete_button.setIcon(icons.icon("close_tab"))
        delete_button.setIconSize(QSize(icons.ICON_SIZE, icons.ICON_SIZE))
        icons.register(delete_button, "close_tab")
        delete_button.setFixedSize(26, 26)
        delete_button.setToolTip(tr("Delete row"))
        delete_button.clicked.connect(self._on_delete_clicked)
        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(2, 2, 2, 2)
        actions_layout.addWidget(delete_button)
        self._table.setCellWidget(index, COL_ACTIONS, actions)
        self._table.blockSignals(False)
        self._render_value(index)

        type_combo.currentTextChanged.connect(
            lambda _text, combo=type_combo: self._on_kind_changed(combo)
        )
        format_combo.currentTextChanged.connect(
            lambda _text, combo=format_combo: self._on_format_changed(combo)
        )

    def _row_of(self, widget: QWidget, col: int) -> int | None:
        for index in range(self._table.rowCount()):
            if self._table.cellWidget(index, col) is widget:
                return index
        return None

    def _text_at(self, index: int, col: int) -> str:
        item = self._table.item(index, col)
        return item.text().strip() if item is not None else ""

    def _kind_at(self, index: int) -> str:
        combo = self._table.cellWidget(index, COL_TYPE)
        return combo.currentText() if combo is not None else "holding_registers"

    def _values_at(self, index: int) -> list[int | bool]:
        item = self._table.item(index, COL_NAME)
        values = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return values if isinstance(values, list) else []

    def _address_at(self, index: int) -> int | None:
        try:
            address = int(self._text_at(index, COL_ADDRESS), 0)
        except ValueError:
            return None
        return address if 0 <= address < BLOCK_SIZE else None

    def _render_value(self, index: int) -> None:
        kind = self._kind_at(index)
        values = self._values_at(index)
        if kind in REGISTER_KINDS:
            fmt = self._table.cellWidget(index, COL_FORMAT).currentText()
            text = format_register_values([int(v) for v in values], fmt)
        else:
            text = format_values(values)
        item = self._table.item(index, COL_VALUE)
        if item is None:
            return
        self._table.blockSignals(True)
        item.setText(text)
        self._table.blockSignals(False)

    def _push_row(self, index: int) -> None:
        """Отправить значения строки в backend (блоки хранятся и до старта)."""
        address = self._address_at(index)
        values = self._values_at(index)
        if address is None or not values:
            return
        self.setValuesRequested.emit(self._kind_at(index), address, list(values))

    # --- слоты таблицы ---

    @Slot(QTableWidgetItem)
    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        index, col = item.row(), item.column()
        if col == COL_VALUE:
            self._commit_value(index)
        elif col == COL_COUNT:
            self._commit_count(index)
        elif col == COL_ADDRESS:
            self._push_row(index)  # валидный адрес — значения уезжают на новое место

    def _commit_value(self, index: int) -> None:
        kind = self._kind_at(index)
        item = self._table.item(index, COL_VALUE)
        try:
            values = parse_values(kind, item.text())
        except ValueError as exc:
            self.logLine.emit(tr("✗ parse error: {exc}", exc=exc))
            self._render_value(index)  # откат к сохранённым значениям
            return
        self._table.item(index, COL_NAME).setData(Qt.ItemDataRole.UserRole, values)
        self._render_value(index)
        self._push_row(index)

    def _commit_count(self, index: int) -> None:
        values = self._values_at(index)
        try:
            count = int(self._text_at(index, COL_COUNT), 0)
        except ValueError:
            count = 0
        if not 1 <= count <= BLOCK_SIZE:
            self._table.blockSignals(True)
            self._table.item(index, COL_COUNT).setText(str(len(values)))
            self._table.blockSignals(False)
            return
        default: int | bool = 0 if self._kind_at(index) in REGISTER_KINDS else False
        values = (values + [default] * count)[:count]
        self._table.item(index, COL_NAME).setData(Qt.ItemDataRole.UserRole, values)
        self._render_value(index)
        self._push_row(index)

    @Slot(object)
    def _on_kind_changed(self, combo: QComboBox) -> None:
        index = self._row_of(combo, COL_TYPE)
        if index is None:
            return
        count = len(self._values_at(index)) or 1
        default: int | bool = 0 if combo.currentText() in REGISTER_KINDS else False
        self._table.item(index, COL_NAME).setData(
            Qt.ItemDataRole.UserRole, [default] * count
        )
        self._render_value(index)
        self._push_row(index)

    @Slot(object)
    def _on_format_changed(self, combo: QComboBox) -> None:
        index = self._row_of(combo, COL_FORMAT)
        if index is not None:
            self._render_value(index)

    @Slot()
    def _on_delete_clicked(self) -> None:
        button = self.sender()
        for index in range(self._table.rowCount()):
            if self._table.cellWidget(index, COL_ACTIONS) is button.parent():
                self._table.removeRow(index)
                return

    # --- шаблоны ---

    def _apply_template(self, info: TemplateInfo) -> None:
        """Добавить регистры шаблона в карту (дубли kind+address пропускаются)."""
        try:
            data = load_template(info)
        except ValueError as exc:
            self.logLine.emit(
                tr("✗ failed to load template {name}: {exc}", name=info.resource, exc=exc)
            )
            return
        existing = set()
        for index in range(self._table.rowCount()):
            address = self._address_at(index)
            if address is not None:
                existing.add((self._kind_at(index), address))
        added = skipped = 0
        for entry in data.get("registers", []):
            if not isinstance(entry, dict):
                continue
            kind = entry.get("kind") if entry.get("kind") in KINDS else "holding_registers"
            try:
                address = int(entry["address"])
            except (KeyError, TypeError, ValueError):
                continue
            if (kind, address) in existing:
                skipped += 1
                continue
            existing.add((kind, address))
            self._add_row(entry)
            added += 1
        if added or skipped:
            self.logLine.emit(
                tr("← template: added {added} rows to the map", added=added)
                + (
                    tr(", skipped {skipped} duplicates", skipped=skipped)
                    if skipped
                    else ""
                )
            )

    # --- сервер ---

    def _build_params(self) -> SimTcpParams | RtuParams:
        if self._type_combo.currentIndex() == 1:
            return RtuParams(
                port=self._rtu_port.currentText(),
                baudrate=int(self._rtu_baud.currentText()),
                bytesize=int(self._rtu_bytesize.currentText()),
                parity=self._rtu_parity.currentText(),
                stopbits=int(self._rtu_stopbits.currentText()),
            )
        return SimTcpParams(
            host=self._tcp_host.text().strip(), port=self._tcp_port.value()
        )

    @Slot()
    def _on_button_clicked(self) -> None:
        if self._running:
            self.stopRequested.emit()
            return
        try:
            params = self._build_params()
        except ValueError:
            self._status_message = "Invalid settings"
            self._status_is_error = True
            self._render_status()
            return
        self._last_params = params
        # queued-сигналы: set_values доберутся до backend раньше start_server
        for index in range(self._table.rowCount()):
            self._push_row(index)
        self.startRequested.emit(params, self._unit_combo.currentData())

    @Slot(bool, str)
    def set_running(self, ok: bool, message: str) -> None:
        """Слот на SimWorker.serverChanged: кнопка, статус, гейтинг параметров."""
        self._running = ok
        if not ok:
            self._clients = 0
        self._status_message = message
        self._status_is_error = not ok and message != "Stopped"
        self._sync_button()
        self._render_status()
        for widget in (
            self._type_combo,
            self._tcp_host,
            self._tcp_port,
            self._rtu_port,
            self._rtu_refresh,
            self._rtu_baud,
            self._rtu_bytesize,
            self._rtu_parity,
            self._rtu_stopbits,
            self._unit_combo,
        ):
            widget.setEnabled(not ok)

    @Slot(bool)
    def handle_client_changed(self, connected: bool) -> None:
        self._clients = max(0, self._clients + (1 if connected else -1))
        if self._running:
            self._render_status()

    @Slot(str, int, list)
    def handle_master_write(self, kind: str, address: int, values: list) -> None:
        """Запись мастера: обновить Value строк, покрывающих адреса записи."""
        registers = kind in REGISTER_KINDS
        for index in range(self._table.rowCount()):
            if self._kind_at(index) != kind:
                continue
            row_address = self._address_at(index)
            if row_address is None:
                continue
            stored = self._values_at(index)
            start = max(address, row_address)
            end = min(address + len(values), row_address + len(stored))
            if start >= end:
                continue
            # data(UserRole) возвращает копию — собираем новый список и пишем назад
            updated = list(stored)
            for pos in range(start, end):
                raw = values[pos - address]
                updated[pos - row_address] = int(raw) if registers else bool(raw)
            self._table.item(index, COL_NAME).setData(Qt.ItemDataRole.UserRole, updated)
            self._render_value(index)

    def running_description(self) -> str | None:
        """«sim tcp host:port»/«sim rtu port» для заголовка вкладки, если запущен."""
        if self._running and self._last_params is not None:
            return describe_sim(self._last_params)
        return None

    def _render_status(self) -> None:
        if self._status_is_error:
            self._status.setText(self._status_message)
            self._status.setStyleSheet("color: red")
            return
        colors = theme.status_colors()
        if self._running:
            text = (
                f"{tr(self._status_message)} — "
                f"{tr('clients: {count}', count=self._clients)}"
            )
            color = colors["ok"]
        else:
            text, color = tr(self._status_message), colors["off"]
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {color}")

    @Slot()
    def _refresh_ports(self) -> None:
        current = self._rtu_port.currentText()
        previous = {self._rtu_port.itemText(i) for i in range(self._rtu_port.count())}
        ports = [p.device for p in list_ports.comports()]
        self._rtu_port.clear()
        self._rtu_port.addItems(ports)  # попап сам подгонит ширину
        new_ports = [p for p in ports if p not in previous]
        if new_ports:
            self._rtu_port.setCurrentText(new_ports[0])
            return
        index = self._rtu_port.findText(current)
        if index >= 0:
            self._rtu_port.setCurrentIndex(index)

    # --- состояние и перевод ---

    def state(self) -> dict[str, Any]:
        rows = []
        for index in range(self._table.rowCount()):
            address = self._address_at(index)
            if address is None:
                continue
            name_item = self._table.item(index, COL_NAME)
            rows.append(
                {
                    "name": name_item.text() if name_item is not None else "",
                    "kind": self._kind_at(index),
                    "address": address,
                    "count": len(self._values_at(index)),
                    "format": self._table.cellWidget(index, COL_FORMAT).currentText(),
                    "values": list(self._values_at(index)),
                }
            )
        unit = self._unit_combo.currentData()
        return {
            "server": {
                "type": self._type_combo.currentData(),
                "host": self._tcp_host.text(),
                "port": self._tcp_port.value(),
                "rtu_port": self._rtu_port.currentText(),
                "rtu_baud": self._rtu_baud.currentText(),
                "rtu_bytesize": self._rtu_bytesize.currentText(),
                "rtu_parity": self._rtu_parity.currentText(),
                "rtu_stopbits": self._rtu_stopbits.currentText(),
                "unit": "any" if unit is None else unit,
            },
            "rows": rows,
        }

    def set_state(self, state: dict) -> None:
        if not isinstance(state, dict):
            return
        server = state.get("server")
        if isinstance(server, dict):
            type_index = self._type_combo.findData(str(server.get("type", "")))
            if type_index >= 0:
                self._type_combo.setCurrentIndex(type_index)
            self._tcp_host.setText(str(server.get("host", self._tcp_host.text())))
            try:
                port = int(server.get("port", self._tcp_port.value()))
            except (TypeError, ValueError):
                port = self._tcp_port.value()
            self._tcp_port.setValue(min(65535, max(1, port)))
            rtu_port = str(server.get("rtu_port", ""))
            if rtu_port:
                if self._rtu_port.findText(rtu_port) < 0:
                    self._rtu_port.addItem(rtu_port)
                self._rtu_port.setCurrentText(rtu_port)
            self._rtu_baud.setCurrentText(
                str(server.get("rtu_baud", self._rtu_baud.currentText()))
            )
            for combo, key in (
                (self._rtu_bytesize, "rtu_bytesize"),
                (self._rtu_parity, "rtu_parity"),
                (self._rtu_stopbits, "rtu_stopbits"),
            ):
                text = str(server.get(key, ""))
                if text and combo.findText(text) >= 0:
                    combo.setCurrentText(text)
            unit = server.get("unit", "any")
            unit_index = (
                self._unit_combo.findData(unit)
                if isinstance(unit, int) and not isinstance(unit, bool)
                else 0
            )
            self._unit_combo.setCurrentIndex(unit_index if unit_index >= 0 else 0)
        rows = state.get("rows")
        if isinstance(rows, list):
            while self._table.rowCount():
                self._table.removeRow(0)
            for entry in rows:
                if isinstance(entry, dict):
                    self._add_row(entry)

    def retranslate(self) -> None:
        """Переприменить tr() ко всем строкам панели (по смене языка)."""
        for widget, text in self._translatable:
            widget.setText(tr(text))
            if isinstance(widget, QToolButton):
                widget.setToolTip(tr(text))
                widget.setAccessibleName(tr(text))
        for widget, tip in self._translatable_tips:
            widget.setToolTip(tr(tip))
        self._sync_header()
        self._unit_combo.setItemText(0, tr("any"))
        self._sync_button()
        self._render_status()
