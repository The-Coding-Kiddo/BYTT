"""Records the raw wire stream to disk. Recording the bytes LiveClient
already validates (checksum-ok, framed) is enough to reconstruct a session
later via the same .bsf-style playback path -- no vendor recording feature
needed, we already receive everything there is to record."""
from pathlib import Path
from datetime import datetime


class Recorder:
    def __init__(self, directory: str = "recordings"):
        self.directory = Path(directory)
        self._file = None

    @property
    def is_recording(self) -> bool:
        return self._file is not None

    def start(self) -> str:
        """Opens a new timestamped file for writing. Returns its path."""
        self.directory.mkdir(parents=True, exist_ok=True)
        name = datetime.now().strftime("%Y%m%d_%H%M%S") + ".raw"
        path = self.directory / name
        self._file = open(path, "wb")
        return str(path)

    def stop(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def write_packet(self, packet: bytes) -> None:
        if self._file is not None:
            self._file.write(packet)
