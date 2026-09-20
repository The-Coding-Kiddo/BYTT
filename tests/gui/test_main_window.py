import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt as QtCoreQt
from bytt.gui.main_window import MainWindow, NADIR_GAP_FRACTION
from bytt.config import AppConfig

@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app

def test_main_window_starts_disconnected():
    window = MainWindow(AppConfig())
    assert window.statusBar().currentMessage() in ("", "disconnected")
    assert window.source is None

def test_record_action_enabled_on_connect_and_writes_packets(tmp_path, monkeypatch):
    window = MainWindow(AppConfig())
    monkeypatch.setattr(window.recorder, "directory", tmp_path)
    assert not window.record_action.isEnabled()

    window.connect_towfish("127.0.0.1", 1, 2)  # bogus port, connect fails async in a thread
    assert window.record_action.isEnabled()

    window.record_action.setChecked(True)
    assert window.recorder.is_recording
    window.source.raw_packet_received.emit(b"hello")
    window.record_action.setChecked(False)
    assert not window.recorder.is_recording

    written = list(tmp_path.iterdir())
    assert len(written) == 1
    assert written[0].read_bytes() == b"hello"

    window.disconnect_source()
    window.command_client.close()


def test_recording_indicator_shows_while_recording_and_clears_after(tmp_path, monkeypatch):
    window = MainWindow(AppConfig())
    monkeypatch.setattr(window.recorder, "directory", tmp_path)
    window.connect_towfish("127.0.0.1", 1, 2)
    assert window.recording_indicator.text() == ""

    window.record_action.setChecked(True)
    assert "REC" in window.recording_indicator.text()

    window.record_action.setChecked(False)
    assert window.recording_indicator.text() == ""

    window.disconnect_source()
    window.command_client.close()


def test_recording_indicator_clears_on_disconnect(tmp_path, monkeypatch):
    window = MainWindow(AppConfig())
    monkeypatch.setattr(window.recorder, "directory", tmp_path)
    window.connect_towfish("127.0.0.1", 1, 2)
    window.record_action.setChecked(True)
    assert "REC" in window.recording_indicator.text()

    window.disconnect_source()
    assert window.recording_indicator.text() == ""
    window.command_client.close()


def test_disconnect_source_stops_recording_and_disables_action(tmp_path, monkeypatch):
    window = MainWindow(AppConfig())
    monkeypatch.setattr(window.recorder, "directory", tmp_path)
    window.connect_towfish("127.0.0.1", 1, 2)
    window.record_action.setChecked(True)
    assert window.recorder.is_recording

    window.disconnect_source()
    assert not window.recorder.is_recording
    assert not window.record_action.isEnabled()
    assert not window.record_action.isChecked()
    window.command_client.close()


def test_ping_received_records_swath_slot_but_no_quad_until_far_range_is_measured():
    # Swath geometry no longer depends on heading at all -- it's derived
    # from the track's own recorded positions (see GPSTrack.swath_quads).
    # A fresh window has no buffered ping history yet, so the far edge is
    # never measured -- every fix still gets a positionally-aligned slot
    # (near_range_m recorded, far_range_m None), but that must never yield
    # a drawn quad: an unearned "we scanned out to the full requested
    # range" claim is worse than no claim at all (see _current_echo_range_m).
    window = MainWindow(AppConfig())
    import numpy as np
    port_raw = np.linspace(0.0, 1.0, 512, dtype=np.float32)
    stbd_raw = np.linspace(1.0, 0.0, 512, dtype=np.float32)

    meta1 = {'nav_fix': {'lat': 41.30, 'lon': 36.33, 'heading': None, 'height': None}}
    window._on_ping_received(port_raw, stbd_raw, meta1)
    assert len(window.gps_track.swath_near_range_m) == 1
    assert window.gps_track.swath_far_range_m[0] is None
    assert list(window.gps_track.swath_quads()) == []

    meta2 = {'nav_fix': {'lat': 41.31, 'lon': 36.33, 'heading': None, 'height': None}}
    window._on_ping_received(port_raw, stbd_raw, meta2)
    assert len(window.gps_track.swath_near_range_m) == 2
    assert window.gps_track.swath_far_range_m[1] is None
    assert list(window.gps_track.swath_quads()) == []


def test_swath_quad_appears_once_far_range_is_actually_measured(monkeypatch):
    window = MainWindow(AppConfig())
    monkeypatch.setattr(
        window.waterfall, "detect_channel_echo_edges",
        lambda channel_w, gap, n_rows=200: {'port': (10, 400), 'stbd': (10, 400)})

    import numpy as np
    port_raw = np.linspace(0.0, 1.0, 512, dtype=np.float32)
    stbd_raw = np.linspace(1.0, 0.0, 512, dtype=np.float32)
    meta1 = {'nav_fix': {'lat': 41.30, 'lon': 36.33, 'heading': None, 'height': None}}
    window._on_ping_received(port_raw, stbd_raw, meta1)
    meta2 = {'nav_fix': {'lat': 41.31, 'lon': 36.33, 'heading': None, 'height': None}}
    window._on_ping_received(port_raw, stbd_raw, meta2)

    assert window.gps_track.swath_far_range_m[0] is not None
    assert len(list(window.gps_track.swath_quads())) == 1


def test_swath_prefers_pings_own_reported_range_over_panel_setting(monkeypatch):
    # The panel's configured range is only what we'd ASK the sonar to do.
    # The ping's own max_range_m (real for live, and now computed from the
    # file header for playback) is what it actually reported -- must be
    # used as the meters-per-sample scale in preference to the panel value.
    window = MainWindow(AppConfig())
    captured_channel_w = {}
    def fake_detect(channel_w, gap, n_rows=200):
        captured_channel_w['value'] = channel_w
        return {'port': (10, 400), 'stbd': (10, 400)}
    monkeypatch.setattr(window.waterfall, "detect_channel_echo_edges", fake_detect)

    import numpy as np
    port_raw = np.linspace(0.0, 1.0, 512, dtype=np.float32)
    stbd_raw = np.linspace(1.0, 0.0, 512, dtype=np.float32)
    # Panel defaults to 50m; the ping itself reports a very different 17.5m
    # (the real figure from the 20250530_093513.bsf recording).
    meta1 = {'nav_fix': {'lat': 41.30, 'lon': 36.33, 'heading': None, 'height': None},
             'max_range_m': 17.5}
    window._on_ping_received(port_raw, stbd_raw, meta1)
    meta2 = {'nav_fix': {'lat': 41.31, 'lon': 36.33, 'heading': None, 'height': None},
             'max_range_m': 17.5}
    window._on_ping_received(port_raw, stbd_raw, meta2)

    channel_w = captured_channel_w['value']
    expected_far = 400 / channel_w * 17.5
    assert abs(window.gps_track.swath_far_range_m[0] - expected_far) < 0.01
    assert window._current_swath_range_m() == 50  # confirms the panel value was NOT what was used


def test_swath_uses_real_detected_edges_once_buffer_is_full(monkeypatch):
    window = MainWindow(AppConfig())
    monkeypatch.setattr(
        window.waterfall, "detect_channel_echo_edges",
        lambda channel_w, gap, n_rows=200: {'port': (10, 400), 'stbd': (10, 400)})

    import numpy as np
    port_raw = np.linspace(0.0, 1.0, 512, dtype=np.float32)
    stbd_raw = np.linspace(1.0, 0.0, 512, dtype=np.float32)
    meta = {'nav_fix': {'lat': 41.30, 'lon': 36.33, 'heading': 0.0, 'height': None}}
    window._on_ping_received(port_raw, stbd_raw, meta)

    range_m = window._current_swath_range_m()
    channel_w = (window.waterfall.row_width - 8) // 2
    expected_far = 400 / channel_w * range_m
    assert abs(window.gps_track.swath_far_range_m[0] - expected_far) < 0.1


def test_near_edge_ignores_detector_even_when_far_edge_is_used(monkeypatch):
    # Deliberate: the near-edge (nadir gap) detector was checked against a
    # real recording and found unreliable (see _current_echo_range_m's
    # docstring) -- it must keep using the documented fallback guess even
    # when a detected near_idx IS available and the far edge does use
    # detection. This locks that choice in so it can't silently regress.
    window = MainWindow(AppConfig())
    monkeypatch.setattr(
        window.waterfall, "detect_channel_echo_edges",
        lambda channel_w, gap, n_rows=200: {'port': (1, 400), 'stbd': (0, 400)})

    import numpy as np
    port_raw = np.linspace(0.0, 1.0, 512, dtype=np.float32)
    stbd_raw = np.linspace(1.0, 0.0, 512, dtype=np.float32)
    meta = {'nav_fix': {'lat': 41.30, 'lon': 36.33, 'heading': 0.0, 'height': None}}
    window._on_ping_received(port_raw, stbd_raw, meta)

    range_m = window._current_swath_range_m()
    expected_near = range_m * NADIR_GAP_FRACTION  # the fallback, not the detector's ~0
    assert abs(window.gps_track.swath_near_range_m[0] - expected_near) < 1e-6


def test_swath_leaves_a_nadir_gap_using_fallback_fraction():
    # The near-range guess is still computed and recorded even before the
    # far edge is ever measured (it's cheap, and ready the moment real
    # far-edge data does arrive) -- but with no far measurement yet, this
    # window correctly draws no swath at all (see the test above).
    window = MainWindow(AppConfig())
    import numpy as np
    port_raw = np.linspace(0.0, 1.0, 512, dtype=np.float32)
    stbd_raw = np.linspace(1.0, 0.0, 512, dtype=np.float32)

    meta = {'nav_fix': {'lat': 41.30, 'lon': 36.33, 'heading': 0.0, 'height': None}}
    window._on_ping_received(port_raw, stbd_raw, meta)

    range_m = window._current_swath_range_m()
    expected_near = range_m * 0.08
    assert abs(window.gps_track.swath_near_range_m[0] - expected_near) < 1e-6
    assert window.gps_track.swath_far_range_m[0] is None


def test_swath_uses_reported_height_as_nadir_gap_when_available():
    window = MainWindow(AppConfig())
    import numpy as np
    port_raw = np.linspace(0.0, 1.0, 512, dtype=np.float32)
    stbd_raw = np.linspace(1.0, 0.0, 512, dtype=np.float32)

    meta = {'nav_fix': {'lat': 41.30, 'lon': 36.33, 'heading': 0.0, 'height': 7.5}}
    window._on_ping_received(port_raw, stbd_raw, meta)

    assert abs(window.gps_track.swath_near_range_m[0] - 7.5) < 1e-6


def test_data_indicator_turns_warning_after_no_data_timeout():
    window = MainWindow(AppConfig())
    window.connect_towfish("127.0.0.1", 1, 2)
    window._on_no_data_timeout()
    assert window.connection_indicators.data._state == "warning"
    window.disconnect_source()
    window.command_client.close()


def test_data_indicator_turns_ok_when_a_ping_arrives():
    window = MainWindow(AppConfig())
    window.connect_towfish("127.0.0.1", 1, 2)
    import numpy as np
    port_raw = np.linspace(0.0, 1.0, 512, dtype=np.float32)
    stbd_raw = np.linspace(1.0, 0.0, 512, dtype=np.float32)
    window._on_ping_received(port_raw, stbd_raw, {})
    assert window.connection_indicators.data._state == "ok"
    window.disconnect_source()
    window.command_client.close()


def test_link_status_changed_updates_sonar_indicator():
    window = MainWindow(AppConfig())
    window.connect_towfish("127.0.0.1", 1, 2)
    window._on_link_status_changed(True, True)
    assert window.connection_indicators.sonar._state == "ok"
    window._on_link_status_changed(True, False)
    assert window.connection_indicators.sonar._state == "warning"
    window._on_link_status_changed(False, False)
    assert window.connection_indicators.sonar._state == "error"
    window.disconnect_source()
    window.command_client.close()


def test_disconnect_resets_indicators():
    window = MainWindow(AppConfig())
    window.connect_towfish("127.0.0.1", 1, 2)
    window.connection_indicators.set_data_state("ok")
    window.disconnect_source()
    assert window.connection_indicators.data._state == "unknown"
    window.command_client.close()


def test_connect_towfish_warns_on_segment_mismatch():
    config = AppConfig(pc_ip="10.0.0.5")  # different /24 than the default towfish_ip
    window = MainWindow(config)
    window.connect_towfish("192.168.1.16", 1, 2)
    assert "not on the same network segment" in window.statusBar().currentMessage()
    window.disconnect_source()
    window.command_client.close()


def test_no_data_timer_fires_warning_after_silence():
    window = MainWindow(AppConfig())
    window.connect_towfish("127.0.0.1", 1, 2)
    window._on_no_data_timeout()  # simulate the timer firing directly, no real 5s wait
    assert "no data received" in window.statusBar().currentMessage()
    window.disconnect_source()
    window.command_client.close()


def test_ping_received_restarts_no_data_timer_in_live_mode():
    window = MainWindow(AppConfig())
    window.connect_towfish("127.0.0.1", 1, 2)
    window._no_data_timer.stop()
    import numpy as np
    port_raw = np.linspace(0.0, 1.0, 512, dtype=np.float32)
    stbd_raw = np.linspace(1.0, 0.0, 512, dtype=np.float32)
    window._on_ping_received(port_raw, stbd_raw, {})
    assert window._no_data_timer.isActive()
    window.disconnect_source()
    window.command_client.close()


def test_sonar_control_apply_sends_command_when_connected():
    window = MainWindow(AppConfig())
    window.connect_towfish("127.0.0.1", 1, 2)  # command_client fails to connect, that's fine
    window.sonar_control_panel.hf_range.setValue(99)
    window.sonar_control_panel.apply_button.click()
    # No real command server -- just confirm the failure is surfaced, not silently swallowed
    # or crashing, and that the panel's command actually reached MainWindow's handler.
    assert "sonar command failed" in window.statusBar().currentMessage()
    window.disconnect_source()
    window.command_client.close()


def test_sonar_control_panel_has_view_menu_toggle():
    window = MainWindow(AppConfig())
    view_menu = None
    for action in window.menuBar().actions():
        if action.text() == "&View":
            view_menu = action.menu()
    texts = {a.text() for a in view_menu.actions()}
    assert any("Sonar Control" in t for t in texts)


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


def test_main_window_has_gps_track_and_panel():
    window = MainWindow(AppConfig())
    from bytt.nav.gps_track import GPSTrack
    from bytt.gui.gps_panel import GpsPanel
    assert isinstance(window.gps_track, GPSTrack)
    assert isinstance(window.gps_panel, GpsPanel)
    assert window._latest_heading is None


def test_connect_towfish_and_open_playback_each_create_a_fresh_gps_track(tmp_path):
    from bytt.protocol import constants as pc
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(bytes(header))

    window = MainWindow(AppConfig())
    first_track = window.gps_track
    window.open_playback_file(str(bsf_path))
    assert window.gps_track is not first_track
    window.source.stop()

    second_track = window.gps_track
    window.open_playback_file(str(bsf_path))
    assert window.gps_track is not second_track
    window.source.stop()


def test_on_ping_received_feeds_nav_fix_into_gps_track_and_refreshes_panel():
    import numpy as np
    window = MainWindow(AppConfig())
    port_raw = np.linspace(0.0, 1.0, 512, dtype=np.float32)
    stbd_raw = np.linspace(1.0, 0.0, 512, dtype=np.float32)

    meta = {'nav_fix': {'lat': 41.47, 'lon': 36.13, 'heading': 90.0, 'height': 5.0}}
    window._on_ping_received(port_raw, stbd_raw, meta)

    assert window.gps_track.has_data
    assert window._latest_heading == 90.0


def test_on_ping_received_with_no_nav_fix_does_not_touch_gps_track():
    import numpy as np
    window = MainWindow(AppConfig())
    port_raw = np.linspace(0.0, 1.0, 512, dtype=np.float32)
    stbd_raw = np.linspace(1.0, 0.0, 512, dtype=np.float32)

    window._on_ping_received(port_raw, stbd_raw, {'nav_fix': None})
    assert not window.gps_track.has_data


def test_open_playback_file_clears_stale_gps_panel_display(tmp_path):
    import numpy as np
    from bytt.protocol import constants as pc
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(bytes(header))  # header-only: 0 pings, exercises the path safely

    window = MainWindow(AppConfig())
    port_raw = np.linspace(0.0, 1.0, 512, dtype=np.float32)
    stbd_raw = np.linspace(1.0, 0.0, 512, dtype=np.float32)
    meta = {'nav_fix': {'lat': 41.47, 'lon': 36.13, 'heading': 90.0, 'height': 5.0}}
    window._on_ping_received(port_raw, stbd_raw, meta)
    assert window.gps_panel._track_curve.getData()[0] is not None
    assert len(window.gps_panel._track_curve.getData()[0]) == 1

    window.open_playback_file(str(bsf_path))
    xs, ys = window.gps_panel._track_curve.getData()
    assert xs is None or len(xs) == 0
    assert ys is None or len(ys) == 0
    window.source.stop()


def test_on_ping_received_does_not_readd_unchanged_nav_fix():
    import numpy as np
    window = MainWindow(AppConfig())
    port_raw = np.linspace(0.0, 1.0, 512, dtype=np.float32)
    stbd_raw = np.linspace(1.0, 0.0, 512, dtype=np.float32)

    meta = {'nav_fix': {'lat': 41.47, 'lon': 36.13, 'heading': 90.0, 'height': 5.0}}
    window._on_ping_received(port_raw, stbd_raw, meta)
    window._on_ping_received(port_raw, stbd_raw, meta)
    assert len(window.gps_track.xs) == 1

    meta2 = {'nav_fix': {'lat': 41.48, 'lon': 36.14, 'heading': 91.0, 'height': 5.0}}
    window._on_ping_received(port_raw, stbd_raw, meta2)
    assert len(window.gps_track.xs) == 2


def test_view_menu_has_gps_panel_toggle():
    window = MainWindow(AppConfig())
    view_menu = None
    for action in window.menuBar().actions():
        if action.text() == "&View":
            view_menu = action.menu()
    assert view_menu is not None
    texts = {a.text() for a in view_menu.actions()}
    assert any("GPS" in t for t in texts)
