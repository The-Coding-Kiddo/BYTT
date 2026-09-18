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
