"""Панель режима «rtu sniffer»: serial-параметры и вкладки по unit-адресам.

Пассивный режим: значения приходят только со шины (SnifferWorker.valuesChanged),
записи нет. Вкладка «unit N» создаётся при первом кадре/значении для N;
строки (kind, address) добавляются автоматически и держатся отсортированными
по адресу. История для спарклайнов пишется при каждом обновлении значения —
у сниффера нет record-режима, он «пишет» всегда.
"""

import time
from typing import Any, get_args

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
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
    decode_register_values,
    format_register_values,
    format_values,
)
from modbus_connector.registers_panel import SparklineWidget
from modbus_connector.sniffer_backend import describe_sniffer
from modbus_connector.timeseries import TimeSeries

KINDS = list(get_args(RegisterKind))
FORMATS = list(get_args(DisplayFormat))
REGISTER_KINDS = ("holding_registers", "input_registers")
NON_NUMERIC_FORMATS = ("hex", "ascii", "ascii1")  # в тренд не пишутся

COL_ADDRESS, COL_NAME, COL_TYPE, COL_FORMAT, COL_VALUE, COL_TREND = range(6)
HEADER_LABELS = ("Address", "Name", "Type", "Format", "Value", "Trend")

# роль данных ячейки Name: значения строки (list[int|bool]), как в SimPanel
_VALUES_ROLE = Qt.ItemDataRole.UserRole

LOG_MAX_BLOCKS = 1000


class UnitTab(QWidget):
    """Вкладка одного unit: таблица значений + лог кадров только этого unit."""

    def __init__(self, unit: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.unit = unit
        # ключ строки — (kind, address); значения — в UserRole ячейки Name
        self._rows: dict[tuple[str, int], QTableWidgetItem] = {}  # → ячейка Name
        self._series: dict[tuple[str, int], TimeSeries] = {}
        self._sparklines: dict[tuple[str, int], SparklineWidget] = {}
        self._flash_generations: dict[tuple[str, int], int] = {}

        self._table = QTableWidget(0, len(HEADER_LABELS))
        self._sync_header()
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for col, width in ((COL_ADDRESS, 70), (COL_NAME, 160), (COL_TYPE, 140),
                           (COL_FORMAT, 90), (COL_VALUE, 140)):
            self._table.setColumnWidth(col, width)
        self._table.verticalHeader().setVisible(False)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(LOG_MAX_BLOCKS)
        self._log.setMaximumHeight(140)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(self._table, 1)
        layout.addWidget(self._log)

    # --- строки таблицы ---

    def _sync_header(self) -> None:
        self._table.setHorizontalHeaderLabels([tr(text) for text in HEADER_LABELS])

    def _row_index(self, key: tuple[str, int]) -> int | None:
        item = self._rows.get(key)
        return item.row() if item is not None else None

    def _insert_position(self, address: int) -> int:
        """Строки отсортированы по адресу; равные адреса — в порядке появления."""
        position = 0
        for index in range(self._table.rowCount()):
            item = self._table.item(index, COL_ADDRESS)
            if item is not None and int(item.text()) <= address:
                position = index + 1
        return position

    def _add_row(self, kind: str, address: int, name: str, fmt: str,
                 values: list[int | bool]) -> None:
        key = (kind, address)
        index = self._insert_position(address)
        self._table.insertRow(index)

        readonly = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        address_item = QTableWidgetItem(str(address))
        address_item.setFlags(readonly)
        self._table.setItem(index, COL_ADDRESS, address_item)

        name_item = QTableWidgetItem(name)
        name_item.setData(_VALUES_ROLE, list(values))
        self._table.setItem(index, COL_NAME, name_item)

        type_item = QTableWidgetItem(kind)  # RegisterKind не переводится
        type_item.setFlags(readonly)
        self._table.setItem(index, COL_TYPE, type_item)

        format_combo = theme.FitComboBox()
        format_combo.addItems(FORMATS)
        format_combo.setCurrentText(fmt)
        format_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        format_combo.setToolTip(
            tr("Display format (registers only; coils/discrete show 0/1)")
        )
        format_combo.currentTextChanged.connect(
            lambda _text, k=key: self._render_value(k)
        )
        self._table.setCellWidget(index, COL_FORMAT, format_combo)

        value_item = QTableWidgetItem("")
        value_item.setFlags(readonly)
        self._table.setItem(index, COL_VALUE, value_item)

        series = TimeSeries()
        sparkline = SparklineWidget(series)
        self._series[key] = series
        self._sparklines[key] = sparkline
        self._table.setCellWidget(index, COL_TREND, sparkline)

        self._rows[key] = name_item
        self._render_value(key)

    def _format_at(self, key: tuple[str, int]) -> str:
        index = self._row_index(key)
        combo = self._table.cellWidget(index, COL_FORMAT) if index is not None else None
        return combo.currentText() if combo is not None else "dec"

    def _render_value(self, key: tuple[str, int]) -> None:
        index = self._row_index(key)
        if index is None:
            return
        kind, _address = key
        item = self._table.item(index, COL_VALUE)
        name_item = self._rows.get(key)
        if item is None or name_item is None:
            return
        values = name_item.data(_VALUES_ROLE) or []
        if kind in REGISTER_KINDS:
            text = format_register_values([int(v) for v in values], self._format_at(key))
        else:
            text = format_values(values)
        item.setText(text)

    def _primary_number(self, key: tuple[str, int]) -> float | None:
        """Число для тренда: биты 1.0/0.0, регистры — decode[0] по формату."""
        kind, _address = key
        name_item = self._rows.get(key)
        values = name_item.data(_VALUES_ROLE) if name_item is not None else None
        if not values:
            return None
        if kind not in REGISTER_KINDS:
            return float(int(bool(values[0])))
        fmt = self._format_at(key)
        if fmt in NON_NUMERIC_FORMATS:
            return None
        decoded = decode_register_values([int(v) for v in values], fmt)
        return float(decoded[0]) if decoded else None

    def handle_values(self, kind: str, address: int, values: list) -> None:
        """Значения со шины: новая строка или обновление + flash + тренд."""
        key = (kind, address)
        coerced = [int(v) if kind in REGISTER_KINDS else bool(v) for v in values]
        if key not in self._rows:
            self._add_row(kind, address, "", "dec", coerced)
            self._flash_value(key)
        else:
            name_item = self._rows[key]
            if name_item.data(_VALUES_ROLE) != coerced:
                name_item.setData(_VALUES_ROLE, coerced)
                self._render_value(key)
                self._flash_value(key)
        primary = self._primary_number(key)
        if primary is not None:
            self._series[key].append(time.monotonic(), primary)
            self._sparklines[key].refresh()

    def _flash_value(self, key: tuple[str, int]) -> None:
        """Подсветить ячейку Value зелёным на ~2 с, как в master/slave панелях."""
        index = self._row_index(key)
        if index is None:
            return
        item = self._table.item(index, COL_VALUE)
        if item is None:
            return
        item.setBackground(theme.flash_color())
        generation = self._flash_generations.get(key, 0) + 1
        self._flash_generations[key] = generation
        # parented QTimer, не QTimer.singleShot(lambda) — см. registers_panel
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._clear_flash(key, generation))
        timer.start(2000)

    def _clear_flash(self, key: tuple[str, int], generation: int) -> None:
        if self._flash_generations.get(key) != generation:
            return  # подавлено более новой вспышкой — её таймер и очистит
        self._flash_generations.pop(key, None)
        index = self._row_index(key)
        if index is None:
            return
        item = self._table.item(index, COL_VALUE)
        if item is not None:
            item.setBackground(QBrush())

    # --- лог кадров unit ---

    def append_log(self, line: str) -> None:
        self._log.appendPlainText(line)

    # --- состояние и перевод ---

    def rows_state(self) -> list[dict[str, Any]]:
        rows = []
        for index in range(self._table.rowCount()):
            address_item = self._table.item(index, COL_ADDRESS)
            name_item = self._table.item(index, COL_NAME)
            type_item = self._table.item(index, COL_TYPE)
            if address_item is None or name_item is None or type_item is None:
                continue
            combo = self._table.cellWidget(index, COL_FORMAT)
            rows.append(
                {
                    "address": int(address_item.text()),
                    "kind": type_item.text(),
                    "name": name_item.text(),
                    "format": combo.currentText() if combo is not None else "dec",
                    "value": list(name_item.data(_VALUES_ROLE) or []),
                }
            )
        return rows

    def set_rows(self, rows: list) -> None:
        self._table.setRowCount(0)
        self._rows.clear()
        self._series.clear()
        self._sparklines.clear()
        self._flash_generations.clear()
        for entry in rows:
            if not isinstance(entry, dict):
                continue
            kind = entry.get("kind") if entry.get("kind") in KINDS else "holding_registers"
            try:
                address = int(str(entry.get("address", 0)), 0)
            except (TypeError, ValueError):
                continue
            if not 0 <= address <= 0xFFFF:
                continue
            fmt = entry.get("format") if entry.get("format") in FORMATS else "dec"
            default: int | bool = 0 if kind in REGISTER_KINDS else False
            values: list[int | bool] = []
            raw = entry.get("value")
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, bool):
                        values.append(int(item) if kind in REGISTER_KINDS else item)
                    elif isinstance(item, int) and 0 <= item <= 0xFFFF:
                        values.append(item if kind in REGISTER_KINDS else bool(item))
            if not values:
                values = [default]
            self._add_row(kind, address, str(entry.get("name", "")), fmt, values)

    def retranslate(self) -> None:
        self._sync_header()


class SnifferPanel(QWidget):
    """Режим «rtu sniffer»: serial-параметры + вкладки unit'ов с картой шины."""

    startRequested = Signal(object)  # RtuParams — только сигналом (Q_ARG не маршаллит)
    stopRequested = Signal()
    logLine = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sniffing = False
        self._status_message = "Stopped"
        self._status_is_error = False
        self._last_params: RtuParams | None = None
        self._unit_tabs: dict[int, UnitTab] = {}
        self._translatable: list[tuple[QWidget, str]] = []  # (widget, English key)
        self._translatable_tips: list[tuple[QWidget, str]] = []

        self._port = theme.FitComboBox()
        self._port.setMinimumWidth(140)
        self._port.setMaximumWidth(220)  # длинные пути не должны растягивать окно
        self._refresh = icons.make_button(tr("Refresh"), "readwrite")
        self._track(self._refresh, "Refresh")
        self._refresh.clicked.connect(self._refresh_ports)
        self._baud = theme.FitComboBox(editable=True)
        self._baud.addItems(BAUDRATES)
        self._bytesize = theme.FitComboBox()
        self._bytesize.addItems(["8", "7"])
        self._parity = theme.FitComboBox()
        self._parity.addItems(["N", "E", "O"])
        self._stopbits = theme.FitComboBox()
        self._stopbits.addItems(["1", "2"])
        self._refresh_ports()

        params_row = QHBoxLayout()
        for label, widget in (
            ("Port:", self._port),
            ("", self._refresh),
            ("Baud:", self._baud),
            ("Bits:", self._bytesize),
            ("Parity:", self._parity),
            ("Stop:", self._stopbits),
        ):
            if label:
                params_row.addWidget(self._label(label))
            params_row.addWidget(widget)
        params_row.addStretch(1)

        self._button = icons.make_button(tr("Start sniffing"), "scanner")
        self._button.clicked.connect(self._on_button_clicked)
        self._status = QLabel()
        # длинный статус не должен расширять окно (как в ConnectionPanel)
        self._status.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        controls_row = QHBoxLayout()
        controls_row.addWidget(self._button)
        controls_row.addWidget(self._status, 1)

        self._tabs = QTabWidget()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(params_row)
        layout.addLayout(controls_row)
        layout.addWidget(self._tabs, 1)
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

    def _sync_button(self) -> None:
        text = tr("Stop sniffing") if self._sniffing else tr("Start sniffing")
        icon_name = "poll_stop" if self._sniffing else "scanner"
        self._button.setText(text)
        self._button.setToolTip(text)
        self._button.setAccessibleName(text)
        self._button.setIcon(icons.icon(icon_name))
        icons.register(self._button, icon_name)

    @Slot()
    def _refresh_ports(self) -> None:
        current = self._port.currentText()
        previous = {self._port.itemText(i) for i in range(self._port.count())}
        ports = [p.device for p in list_ports.comports()]
        self._port.clear()
        self._port.addItems(ports)  # попап сам подгонит ширину
        new_ports = [p for p in ports if p not in previous]
        if new_ports:
            self._port.setCurrentText(new_ports[0])
            return
        index = self._port.findText(current)
        if index >= 0:
            self._port.setCurrentIndex(index)

    # --- вкладки unit'ов ---

    def _unit_tab(self, unit: int) -> UnitTab:
        tab = self._unit_tabs.get(unit)
        if tab is None:
            tab = UnitTab(unit)
            self._unit_tabs[unit] = tab
            self._tabs.addTab(tab, tr("unit {unit}", unit=unit))
        return tab

    @Slot(int, str, int, list)
    def handle_values(self, unit: int, kind: str, address: int, values: list) -> None:
        self._unit_tab(unit).handle_values(kind, address, values)

    @Slot(int, str)
    def handle_frame_for_unit(self, unit: int, line: str) -> None:
        self._unit_tab(unit).append_log(line)

    # --- сниффинг ---

    def _build_params(self) -> RtuParams:
        return RtuParams(
            port=self._port.currentText(),
            baudrate=int(self._baud.currentText()),
            bytesize=int(self._bytesize.currentText()),
            parity=self._parity.currentText(),
            stopbits=int(self._stopbits.currentText()),
        )

    @Slot()
    def _on_button_clicked(self) -> None:
        if self._sniffing:
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
        self.startRequested.emit(params)

    @Slot(bool, str)
    def set_sniffing(self, ok: bool, message: str) -> None:
        """Слот на SnifferWorker.sniffingChanged: кнопка, статус, гейтинг."""
        self._sniffing = ok
        self._status_message = message
        self._status_is_error = not ok and message != "Stopped"
        self._sync_button()
        self._render_status()
        for widget in (
            self._port,
            self._refresh,
            self._baud,
            self._bytesize,
            self._parity,
            self._stopbits,
        ):
            widget.setEnabled(not ok)

    def sniffing_description(self) -> str | None:
        """«sniff rtu port @ baud» для заголовка вкладки, если слушаем."""
        if self._sniffing and self._last_params is not None:
            return describe_sniffer(self._last_params)
        return None

    def _render_status(self) -> None:
        if self._status_is_error:
            self._status.setText(self._status_message)
            self._status.setStyleSheet("color: red")
            return
        colors = theme.status_colors()
        color = colors["ok"] if self._sniffing else colors["off"]
        self._status.setText(tr(self._status_message))
        self._status.setStyleSheet(f"color: {color}")

    # --- состояние и перевод ---

    def state(self) -> dict[str, Any]:
        try:
            baudrate: int | str = int(self._baud.currentText())
        except ValueError:
            baudrate = self._baud.currentText()
        return {
            "params": {
                "port": self._port.currentText(),
                "baudrate": baudrate,
                "bytesize": int(self._bytesize.currentText()),
                "parity": self._parity.currentText(),
                "stopbits": int(self._stopbits.currentText()),
            },
            "units": [
                {"unit": unit, "rows": tab.rows_state()}
                for unit, tab in sorted(self._unit_tabs.items())
            ],
        }

    def set_state(self, state: dict) -> None:
        if not isinstance(state, dict):
            return
        params = state.get("params")
        if isinstance(params, dict):
            port = str(params.get("port", ""))
            if port:
                if self._port.findText(port) < 0:
                    self._port.addItem(port)
                self._port.setCurrentText(port)
            baud = params.get("baudrate", "")
            if str(baud):
                self._baud.setCurrentText(str(baud))
            for combo, key in (
                (self._bytesize, "bytesize"),
                (self._parity, "parity"),
                (self._stopbits, "stopbits"),
            ):
                text = str(params.get(key, ""))
                if text and combo.findText(text) >= 0:
                    combo.setCurrentText(text)
        units = state.get("units")
        if isinstance(units, list):
            for entry in units:
                if not isinstance(entry, dict):
                    continue
                unit = entry.get("unit")
                if not isinstance(unit, int) or isinstance(unit, bool):
                    continue
                if not 0 <= unit <= 247:
                    continue
                rows = entry.get("rows")
                self._unit_tab(unit).set_rows(rows if isinstance(rows, list) else [])

    def retranslate(self) -> None:
        """Переприменить tr() ко всем строкам панели (по смене языка)."""
        for widget, text in self._translatable:
            widget.setText(tr(text))
        for widget, tip in self._translatable_tips:
            widget.setToolTip(tr(tip))
        self._sync_button()
        self._render_status()
        for index in range(self._tabs.count()):
            tab = self._tabs.widget(index)
            if isinstance(tab, UnitTab):
                self._tabs.setTabText(index, tr("unit {unit}", unit=tab.unit))
                tab.retranslate()
