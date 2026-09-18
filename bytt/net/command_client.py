"""Outbound TCP client for the towfish/SatCenter command port (16128).
Sends type-107 SS sonar work control commands built by
bytt.protocol.commands.build_command_107."""
import socket
from PySide6.QtCore import QObject, Signal
from bytt.protocol.commands import build_command_107, SonarWorkCommand


class CommandClient(QObject):
    status_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sock = None
        self._identification = 0

    def connect_to(self, host: str, port: int, timeout: float = 3.0) -> None:
        self.status_changed.emit('connecting')
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self.status_changed.emit('connected')

    def send_command(self, cmd: SonarWorkCommand) -> None:
        if self._sock is None:
            raise RuntimeError("CommandClient.send_command called before connect_to()")
        packet = build_command_107(cmd, identification=self._identification)
        self._identification = (self._identification + 1) % 65536
        self._sock.sendall(packet)

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
            self.status_changed.emit('disconnected')
