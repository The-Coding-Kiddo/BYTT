import pytest
from PySide6.QtWidgets import QApplication
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
