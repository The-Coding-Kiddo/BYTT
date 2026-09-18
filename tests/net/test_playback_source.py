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

def test_step_forward_emits_exactly_one_ping_and_pauses(tmp_path):
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
    time.sleep(0.1)
    QCoreApplication.processEvents()
    count_before = len(received)

    source.step_forward()
    deadline = time.time() + 2.0
    while time.time() < deadline and len(received) == count_before:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert len(received) == count_before + 1

    # Confirm it's paused again after the step (no further auto-advance).
    time.sleep(0.2)
    QCoreApplication.processEvents()
    assert len(received) == count_before + 1

    source.stop()

def test_step_forward_at_last_ping_is_a_noop(tmp_path):
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(_build_bsf_bytes_n(n_pings=3))

    received = []
    source = PlaybackSource(str(bsf_path), pings_per_second=20)
    source.ping_received.connect(lambda port, stbd, meta: received.append(meta))
    source.start()

    # Wait for the very first ping (index 0) so we know self._pings is
    # loaded, then pause immediately -- at 20 pings/sec (50ms interval),
    # our reaction time is comfortably inside the window before the next
    # scheduled emission, so pausing here is deterministic.
    deadline = time.time() + 2.0
    while time.time() < deadline and len(received) < 1:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    assert len(received) >= 1
    source.pause()
    QCoreApplication.processEvents()

    # Now deterministically jump to the last index (2) while paused.
    source.seek(2)
    deadline = time.time() + 2.0
    while time.time() < deadline and len(received) < 2:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    assert len(received) == 2
    count_before = len(received)

    source.step_forward()
    time.sleep(0.2)
    QCoreApplication.processEvents()
    assert len(received) == count_before, "stepping past the last ping must not emit"

    source.stop()

def test_step_backward_at_first_ping_is_a_noop(tmp_path):
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(_build_bsf_bytes_n(n_pings=3))

    received = []
    source = PlaybackSource(str(bsf_path), pings_per_second=20)
    source.ping_received.connect(lambda port, stbd, meta: received.append(meta))
    source.start()

    deadline = time.time() + 2.0
    while time.time() < deadline and len(received) < 1:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    assert len(received) >= 1
    source.pause()
    QCoreApplication.processEvents()
    count_before = len(received)  # should be 1, at index 0

    source.step_backward()
    time.sleep(0.2)
    QCoreApplication.processEvents()
    assert len(received) == count_before, "stepping before the first ping must not emit"

    source.stop()

def test_seek_jumps_to_arbitrary_position(tmp_path):
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(_build_bsf_bytes_n(n_pings=20))

    positions = []
    source = PlaybackSource(str(bsf_path), pings_per_second=20)
    source.position_changed.connect(lambda idx, total: positions.append(idx))
    source.start()

    deadline = time.time() + 2.0
    while time.time() < deadline and len(positions) < 1:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    assert len(positions) >= 1
    source.pause()
    QCoreApplication.processEvents()

    source.seek(15)
    deadline = time.time() + 2.0
    while time.time() < deadline and 15 not in positions:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    assert 15 in positions

    source.stop()

def test_seek_clamps_out_of_range_input(tmp_path):
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(_build_bsf_bytes_n(n_pings=5))

    positions = []
    source = PlaybackSource(str(bsf_path), pings_per_second=20)
    source.position_changed.connect(lambda idx, total: positions.append(idx))
    source.start()

    deadline = time.time() + 2.0
    while time.time() < deadline and len(positions) < 1:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    assert len(positions) >= 1
    source.pause()
    QCoreApplication.processEvents()

    source.seek(-5)  # must clamp to 0, not raise
    deadline = time.time() + 2.0
    while time.time() < deadline and len(positions) < 2:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    assert positions[-1] == 0

    source.seek(9999)  # must clamp to the last valid index (4), not raise
    deadline = time.time() + 2.0
    while time.time() < deadline and len(positions) < 3:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    assert positions[-1] == 4

    source.stop()
