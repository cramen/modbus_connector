"""Немодальная помощь по окнам: кнопка "?" показывает HTML-справку."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

REGISTERS_HELP = """
<h3>Registers table</h3>
<p>Each row is a register range: <b>Name</b>, <b>Type</b> (coils, discrete
inputs, holding or input registers), <b>Address</b> (dec or 0x-hex),
<b>Count</b>, optional per-row <b>Unit ID</b> and <b>Poll, ms</b> interval,
display <b>Format</b>, the last read <b>Value</b> with a trend sparkline,
and <b>New value</b> for writes (raw values, no scale/offset).</p>
<ul>
<li><b>Read all</b> reads every row once; the split button starts polling
with or without history recording (the dropdown chooses; the choice flips
recording mid-poll without restarting timers).</li>
<li><b>Log to file</b> records read values to CSV or JSON Lines; <b>⚙</b>
opens file/format/field/row settings.</li>
<li><b>Display…</b> — per-row Scale/Offset/Unit and byte order;
<b>Filter…</b> hides non-matching rows; <b>Sort by address</b> reorders;
<b>CSV</b> imports/exports the table; <b>Mask write (0x16)…</b> and
<b>Read/Write (0x17)…</b> run the advanced functions.</li>
<li>Right-click a row for quick actions (copy, write 0/1, increment,
decrement, toggle, move) — they act on the selected rows.</li>
</ul>
<table border="1" cellspacing="0" cellpadding="3">
<tr><th>Hotkey</th><th>Action</th></tr>
<tr><td>Enter</td><td>write the row's New value</td></tr>
<tr><td>Ctrl+R</td><td>read the current row</td></tr>
<tr><td>Ctrl+Shift+R</td><td>read all rows</td></tr>
<tr><td>Ctrl+C</td><td>copy the value</td></tr>
<tr><td>Ctrl+0 / Ctrl+1</td><td>write 0 / 1</td></tr>
<tr><td>Ctrl+= / Ctrl+-</td><td>increment / decrement the last read value
(numpad Ctrl++ works too)</td></tr>
<tr><td>Ctrl+T</td><td>toggle (coil flips; register 0↔1)</td></tr>
<tr><td>Ctrl+Up / Ctrl+Down</td><td>move the selected rows</td></tr>
</table>
<p>⌘ replaces Ctrl on macOS.</p>
"""

GRAPH_HELP = """
<h3>Graph window</h3>
<ul>
<li>The <b>Series</b> checklist picks which table rows are plotted (new rows
join checked; "Refresh rows" rebuilds the list).</li>
<li><b>X scale</b>: <b>Follow</b> slides a window of the given width behind
the latest data, <b>Full</b> fits everything, <b>Manual</b> freezes the
view. Wheel zooms at the cursor, left-drag pans, <b>Zoom rect</b> zooms to a
rectangle — any of these switches the mode to Manual;
<b>Reset view</b> returns to Follow.</li>
<li><b>Markers</b> shows two draggable lines (green A, red B) with per-series
min/max/avg between them and Δt.</li>
<li>Hovering the plot shows a crosshair: a dashed vertical and a top-right
readout with every series' value at that time (nearest sample).</li>
<li><b>Clear</b> wipes the recorded history and restarts the time axis.</li>
<li><b>Start polling and record</b> drives the register table's polling from
here; while it records, the button stops it.</li>
</ul>
"""

SCANNER_HELP = """
<h3>Scanner</h3>
<ul>
<li><b>Unit scan</b>: sweeps the unit range with the probe list (type,
address, count per probe) and lists units that answered at least one probe.
Add/remove probes above; <b>Stop</b> aborts the sweep.</li>
<li><b>Double-click</b> a found unit to select it in the connection panel;
<b>Device ID…</b> reads the selected unit's identification (0x2B/0x0E).</li>
<li><b>Registers scan</b>: probes the address range of one unit and kind.
Found addresses arrive checked; uncheck rows you don't need (Space toggles,
All/None for long lists) and <b>Add selected to table</b> creates register
rows for them — duplicates (same type and address) are skipped.</li>
</ul>
"""


def make_help_button(parent: QWidget, title: str, html: str) -> QToolButton:
    """Маленькая квадратная кнопка "?", открывающая справку по окну."""
    button = QToolButton(parent)
    button.setText("?")
    button.setFixedSize(26, 26)
    button.setToolTip("Help")
    button.clicked.connect(lambda: show_help(parent, title, html))
    return button


def show_help(parent: QWidget, title: str, html: str) -> QDialog:
    """Показать немодальный диалог помощи; удаляется при закрытии."""
    dialog = QDialog(parent)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dialog.setWindowTitle(title)
    dialog.resize(520, 480)
    browser = QTextBrowser()
    browser.setHtml(html)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.reject)
    layout = QVBoxLayout(dialog)
    layout.addWidget(browser)
    layout.addWidget(buttons)
    dialog.show()
    return dialog
