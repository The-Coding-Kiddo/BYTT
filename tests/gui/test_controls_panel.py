import time
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication
from bytt.gui.controls_panel import ControlsPanel
from bytt.processing.enhancement import DEFAULT_ENHANCE_PARAMS

@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app

def _wait_for(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline and not predicate():
        QCoreApplication.processEvents()
        time.sleep(0.01)
    return predicate()

def test_current_params_matches_defaults_on_construction():
    panel = ControlsPanel()
    p = panel.current_params()
    assert p.noise_idx == DEFAULT_ENHANCE_PARAMS.noise_idx
    assert p.contrast_idx == DEFAULT_ENHANCE_PARAMS.contrast_idx
    assert p.agc == DEFAULT_ENHANCE_PARAMS.agc
    assert p.target_idx == DEFAULT_ENHANCE_PARAMS.target_idx
    assert p.target_ksize == DEFAULT_ENHANCE_PARAMS.target_ksize
    assert p.shadow_enh == DEFAULT_ENHANCE_PARAMS.shadow_enh
    assert p.overlay == DEFAULT_ENHANCE_PARAMS.overlay
    assert p.sharpen == DEFAULT_ENHANCE_PARAMS.sharpen
    assert p.gain == DEFAULT_ENHANCE_PARAMS.gain
    assert p.gamma == DEFAULT_ENHANCE_PARAMS.gamma
    assert p.lut_idx == DEFAULT_ENHANCE_PARAMS.lut_idx
    assert p.hdr == DEFAULT_ENHANCE_PARAMS.hdr

def test_changing_gain_emits_params_changed_after_debounce():
    panel = ControlsPanel()
    received = []
    panel.params_changed.connect(lambda p: received.append(p))
    panel.gain_spin.setValue(3.5)
    assert _wait_for(lambda: len(received) > 0, timeout=1.0)
    assert received[-1].gain == 3.5

def test_rapid_successive_changes_only_fire_once_after_settling():
    panel = ControlsPanel()
    received = []
    panel.params_changed.connect(lambda p: received.append(p))
    for v in (1.5, 2.0, 2.5, 3.0):
        panel.gain_spin.setValue(v)
        QCoreApplication.processEvents()
    assert _wait_for(lambda: len(received) > 0, timeout=1.0)
    time.sleep(0.3)
    QCoreApplication.processEvents()
    assert len(received) == 1
    assert received[0].gain == 3.0

def test_reset_to_defaults_resets_every_field_and_emits():
    panel = ControlsPanel()
    panel.gain_spin.setValue(4.0)
    panel.contrast_combo.setCurrentIndex(0)
    panel.agc_check.setChecked(True)
    received = []
    panel.params_changed.connect(lambda p: received.append(p))
    panel.reset_to_defaults()
    assert received
    p = received[-1]
    assert p.gain == DEFAULT_ENHANCE_PARAMS.gain
    assert p.contrast_idx == DEFAULT_ENHANCE_PARAMS.contrast_idx
    assert p.agc == DEFAULT_ENHANCE_PARAMS.agc

def test_channel_toggles_emit_channels_changed():
    panel = ControlsPanel()
    received = []
    panel.channels_changed.connect(lambda port_on, stbd_on: received.append((port_on, stbd_on)))
    panel.port_check.setChecked(False)
    assert received[-1] == (False, True)
    panel.stbd_check.setChecked(False)
    assert received[-1] == (False, False)
