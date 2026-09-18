"""Replays a .bsf file through the same ping_received/status_changed signal
interface LiveClient exposes, so GUI code cannot tell live data from
playback apart."""
import threading
import time
from pathlib import Path
from PySide6.QtCore import QObject, Signal
from bytt.bsf.file_io import load_all_pings
from bytt.protocol.constants import SAMPLE_OFFSET
from bytt.protocol.packets import extract_raw_channels, get_ping_meta


class PlaybackSource(QObject):
    ping_received = Signal(object, object, dict)
    status_changed = Signal(str)

    def __init__(self, bsf_path: str, pings_per_second: float = 16.0, parent=None):
        super().__init__(parent)
        self.bsf_path = bsf_path
        self._interval_s = 1.0 / pings_per_second
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self):
        path = Path(self.bsf_path)
        if not path.exists():
            self.status_changed.emit(f'playback error: file not found: {path}')
            return
        self.status_changed.emit('loading')
        try:
            data = path.read_bytes()
            pings, _nav_records = load_all_pings(data)
        except Exception as e:
            self.status_changed.emit(f'playback error: {e}')
            return
        if not pings:
            self.status_changed.emit('playback error: no ping records found')
            return
        self.status_changed.emit(f'connected — playback ({len(pings)} pings)')
        for offset, size in pings:
            if self._stop.is_set():
                break
            ping = data[offset:offset + size]
            port_raw, stbd_raw = extract_raw_channels(ping)
            meta = get_ping_meta(ping)
            self.ping_received.emit(port_raw, stbd_raw, meta)
            time.sleep(self._interval_s)
        if not self._stop.is_set():
            self.status_changed.emit('playback finished')
