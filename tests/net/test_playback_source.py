import struct
import time
import pytest
from PySide6.QtCore import QCoreApplication
from bytt.protocol import constants as pc
from bytt.net.playback_source import PlaybackSource

@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app

def _build_ping_record(n_samples=4):
    size = pc.SAMPLE_OFFSET + 2 * n_samples * 4
    rec = bytearray(size)
    struct.pack_into('<I', rec, 0, pc.BSF_RECORD_TYPE)
    struct.pack_into('<I', rec, 12, size)
    struct.pack_into('<H', rec, pc.CH_SR_OFFSET + 2, 400)
    struct.pack_into('<I', rec, pc.CH_SR_OFFSET + 28, 96000)
    struct.pack_into('<I', rec, pc.CH_SR_OFFSET + 52, n_samples)
    return bytes(rec)

def _build_bsf_bytes(n_pings=3, n_samples=4):
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    body = b''.join(_build_ping_record(n_samples) for _ in range(n_pings))
    return bytes(header) + body

def test_playback_emits_one_ping_received_per_record(tmp_path):
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(_build_bsf_bytes(n_pings=3))

    received = []
    source = PlaybackSource(str(bsf_path), pings_per_second=1000)  # fast for the test
    source.ping_received.connect(lambda port, stbd, meta: received.append(meta))
    source.start()
    deadline = time.time() + 3.0
    while time.time() < deadline and len(received) < 3:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    source.stop()

    assert len(received) == 3

def test_playback_missing_file_emits_status_error(tmp_path):
    statuses = []
    source = PlaybackSource(str(tmp_path / "missing.bsf"))
    source.status_changed.connect(statuses.append)
    source.start()
    deadline = time.time() + 2.0
    while time.time() < deadline and not statuses:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    source.stop()
    assert any("not found" in s or "error" in s.lower() for s in statuses)
