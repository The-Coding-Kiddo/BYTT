import pytest
from PySide6.QtWidgets import QApplication
from bytt.gui.sonar_control_panel import SonarControlPanel

@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


def test_current_command_reflects_widget_state():
    panel = SonarControlPanel()
    panel.hf_range.setValue(75)
    panel.hf_gain.setValue(20)
    panel.lf_enable.setChecked(False)
    cmd = panel.current_command()
    assert cmd.sonar_range_hf == 75
    assert cmd.gain_hf == 20
    assert cmd.ch_en_lf == 0
    assert cmd.sonar_run == 0


def test_start_stop_toggles_run_state_and_emits():
    panel = SonarControlPanel()
    received = []
    panel.apply_requested.connect(received.append)

    panel.run_button.click()
    assert panel.run_button.text() == "Stop"
    assert received[-1].sonar_run == 1

    panel.run_button.click()
    assert panel.run_button.text() == "Start"
    assert received[-1].sonar_run == 0


def test_apply_button_emits_current_command():
    panel = SonarControlPanel()
    received = []
    panel.apply_requested.connect(received.append)
    panel.hf_range.setValue(30)
    panel.apply_button.click()
    assert received[-1].sonar_range_hf == 30
