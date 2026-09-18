import socket
import struct
import threading
import time
import pytest
from PySide6.QtCore import QCoreApplication
from bytt.protocol import constants as pc
from bytt.protocol.commands import build_command_107, SonarWorkCommand
from bytt.net.live_client import LiveClient

@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app

def _fake_server(port_holder, packet_to_send, ready, stop):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port_holder.append(srv.getsockname()[1])
    srv.settimeout(3.0)
    ready.set()
    try:
        conn, _ = srv.accept()
    except socket.timeout:
        return
    conn.sendall(packet_to_send)
    while not stop.is_set():
        time.sleep(0.05)
    conn.close()
    srv.close()

def test_live_client_emits_ping_received_for_a_166_status_packet():
    header = struct.pack('<IHHIHHHHI', pc.PACKET_IDENTIFIER, pc.PACKET_HEADER_SIZE,
                          pc.PACKET_VERSION, 0, pc.PACKET_FLAG_CHECKSUM_BIT, 0, 1, 1,
                          pc.PACKET_TYPE_166)
    content = bytes([0x11])  # data+cmd connected
    size = len(header) + len(content) + 4
    header = header[:pc.PACKET_SIZE_OFFSET] + struct.pack('<I', size) + header[pc.PACKET_SIZE_OFFSET+4:]
    from bytt.protocol.packets import compute_checksum
    body = header + content
    packet = body + struct.pack('<I', compute_checksum(body))

    port_holder, ready, stop = [], threading.Event(), threading.Event()
    server = threading.Thread(target=_fake_server, args=(port_holder, packet, ready, stop), daemon=True)
    server.start()
    ready.wait(timeout=2.0)

    statuses = []
    client = LiveClient("127.0.0.1", port_holder[0])
    client.status_changed.connect(statuses.append)
    client.start()
    deadline = time.time() + 3.0
    while time.time() < deadline and not any("hw:" in s for s in statuses):
        QCoreApplication.processEvents()
        time.sleep(0.02)
    client.stop()
    stop.set()
    server.join(timeout=2.0)

    assert any("connected (data+cmd)" in s for s in statuses)


def _make_multi_header(frame_id, total, packet_num, packet_type=pc.PACKET_TYPE_3101):
    """Build a minimal header-sized packet with the multi-packet fields set
    at the correct offsets, reusing the same struct pattern as the 166-status
    test above. _reassemble() only reads header fields, so no body/checksum
    is needed here."""
    header = struct.pack('<IHHIHHHHI', pc.PACKET_IDENTIFIER, pc.PACKET_HEADER_SIZE,
                          pc.PACKET_VERSION, 0, pc.PACKET_FLAG_MULTI_BIT,
                          frame_id, total, packet_num, packet_type)
    return header


def test_reassemble_clean_three_fragment_frame():
    client = LiveClient("127.0.0.1", 0)
    packet_type = pc.PACKET_TYPE_3101
    frame_id = 1
    total = 3
    assert client._reassemble(_make_multi_header(frame_id, total, 1, packet_type),
                               packet_type, b'AAA') is None
    assert client._reassemble(_make_multi_header(frame_id, total, 2, packet_type),
                               packet_type, b'BBB') is None
    result = client._reassemble(_make_multi_header(frame_id, total, 3, packet_type),
                                 packet_type, b'CCC')
    assert result == b'AAABBBCCC'
    assert client._partial_frames == {}


def test_reassemble_out_of_range_packet_num_does_not_raise_or_corrupt_state():
    client = LiveClient("127.0.0.1", 0)
    packet_type = pc.PACKET_TYPE_3101
    frame_id = 2
    total = 3
    # Out-of-range packet_num (5) for a 3-fragment frame must be dropped,
    # not raise, and must not leave the partial-frame entry in a state that
    # would later KeyError on join.
    result = client._reassemble(_make_multi_header(frame_id, total, 5, packet_type),
                                 packet_type, b'ZZZ')
    assert result is None
    # A subsequent, well-formed 3-fragment frame with the same frame_id must
    # still reassemble correctly (state wasn't corrupted by the bad fragment).
    assert client._reassemble(_make_multi_header(frame_id, total, 1, packet_type),
                               packet_type, b'AAA') is None
    assert client._reassemble(_make_multi_header(frame_id, total, 2, packet_type),
                               packet_type, b'BBB') is None
    result = client._reassemble(_make_multi_header(frame_id, total, 3, packet_type),
                                 packet_type, b'CCC')
    assert result == b'AAABBBCCC'


def test_reassemble_via_handle_packet_does_not_kill_read_loop():
    # Exercise the same scenario through _handle_packet (the path that used
    # to propagate a KeyError up into _run's blanket except-and-die), using
    # valid checksums like the rest of the packets flowing through this path.
    from bytt.protocol.packets import compute_checksum

    client = LiveClient("127.0.0.1", 0)
    statuses = []
    client.status_changed.connect(statuses.append)

    def build_packet(frame_id, total, packet_num, content):
        header = struct.pack('<IHHIHHHHI', pc.PACKET_IDENTIFIER, pc.PACKET_HEADER_SIZE,
                              pc.PACKET_VERSION, 0,
                              pc.PACKET_FLAG_MULTI_BIT | pc.PACKET_FLAG_CHECKSUM_BIT,
                              frame_id, total, packet_num, pc.PACKET_TYPE_166)
        size = len(header) + len(content) + 4
        header = header[:pc.PACKET_SIZE_OFFSET] + struct.pack('<I', size) + \
            header[pc.PACKET_SIZE_OFFSET + 4:]
        body = header + content
        return body + struct.pack('<I', compute_checksum(body))

    # Malformed frame: total=3 but packet_num values 5, 6, 7 — this used to
    # raise KeyError inside the final join.
    client._handle_packet(build_packet(3, 3, 5, b'\x00'))
    client._handle_packet(build_packet(3, 3, 6, b'\x00'))
    client._handle_packet(build_packet(3, 3, 7, b'\x00'))  # must not raise

    # The client must still be alive and able to process further packets.
    client._handle_packet(build_packet(4, 1, 1, bytes([0x11])))
    assert any("hw:" in s for s in statuses)


def test_prune_partial_frames_drops_stale_entry_past_timeout():
    client = LiveClient("127.0.0.1", 0)
    packet_type = pc.PACKET_TYPE_3101
    stale_key = (packet_type, 9)
    client._partial_frames[stale_key] = {
        'total': 3, 'parts': {1: b'A'},
        'first_seen': time.time() - (pc.MULTI_PACKET_FRAME_TIMEOUT_S + 1.0),
    }
    # Any reassemble call prunes stale entries first.
    client._reassemble(_make_multi_header(99, 3, 1, packet_type), packet_type, b'X')
    assert stale_key not in client._partial_frames
