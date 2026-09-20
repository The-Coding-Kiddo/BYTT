"""Replays a .bsf file through the same ping_received/status_changed signal
interface LiveClient exposes, so GUI code cannot tell live data from
playback apart. Adds pause/resume/step/seek on top of that shared
interface -- PlaybackSource-only, since a live feed has no "past" to
step back into."""
import bisect
import threading
from pathlib import Path
from PySide6.QtCore import QObject, Signal
from bytt.bsf.file_io import load_all_pings, read_file_header
from bytt.protocol.packets import extract_raw_channels, get_ping_meta, extract_nav_fix
from bytt.protocol import constants as pc


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
        self._sound_speed_mps = None  # from the file header; needed to compute real ping range
        self._nav_fixes = []  # [(ping_index_before_it, {'lat', 'lon', 'heading', 'height'}), ...]
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

    def set_speed(self, interval_s: float) -> None:
        self._interval_s = max(0.0, interval_s)

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

    def _nav_fix_for(self, ping_index):
        if not self._nav_fixes:
            return None
        keys = [t[0] for t in self._nav_fixes]
        # nav_records store the index of the last ping emitted *before* the
        # nav record was written, so a fix only applies to pings strictly
        # after that index -- bisect_left (not _right) encodes that.
        idx = bisect.bisect_left(keys, ping_index)
        if idx == 0:
            return None
        return self._nav_fixes[idx - 1][1]

    def _emit_current(self):
        offset, size = self._pings[self._current_index]
        ping = self._data[offset:offset + size]
        port_raw, stbd_raw = extract_raw_channels(ping)
        meta = get_ping_meta(ping, sound_speed_mps=self._sound_speed_mps)
        meta['nav_fix'] = self._nav_fix_for(self._current_index)
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
            self._sound_speed_mps = read_file_header(self._data).get('sound_speed')
            self._pings, nav_records = load_all_pings(self._data)
        except Exception as e:
            self.status_changed.emit(f'playback error: {e}')
            return
        self._nav_fixes = []
        for offset, ping_idx_before in nav_records:
            fix = extract_nav_fix(self._data[offset:offset + pc.NAV_RECORD_SIZE])
            if fix is not None:
                lat, lon, _ts = fix
                self._nav_fixes.append(
                    (ping_idx_before, {'lat': lat, 'lon': lon, 'heading': None, 'height': None}))
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
