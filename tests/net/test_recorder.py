from pathlib import Path
from bytt.net.recorder import Recorder


def test_start_stop_writes_packets_to_a_file(tmp_path):
    rec = Recorder(directory=str(tmp_path))
    assert not rec.is_recording

    path = rec.start()
    assert rec.is_recording
    rec.write_packet(b"abc")
    rec.write_packet(b"def")
    rec.stop()
    assert not rec.is_recording

    assert Path(path).read_bytes() == b"abcdef"


def test_write_packet_before_start_is_a_noop(tmp_path):
    rec = Recorder(directory=str(tmp_path))
    rec.write_packet(b"abc")  # must not raise
    assert list(tmp_path.iterdir()) == []
