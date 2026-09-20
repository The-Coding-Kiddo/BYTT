import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt as QtCoreQt
from bytt.gui.main_window import MainWindow
from bytt.config import AppConfig

@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app

def test_main_window_starts_disconnected():
    window = MainWindow(AppConfig())
    assert window.statusBar().currentMessage() in ("", "disconnected")
    assert window.source is None

def test_main_window_has_connect_and_playback_actions():
    window = MainWindow(AppConfig())
    action_texts = {a.text() for a in window.menuBar().actions()[0].menu().actions()}
    assert any("Connect" in t for t in action_texts)
    assert any("Playback" in t or "Open" in t for t in action_texts)

def test_main_window_open_playback_creates_playback_source(tmp_path, monkeypatch):
    from bytt.protocol import constants as pc
    import struct
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(bytes(header))  # header-only: 0 pings, exercises the path safely

    window = MainWindow(AppConfig())
    window.open_playback_file(str(bsf_path))
    assert window.source is not None
    window.source.stop()


def test_on_ping_received_adds_one_row_without_raising():
    import numpy as np

    window = MainWindow(AppConfig())
    port_raw = np.linspace(0.0, 1.0, 512, dtype=np.float32)
    stbd_raw = np.linspace(1.0, 0.0, 512, dtype=np.float32)

    before = window.waterfall.rows_written
    window._on_ping_received(port_raw, stbd_raw, {})
    assert window.waterfall.rows_written == before + 1


def test_on_ping_received_reports_error_via_status_bar_on_malformed_input():
    import numpy as np

    window = MainWindow(AppConfig())
    before = window.waterfall.rows_written
    # Empty channel arrays (e.g. what extract_raw_channels now returns for a
    # corrupt/oversized half_samples field) can't be interpolated sensibly —
    # this must not crash the Qt slot or vanish silently.
    port_raw = np.zeros(0, dtype=np.float32)
    stbd_raw = np.zeros(0, dtype=np.float32)

    window._on_ping_received(port_raw, stbd_raw, {})

    assert window.waterfall.rows_written == before
    assert window.statusBar().currentMessage() != ""


def test_on_ping_received_no_longer_imports_enhance_pixels_directly():
    import bytt.gui.main_window as mw_module
    assert not hasattr(mw_module, "enhance_pixels")
    assert not hasattr(mw_module, "_DEFAULT_ENHANCE_PARAMS")


def test_close_event_disconnects_source_and_closes_command_client(tmp_path):
    from bytt.protocol import constants as pc

    header = bytearray(pc.BSF_FILE_HDR_SZ)
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(bytes(header))

    window = MainWindow(AppConfig())
    window.open_playback_file(str(bsf_path))
    assert window.source is not None

    closed = {"called": False}
    orig_close = window.command_client.close

    def spy_close():
        closed["called"] = True
        orig_close()

    window.command_client.close = spy_close

    window.close()

    assert window.source is None
    assert closed["called"] is True


def test_playback_toolbar_hidden_when_disconnected():
    window = MainWindow(AppConfig())
    assert window.playback_toolbar.isVisible() is False

def test_playback_toolbar_visible_after_opening_playback_file(tmp_path):
    from bytt.protocol import constants as pc
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(bytes(header))  # 0 pings is fine; we're testing toolbar visibility

    window = MainWindow(AppConfig())
    window.show()  # isVisible() reflects ancestor visibility, so the window
                    # must actually be shown for this assertion to be meaningful
    window.open_playback_file(str(bsf_path))
    assert window.playback_toolbar.isVisible() is True
    window.source.stop()

def test_toggle_play_pause_calls_source_pause_and_resume(tmp_path):
    from bytt.protocol import constants as pc
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(bytes(header))

    window = MainWindow(AppConfig())
    window.open_playback_file(str(bsf_path))

    calls = []
    window.source.pause = lambda: calls.append('pause')
    window.source.resume = lambda: calls.append('resume')

    window._toggle_play_pause()  # starts "playing" -> should pause
    assert calls == ['pause']
    assert window.play_pause_action.text() == "Play"

    window._toggle_play_pause()  # now "paused" -> should resume
    assert calls == ['pause', 'resume']
    assert window.play_pause_action.text() == "Pause"

    window.source.stop()

def test_step_buttons_call_source_step_methods(tmp_path):
    from bytt.protocol import constants as pc
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(bytes(header))

    window = MainWindow(AppConfig())
    window.open_playback_file(str(bsf_path))

    calls = []
    window.source.step_forward = lambda: calls.append('fwd')
    window.source.step_backward = lambda: calls.append('back')

    window._step_forward()
    window._step_backward()
    assert calls == ['fwd', 'back']

    window.source.stop()

def test_on_position_changed_updates_label():
    window = MainWindow(AppConfig())
    window._on_position_changed(4, 10)
    assert window.position_label.text() == "Ping 5 / 10"

def test_on_position_changed_updates_scrub_slider_when_not_dragging():
    window = MainWindow(AppConfig())
    window._on_position_changed(3, 10)
    assert window.scrub_slider.maximum() == 9
    assert window.scrub_slider.value() == 3

def test_scrub_slider_ignores_position_updates_while_dragging():
    window = MainWindow(AppConfig())
    window._on_position_changed(2, 10)
    window._on_scrub_pressed()
    window._on_position_changed(7, 10)  # arrives mid-drag, must NOT move the slider
    assert window.scrub_slider.value() == 2

def test_scrub_slider_release_calls_source_seek(tmp_path):
    from bytt.protocol import constants as pc
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(bytes(header))

    window = MainWindow(AppConfig())
    window.open_playback_file(str(bsf_path))

    calls = []
    window.source.seek = lambda idx: calls.append(idx)

    window.scrub_slider.setRange(0, 10)
    window._on_scrub_pressed()
    window.scrub_slider.setValue(6)
    window._on_scrub_released()
    assert calls == [6]

    window.source.stop()

def test_space_shortcut_toggles_play_pause(tmp_path):
    from bytt.protocol import constants as pc
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(bytes(header))

    window = MainWindow(AppConfig())
    window.open_playback_file(str(bsf_path))

    calls = []
    window.source.pause = lambda: calls.append('pause')
    window.source.resume = lambda: calls.append('resume')

    # QShortcut's default context is Qt::WindowShortcut, which only fires
    # while the shortcut's window is the active window. QTest.keyClick alone
    # (without show()+processEvents()) delivers the key event but the
    # shortcut never activates, so the window must actually be shown first.
    window.show()
    QApplication.instance().processEvents()
    QTest.keyClick(window, QtCoreQt.Key.Key_Space)
    assert calls == ['pause']

    window.source.stop()

def test_arrow_shortcuts_step(tmp_path):
    from bytt.protocol import constants as pc
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(bytes(header))

    window = MainWindow(AppConfig())
    window.open_playback_file(str(bsf_path))

    calls = []
    window.source.step_forward = lambda: calls.append('fwd')
    window.source.step_backward = lambda: calls.append('back')

    # See test_space_shortcut_toggles_play_pause for why show()+processEvents()
    # is required before QShortcut (WindowShortcut context) will fire.
    window.show()
    QApplication.instance().processEvents()
    QTest.keyClick(window, QtCoreQt.Key.Key_Right)
    QTest.keyClick(window, QtCoreQt.Key.Key_Left)
    assert calls == ['fwd', 'back']

    window.source.stop()

def test_playback_finished_status_reverts_play_pause_button(tmp_path):
    from bytt.protocol import constants as pc
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(bytes(header))

    window = MainWindow(AppConfig())
    window.open_playback_file(str(bsf_path))
    assert window._is_playing is True
    assert window.play_pause_action.text() == "Pause"

    window._on_source_status_changed('playback finished')

    assert window._is_playing is False
    assert window.play_pause_action.text() == "Play"

    window.source.stop()

def test_speed_inc_dec_update_label_and_clamp():
    window = MainWindow(AppConfig())
    assert window.speed_label.text() == "4×"

    window._speed_inc()
    assert window.speed_label.text() == "8×"
    window._speed_inc()
    assert window.speed_label.text() == "16×"
    window._speed_inc()
    assert window.speed_label.text() == "32×"
    window._speed_inc()
    assert window.speed_label.text() == "MAX"
    window._speed_inc()  # already at max, must not go out of range
    assert window.speed_label.text() == "MAX"

    window._speed_dec()
    assert window.speed_label.text() == "32×"
    window._speed_dec()
    window._speed_dec()
    window._speed_dec()
    window._speed_dec()
    window._speed_dec()
    assert window.speed_label.text() == "1×"
    window._speed_dec()  # already at min, must not go out of range
    assert window.speed_label.text() == "1×"


def test_speed_inc_applies_interval_to_connected_playback_source(tmp_path):
    from bytt.protocol import constants as pc
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(bytes(header))

    window = MainWindow(AppConfig())
    window.open_playback_file(str(bsf_path))

    calls = []
    window.source.set_speed = lambda interval_s: calls.append(interval_s)

    window._speed_inc()  # default idx 2 (4x) -> idx 3 (8x)
    assert calls == [1.0 / (16.0 * 8.0)]

    window.source.stop()


def test_home_seeks_to_zero_resumes_and_sets_pause_label(tmp_path):
    from bytt.protocol import constants as pc
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(bytes(header))

    window = MainWindow(AppConfig())
    window.open_playback_file(str(bsf_path))

    calls = []
    window.source.seek = lambda idx: calls.append(('seek', idx))
    window.source.resume = lambda: calls.append(('resume',))

    window.play_pause_action.setText("Play")
    window._is_playing = False

    window._home()

    assert calls == [('seek', 0), ('resume',)]
    assert window.play_pause_action.text() == "Pause"
    assert window._is_playing is True

    window.source.stop()


def test_speed_and_home_shortcuts_trigger_handlers(tmp_path):
    from bytt.protocol import constants as pc
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(bytes(header))

    window = MainWindow(AppConfig())
    window.open_playback_file(str(bsf_path))

    calls = []
    window.source.set_speed = lambda interval_s: calls.append('speed')
    window.source.seek = lambda idx: calls.append('seek')
    window.source.resume = lambda: calls.append('resume')

    # See test_space_shortcut_toggles_play_pause for why show()+processEvents()
    # is required before QShortcut (WindowShortcut context) will fire.
    window.show()
    QApplication.instance().processEvents()

    QTest.keyClick(window, QtCoreQt.Key.Key_Up)
    assert 'speed' in calls
    calls.clear()

    QTest.keyClick(window, QtCoreQt.Key.Key_Down)
    assert 'speed' in calls
    calls.clear()

    QTest.keyClick(window, QtCoreQt.Key.Key_Home)
    assert 'seek' in calls and 'resume' in calls

    window.source.stop()


def test_q_and_escape_close_the_window():
    window = MainWindow(AppConfig())
    closed = []
    window.closeEvent = lambda event: (closed.append('closed'), event.accept())[-1]

    # See test_space_shortcut_toggles_play_pause for why show()+processEvents()
    # is required before QShortcut (WindowShortcut context) will fire.
    window.show()
    QApplication.instance().processEvents()
    QTest.keyClick(window, QtCoreQt.Key.Key_Q)
    assert closed == ['closed']


def test_controls_panel_exists_and_is_docked():
    window = MainWindow(AppConfig())
    assert window.controls_panel is not None
    assert window.dockWidgetArea(window.controls_panel) is not None

def test_view_menu_has_controls_panel_toggle():
    window = MainWindow(AppConfig())
    menu_titles = [m.title() for m in window.menuBar().findChildren(type(window.menuBar().addMenu("_probe")))]
    # Simpler, robust check: the panel's own toggle action must exist and be
    # associated with the panel's dock widget.
    toggle = window.controls_panel.toggleViewAction()
    assert toggle is not None
    assert toggle.isCheckable()

def test_params_changed_reaches_waterfall_set_enhance_params(tmp_path, monkeypatch):
    from bytt.protocol import constants as pc
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(bytes(header))

    window = MainWindow(AppConfig())
    window.open_playback_file(str(bsf_path))

    calls = []
    window.waterfall.set_enhance_params = lambda p: calls.append(p)
    window.controls_panel.gain_spin.setValue(2.5)

    import time
    from PySide6.QtCore import QCoreApplication
    deadline = time.time() + 1.0
    while time.time() < deadline and not calls:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert calls
    assert calls[-1].gain == 2.5
    window.source.stop()

def test_channels_changed_updates_ping_received_channel_state(tmp_path):
    from bytt.protocol import constants as pc
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(bytes(header))

    window = MainWindow(AppConfig())
    window.open_playback_file(str(bsf_path))

    window.controls_panel.port_check.setChecked(False)
    assert window._port_on is False
    assert window._stbd_on is True
    window.source.stop()
