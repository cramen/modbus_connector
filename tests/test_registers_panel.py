import itertools
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from modbus_connector.registers_panel import COL_NEW_VALUE, RegistersPanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_same_value_written_twice(qapp: QApplication) -> None:
    panel = RegistersPanel(itertools.count(1).__next__)
    writes: list[tuple] = []
    panel.writeRequested.connect(lambda *args: writes.append(args))

    item = panel._table.item(0, COL_NEW_VALUE)
    assert item is not None

    item.setText("5")
    assert len(writes) == 1
    assert item.text() == ""  # cleared after the write is issued

    item.setText("5")
    assert len(writes) == 2
    assert writes[0][1:] == writes[1][1:]  # same unit/row/values, only request id differs

    item.setText("")  # empty text never triggers a write
    assert len(writes) == 2
