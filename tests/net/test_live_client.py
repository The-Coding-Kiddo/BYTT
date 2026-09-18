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
