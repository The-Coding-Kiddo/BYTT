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

def _build_bsf_bytes_n(n_pings, n_samples=4):
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    body = b''.join(_build_ping_record(n_samples) for _ in range(n_pings))
    return bytes(header) + body

def test_pause_halts_ping_emission_and_resume_continues(tmp_path):
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(_build_bsf_bytes_n(n_pings=50))

    received = []
    source = PlaybackSource(str(bsf_path), pings_per_second=1000)
    source.ping_received.connect(lambda port, stbd, meta: received.append(meta))
    source.start()

    deadline = time.time() + 3.0
    while time.time() < deadline and len(received) < 5:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert len(received) >= 5

    source.pause()
    QCoreApplication.processEvents()
    count_at_pause = len(received)
    time.sleep(0.2)
    QCoreApplication.processEvents()
    assert len(received) == count_at_pause, "no new pings should arrive while paused"

    source.resume()
    deadline = time.time() + 3.0
    while time.time() < deadline and len(received) <= count_at_pause:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert len(received) > count_at_pause, "playback should continue after resume"

    source.stop()

def test_pause_and_resume_are_idempotent(tmp_path):
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(_build_bsf_bytes_n(n_pings=3))
    source = PlaybackSource(str(bsf_path), pings_per_second=1000)
    source.pause()
    source.pause()  # must not raise
    source.resume()
    source.resume()  # must not raise
    source.stop()

def test_position_changed_reports_index_and_total(tmp_path):
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(_build_bsf_bytes_n(n_pings=5))

    positions = []
    source = PlaybackSource(str(bsf_path), pings_per_second=1000)
    source.position_changed.connect(lambda idx, total: positions.append((idx, total)))
    source.start()

    deadline = time.time() + 3.0
    while time.time() < deadline and len(positions) < 5:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    source.stop()

    assert len(positions) >= 5
    assert positions[0] == (0, 5)
    assert all(total == 5 for _idx, total in positions)
