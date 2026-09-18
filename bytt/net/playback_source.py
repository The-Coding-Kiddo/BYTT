"""Replays a .bsf file through the same ping_received/status_changed signal
interface LiveClient exposes, so GUI code cannot tell live data from
playback apart. Adds pause/resume/step/seek on top of that shared
interface -- PlaybackSource-only, since a live feed has no "past" to
step back into."""
import threading
from pathlib import Path
from PySide6.QtCore import QObject, Signal
from bytt.bsf.file_io import load_all_pings
from bytt.protocol.packets import extract_raw_channels, get_ping_meta


class PlaybackSource(QObject):
    ping_received = Signal(object, object, dict)
    status_changed = Signal(str)
    position_changed = Signal(int, int)  # current_index, total_pings

    def __init__(self, bsf_path: str, pings_per_second: float = 16.0, parent=None):
        super().__init__(parent)
        self.bsf_path = bsf_path
        self._interval_s = 1.0 / pings_per_second
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread = None
        self._data = None
        self._pings = []
        self._current_index = -1
        self._pending_seek = None
        self._seek_lock = threading.Lock()

    def start(self):
        self._stop.clear()
        self._paused.clear()
        self._current_index = -1
        self._pending_seek = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._paused.clear()  # wake the pause-wait loop so it can see _stop and exit
        if self._thread:
            self._thread.join(timeout=2.0)

    def pause(self):
        self._paused.set()

    def resume(self):
        self._paused.clear()

    def step_forward(self) -> None:
        if not self._pings:
            return
        self.pause()
        with self._seek_lock:
            current = self._current_index
            if current >= len(self._pings) - 1:
                return
            target = current + 1
            self._pending_seek = target

    def step_backward(self) -> None:
        if not self._pings:
            return
        self.pause()
        with self._seek_lock:
            current = self._current_index
            if current <= 0:
                return
            target = current - 1
            self._pending_seek = target

    def seek(self, ping_index: int) -> None:
        if not self._pings:
            return
        target = max(0, min(ping_index, len(self._pings) - 1))
        with self._seek_lock:
            self._pending_seek = target

    def _consume_pending_seek(self):
        with self._seek_lock:
            v = self._pending_seek
            self._pending_seek = None
            return v

    def _emit_current(self):
        offset, size = self._pings[self._current_index]
        ping = self._data[offset:offset + size]
        port_raw, stbd_raw = extract_raw_channels(ping)
        meta = get_ping_meta(ping)
        self.ping_received.emit(port_raw, stbd_raw, meta)
        self.position_changed.emit(self._current_index, len(self._pings))

    def _wait_while_paused(self) -> bool:
        """Blocks while paused. Returns True if a seek/step arrived and was
        emitted (caller should re-check pause state), False if stop was
        requested while waiting."""
        while self._paused.is_set() and not self._stop.is_set():
            pending = self._consume_pending_seek()
            if pending is not None:
                with self._seek_lock:
                    self._current_index = pending
                self._emit_current()
                return True
            self._stop.wait(0.05)
        return False

    def _run(self):
        path = Path(self.bsf_path)
        if not path.exists():
            self.status_changed.emit(f'playback error: file not found: {path}')
            return
        self.status_changed.emit('loading')
        try:
            self._data = path.read_bytes()
            self._pings, _nav_records = load_all_pings(self._data)
        except Exception as e:
            self.status_changed.emit(f'playback error: {e}')
            return
        if not self._pings:
            self.status_changed.emit('playback error: no ping records found')
            return
        self.status_changed.emit(f'connected — playback ({len(self._pings)} pings)')
        finished_emitted = False
        while not self._stop.is_set():
            if self._paused.is_set():
                self._wait_while_paused()
                continue
            pending = self._consume_pending_seek()
            if pending is not None:
                with self._seek_lock:
                    self._current_index = pending
            else:
                with self._seek_lock:
                    self._current_index += 1
            if self._current_index >= len(self._pings):
                with self._seek_lock:
                    self._current_index = len(self._pings) - 1
                self._paused.set()
                if not finished_emitted:
                    self.status_changed.emit('playback finished')
                    finished_emitted = True
                continue
            self._emit_current()
            self._stop.wait(self._interval_s)
