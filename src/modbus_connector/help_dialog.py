"""Немодальная помощь по окнам: иконочная кнопка показывает HTML-справку."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from modbus_connector import icons
from modbus_connector.i18n import current_language, tr

REGISTERS_HELP = """
<h3>Registers table</h3>
<p>Each row is a register range: <b>Name</b>, <b>Type</b> (coils, discrete
inputs, holding or input registers), <b>Address</b> (dec or 0x-hex),
<b>Count</b>, optional per-row <b>Unit ID</b> and <b>Poll, ms</b> interval,
display <b>Format</b>, the last read <b>Value</b> with a trend sparkline,
and <b>New value</b> for writes (raw values, no scale/offset).</p>
<ul>
<li>The <b>checkbox in the first column</b> includes the row in polling and
<b>Read all</b>; unchecked rows are skipped (Ctrl+R, quick actions and
writes still work on them).</li>
<li><b>Read all</b> reads every checked row once; the split button starts polling
with or without history recording (the dropdown chooses; the choice flips
recording mid-poll without restarting timers).</li>
<li><b>Log to file</b> records read values to CSV or JSON Lines; <b>⚙</b>
opens file/format/field/row settings.</li>
<li><b>Display…</b> — per-row Scale/Offset/Unit and byte order;
<b>Filter…</b> hides non-matching rows; <b>Sort by address</b> reorders;
<b>CSV</b> imports/exports the table; <b>Mask write (0x16)…</b> and
<b>Read/Write (0x17)…</b> run the advanced functions.</li>
<li><b>Alarms…</b> — per-row alarm rules over the scaled value (hex/ascii
rows are skipped). The first matching rule wins and paints the Value cell
red/yellow, outranking the change flash; activation and clearing are logged
once per edge (if Log is set), with a beep on activation if Sound is set.</li>
<li><b>Snapshot</b> remembers the current raw values of all rows;
<b>Diff…</b> (enabled after a snapshot) opens a window comparing them with
the latest reads — changed rows are highlighted, "(removed)" marks rows
deleted after the snapshot, "Take new snapshot" accepts the current values
as the new baseline.</li>
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
join checked; "Refresh rows" rebuilds the list). Only rows enabled for polling
(the leftmost checkbox in the table) are listed — unchecking a row there hides
its series here, and the checklist state survives the hide/show cycle.</li>
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
    """Маленькая квадратная иконочная кнопка, открывающая справку по окну."""
    button = icons.make_button(tr("Help"), "help")
    button.setParent(parent)
    button.setFixedSize(26, 26)
    button.clicked.connect(lambda: show_help(parent, title, html))
    return button


def show_help(parent: QWidget, title: str, html: str) -> QDialog:
    """Показать немодальный диалог помощи; удаляется при закрытии."""
    dialog = QDialog(parent)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dialog.setWindowTitle(tr(title))
    dialog.resize(520, 480)
    browser = QTextBrowser()
    browser.setHtml(_sheet(html))  # resolved at click time, never cached
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.reject)
    layout = QVBoxLayout(dialog)
    layout.addWidget(browser)
    layout.addWidget(buttons)
    dialog.show()
    return dialog


def _sheet(english_html: str) -> str:
    """Русская версия листа при текущем русском языке, иначе английская."""
    if current_language() != "ru":
        return english_html
    return HELP_RU.get(english_html, english_html)


REGISTERS_HELP_RU = """
<h3>Таблица регистров</h3>
<p>Каждая строка — диапазон регистров: <b>имя</b>, <b>тип</b> (coils, discrete
inputs, holding или input registers), <b>адрес</b> (dec или 0x-hex),
<b>кол-во</b>, необязательные <b>Unit ID</b> и интервал <b>Poll, мс</b> на
строку, <b>формат</b> отображения, последнее прочитанное <b>значение</b> со
спарклайном и поле <b>Новое значение</b> для записи (сырые значения,
без scale/offset).</p>
<ul>
<li><b>Галочка в первой колонке</b> включает строку в опрос и
<b>Прочитать все</b>; снятые строки пропускаются (Ctrl+R, быстрые действия
и запись на них по-прежнему работают).</li>
<li><b>Прочитать все</b> читает каждую отмеченную строку один раз; split-кнопка
начинает опрос с записью истории или без (выбор в выпадающем меню; на ходу
переключает запись без перезапуска таймеров).</li>
<li><b>Запись в файл</b> пишет значения в CSV или JSON Lines; <b>⚙</b> открывает
настройки файла, формата, полей и строк.</li>
<li><b>Отображение…</b> — Scale/Offset/Unit и порядок байт на строку;
<b>Фильтр…</b> скрывает лишние строки; <b>Сортировать по адресу</b>
упорядочивает таблицу; <b>CSV</b> — импорт/экспорт; <b>Mask write (0x16)…</b> и
<b>Read/Write (0x17)…</b> — расширенные функции.</li>
<li><b>Алармы…</b> — правила алармов на строку по масштабированному значению
(строки hex/ascii пропускаются). Срабатывает первое совпавшее правило и
красит ячейку значения в красный/жёлтый (приоритет над зелёной вспышкой
изменения); активация и снятие пишутся в лог один раз на фронт (если
включён «Лог»), при активации — звук, если включён «Звук».</li>
<li><b>Снапшот</b> запоминает текущие raw-значения всех строк;
<b>Сравнение…</b> (активно после снапшота) открывает окно сравнения с
последними чтениями — изменённые строки подсвечены, «(удалена)» помечает
строки, удалённые после снапшота, «Снять новый снапшот» принимает текущие
значения как новую базу.</li>
<li>Правый клик по строке — быстрые действия (копия, запись 0/1, шаг,
переключение, сдвиг); действуют на выбранные строки.</li>
</ul>
<table border="1" cellspacing="0" cellpadding="3">
<tr><th>Клавиши</th><th>Действие</th></tr>
<tr><td>Enter</td><td>записать «Новое значение» строки</td></tr>
<tr><td>Ctrl+R</td><td>прочитать текущую строку</td></tr>
<tr><td>Ctrl+Shift+R</td><td>прочитать все строки</td></tr>
<tr><td>Ctrl+C</td><td>копировать значение</td></tr>
<tr><td>Ctrl+0 / Ctrl+1</td><td>записать 0 / 1</td></tr>
<tr><td>Ctrl+= / Ctrl+-</td><td>увеличить / уменьшить последнее прочитанное
(работает и Ctrl++ на нампаде)</td></tr>
<tr><td>Ctrl+T</td><td>переключить (coil инвертируется; регистр 0↔1)</td></tr>
<tr><td>Ctrl+Up / Ctrl+Down</td><td>сдвинуть выбранные строки</td></tr>
</table>
<p>На macOS вместо Ctrl используется ⌘.</p>
"""

GRAPH_HELP_RU = """
<h3>Окно графика</h3>
<ul>
<li>Чек-лист <b>Series</b> выбирает строки таблицы для построения (новые
строки добавляются отмеченными; «Refresh rows» перечитывает список). В список
попадают только строки, включённые в поллинг (крайняя левая галочка в таблице)
— снятие галочки скрывает ряд отсюда, состояние чек-листа при этом
сохраняется.</li>
<li><b>X scale</b>: <b>Follow</b> ведёт скользящее окно за последними данными,
<b>Full</b> вписывает всё, <b>Manual</b> замораживает вид. Колесо — зум у
курсора, драг — панорама, <b>Zoom rect</b> — зум рамкой; любой из них
переключает режим в Manual; <b>Reset view</b> возвращает Follow.</li>
<li><b>Markers</b> показывает две перетаскиваемые линии (зелёную A, красную B)
с min/max/avg каждого ряда между ними и Δt.</li>
<li>При наведении курсора — кроссхеар: пунктирная вертикаль и значения всех
рядов в этот момент в правом верхнем углу (ближайший отсчёт).</li>
<li><b>Clear</b> очищает записанную историю и перезапускает ось времени.</li>
<li><b>Начать опрос с записью</b> управляет опросом таблицы прямо из окна;
при активной записи кнопка останавливает его.</li>
</ul>
"""

SCANNER_HELP_RU = """
<h3>Сканер</h3>
<ul>
<li><b>Скан unit-адресов</b>: обход диапазона с списком проб (тип, адрес,
кол-во) и список устройств, ответивших хотя бы на одну пробу. Пробы
добавляются и удаляются выше; <b>Stop</b> прерывает обход.</li>
<li><b>Двойной клик</b> по найденному unit подставляет его в панель
подключения; <b>Device ID…</b> читает идентификацию выбранного unit
(0x2B/0x0E).</li>
<li><b>Скан регистров</b>: проверяет диапазон адресов одного unit и типа.
Найденные адреса отмечены; лишние можно снять (Space переключает, All/None —
для длинных списков), а <b>Add selected to table</b> создаёт строки таблицы
по отмеченным — дубли (тот же тип и адрес) пропускаются.</li>
</ul>
"""

HELP_RU = {
    REGISTERS_HELP: REGISTERS_HELP_RU,
    GRAPH_HELP: GRAPH_HELP_RU,
    SCANNER_HELP: SCANNER_HELP_RU,
}
