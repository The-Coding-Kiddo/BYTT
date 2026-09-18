import socket
import struct
import threading
import pytest
from bytt.protocol.commands import SonarWorkCommand
from bytt.net.command_client import CommandClient

def test_send_command_writes_256_bytes_to_socket():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    received = {}

    def accept_one():
        conn, _ = srv.accept()
        received['data'] = conn.recv(4096)
        conn.close()

    t = threading.Thread(target=accept_one, daemon=True)
    t.start()

    client = CommandClient()
    client.connect_to("127.0.0.1", port)
    client.send_command(SonarWorkCommand(sonar_run=1))
    t.join(timeout=2.0)
    client.close()
    srv.close()

    assert len(received['data']) == 256

def test_send_command_without_connect_raises():
    client = CommandClient()
    with pytest.raises(RuntimeError):
        client.send_command(SonarWorkCommand())
