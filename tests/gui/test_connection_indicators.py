import pytest
from PySide6.QtWidgets import QApplication
from bytt.gui.connection_indicators import ConnectionIndicatorBar

@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


def test_starts_unknown():
    bar = ConnectionIndicatorBar()
    assert bar.data._state == "unknown"
    assert bar.command._state == "unknown"
    assert bar.sonar._state == "unknown"


def test_set_states_independently():
    bar = ConnectionIndicatorBar()
    bar.set_data_state("warning")
    bar.set_command_state("ok")
    bar.set_sonar_state("error")
    assert bar.data._state == "warning"
    assert bar.command._state == "ok"
    assert bar.sonar._state == "error"


def test_reset_returns_all_to_unknown():
    bar = ConnectionIndicatorBar()
    bar.set_data_state("ok")
    bar.reset()
    assert bar.data._state == "unknown"
