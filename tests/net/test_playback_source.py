import struct
import time
import pytest
from PySide6.QtCore import QCoreApplication
from bytt.protocol import constants as pc
from bytt.net.playback_source import PlaybackSource

@pytest.fixture(scope="module", autouse=True)
def qt_app():
    # tests/conftest.py's session-scoped fixture already created a real
    # QApplication before this fixture runs; QCoreApplication.instance()
    # returns that same (compatible) instance here.
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

    # Pin the position deterministically instead of relying on the
    # background thread still being at index 0 by the time pause() lands.
    source.seek(0)
    deadline = time.time() + 2.0
    while time.time() < deadline and len(received) < 2:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    assert len(received) == 2
    count_before = len(received)  # at index 0

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

def test_set_speed_measurably_speeds_up_emission(tmp_path):
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(_build_bsf_bytes_n(n_pings=300))

    received = []
    source = PlaybackSource(str(bsf_path))  # default ~16 pings/sec
    source.ping_received.connect(lambda port, stbd, meta: received.append(meta))
    source.start()

    deadline = time.time() + 2.0
    while time.time() < deadline and len(received) < 1:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    assert len(received) >= 1

    source.set_speed(0.001)
    count_before = len(received)
    window_deadline = time.time() + 0.5
    while time.time() < window_deadline:
        QCoreApplication.processEvents()
        time.sleep(0.005)

    assert len(received) - count_before >= 20, (
        "set_speed with a much shorter interval should allow far more than "
        "the ~8 pings the default ~16/sec rate would produce in 0.5s"
    )

    source.stop()

def test_set_speed_zero_does_not_crash_and_emits_rapidly(tmp_path):
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(_build_bsf_bytes_n(n_pings=300))

    received = []
    source = PlaybackSource(str(bsf_path))
    source.ping_received.connect(lambda port, stbd, meta: received.append(meta))
    source.start()

    deadline = time.time() + 2.0
    while time.time() < deadline and len(received) < 1:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    assert len(received) >= 1

    source.set_speed(0.0)  # must not raise
    count_before = len(received)
    window_deadline = time.time() + 0.5
    while time.time() < window_deadline:
        QCoreApplication.processEvents()
        time.sleep(0.005)

    assert len(received) - count_before >= 20

    source.stop()

def test_controls_still_work_after_playback_reaches_end_of_file(tmp_path):
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(_build_bsf_bytes_n(n_pings=3))

    positions = []
    statuses = []
    source = PlaybackSource(str(bsf_path), pings_per_second=1000)  # fast, run to EOF quickly
    source.position_changed.connect(lambda idx, total: positions.append(idx))
    source.status_changed.connect(statuses.append)
    source.start()

    # Let playback run unattended to natural end-of-file (index 2, the last
    # of 3 pings). The background thread must NOT exit here -- it should
    # clamp at the last index, pause itself, and stay alive/seekable.
    deadline = time.time() + 3.0
    while time.time() < deadline and 'playback finished' not in statuses:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert 'playback finished' in statuses

    # Give it a moment to make sure it doesn't keep emitting once finished.
    QCoreApplication.processEvents()

    # Controls issued after natural end-of-file must still work: seek(0)
    # should emit ping_received/position_changed for index 0.
    positions_before = len(positions)
    source.seek(0)
    deadline = time.time() + 2.0
    while time.time() < deadline and len(positions) == positions_before:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert len(positions) > positions_before
    assert positions[-1] == 0

    # resume() after that must not crash.
    source.resume()
    QCoreApplication.processEvents()
    time.sleep(0.05)
    QCoreApplication.processEvents()

    source.stop()

def _build_nav_record(lat, lon, ts=0.0):
    rec = bytearray(pc.NAV_RECORD_SIZE)
    struct.pack_into('<I', rec, 0, pc.BSF_RECORD_TYPE)
    struct.pack_into('<I', rec, 12, pc.NAV_RECORD_SIZE)
    struct.pack_into('<d', rec, pc.NAV_TS_OFFSET, ts)
    struct.pack_into('<d', rec, pc.NAV_LAT_OFFSET, lat)
    struct.pack_into('<d', rec, pc.NAV_LON_OFFSET, lon)
    return bytes(rec)

def test_pings_before_first_nav_record_have_no_nav_fix(tmp_path):
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(_build_bsf_bytes_n(n_pings=3))  # no nav records at all

    received = []
    source = PlaybackSource(str(bsf_path), pings_per_second=1000)
    source.ping_received.connect(lambda port, stbd, meta: received.append(meta))
    source.start()
    deadline = time.time() + 3.0
    while time.time() < deadline and len(received) < 3:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    source.stop()

    assert len(received) == 3
    assert all(m['nav_fix'] is None for m in received)

def test_nav_record_attaches_to_every_subsequent_ping_until_superseded(tmp_path):
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    ping = _build_ping_record(n_samples=4)
    nav1 = _build_nav_record(lat=41.0, lon=36.0)
    nav2 = _build_nav_record(lat=42.0, lon=37.0)
    # layout: ping, ping, nav1, ping, ping, nav2, ping
    body = ping + ping + nav1 + ping + ping + nav2 + ping
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(bytes(header) + body)

    received = []
    source = PlaybackSource(str(bsf_path), pings_per_second=1000)
    source.ping_received.connect(lambda port, stbd, meta: received.append(meta))
    source.start()
    deadline = time.time() + 3.0
    while time.time() < deadline and len(received) < 5:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    source.stop()

    assert len(received) == 5
    assert received[0]['nav_fix'] is None
    assert received[1]['nav_fix'] is None
    assert abs(received[2]['nav_fix']['lat'] - 41.0) < 1e-9
    assert abs(received[3]['nav_fix']['lat'] - 41.0) < 1e-9
    assert abs(received[4]['nav_fix']['lat'] - 42.0) < 1e-9
    assert received[4]['nav_fix']['heading'] is None
    assert received[4]['nav_fix']['height'] is None
