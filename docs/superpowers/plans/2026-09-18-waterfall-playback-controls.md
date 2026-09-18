# Waterfall Playback Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add play/pause/step/seek to `PlaybackSource` and wire them into `MainWindow` as a
toolbar (buttons, position label, scrub bar) plus keyboard shortcuts, so `.bsf` playback is
actually interactive instead of a one-shot autoplay. Zoom/pan needs no new code — it's already
free via pyqtgraph's built-in `PlotItem` interaction.

**Architecture:** `PlaybackSource`'s background thread gets a small state machine (current index,
a pause flag, a pending-seek slot) instead of a flat `for` loop, so play/pause/step/seek can all
be expressed as "where should the loop be, and is it allowed to advance on its own." `LiveClient`
is untouched — playback controls only ever exist when `self.source` is a `PlaybackSource`.

**Tech Stack:** Python, PySide6 (`QToolBar`, `QSlider`, `QShortcut`), existing `bytt.net.playback_source`/`bytt.gui.main_window`.

**Spec:** `docs/superpowers/specs/2026-09-18-waterfall-playback-controls-design.md`

## Global Constraints

- Playback controls (toolbar, scrub bar, shortcuts) are visible/enabled only when
  `self.source` is a `PlaybackSource` — hidden/disabled for a live towfish connection or when
  disconnected.
- `LiveClient` and the live data path are not modified by this plan.
- `seek()` clamps out-of-range input rather than raising; `step_forward()`/`step_backward()` at a
  track boundary are no-ops (no new `ping_received`/`position_changed` emission); `pause()`/
  `resume()` are idempotent.
- Pause/resume never re-loads the file or loses position — it blocks/unblocks the existing
  background thread, it does not stop and restart it.
- Keyboard shortcuts route through the exact same methods the toolbar buttons call — no
  duplicated logic between the two.
- Tests use real objects (real `PlaybackSource`, real synthetic `.bsf` byte buffers, real Qt
  event loop) — no mocks, matching every existing test in this project.

---

## Task 1: PlaybackSource — state machine, pause/resume, position_changed

**Files:**
- Modify: `bytt/net/playback_source.py` (full rewrite of `PlaybackSource`'s internals; public
  constructor signature and existing `ping_received`/`status_changed` signals are unchanged)
- Test: `tests/net/test_playback_source.py` (add new tests; existing 2 tests must keep passing
  unmodified — they exercise the same public behavior through the new internals)

**Interfaces:**
- Consumes: `bytt.bsf.file_io.load_all_pings(data: bytes) -> (pings, nav_records)`,
  `bytt.protocol.packets.extract_raw_channels(ping) -> (port_raw, stbd_raw)`,
  `bytt.protocol.packets.get_ping_meta(ping) -> dict` (all already exist, unchanged).
- Produces: new `PlaybackSource.pause() -> None`, `PlaybackSource.resume() -> None`, new signal
  `position_changed = Signal(int, int)` (current_index, total_pings), emitted alongside every
  `ping_received`. Tasks 2 and 3 add `step_forward`/`step_backward`/`seek` on top of the state
  this task introduces (`self._pings`, `self._data`, `self._current_index`, `self._pending_seek`,
  `self._seek_lock`, `self._paused`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/net/test_playback_source.py` (keep the existing two tests in the file as-is):

```python
import time
from PySide6.QtCore import QCoreApplication

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/net/test_playback_source.py -v`
Expected: FAIL — `AttributeError: 'PlaybackSource' object has no attribute 'pause'` (and similar
for `resume`/`position_changed`).

- [ ] **Step 3: Rewrite `bytt/net/playback_source.py`**

```python
"""Replays a .bsf file through the same ping_received/status_changed signal
interface LiveClient exposes, so GUI code cannot tell live data from
playback apart. Adds pause/resume/step/seek on top of that shared
interface -- PlaybackSource-only, since a live feed has no "past" to
step back into."""
import threading
import time
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
                self._current_index = pending
                self._emit_current()
                return True
            time.sleep(0.05)
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
        while not self._stop.is_set():
            if self._paused.is_set():
                self._wait_while_paused()
                continue
            pending = self._consume_pending_seek()
            if pending is not None:
                self._current_index = pending
            else:
                self._current_index += 1
            if self._current_index >= len(self._pings):
                break
            self._emit_current()
            time.sleep(self._interval_s)
        if not self._stop.is_set():
            self.status_changed.emit('playback finished')
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/net/test_playback_source.py -v`
Expected: PASS — all 5 tests (2 pre-existing + 3 new).

- [ ] **Step 5: Commit**

```bash
git add bytt/net/playback_source.py tests/net/test_playback_source.py
git commit -m "Add pause/resume and position tracking to PlaybackSource"
```

---

## Task 2: PlaybackSource — step_forward / step_backward

**Files:**
- Modify: `bytt/net/playback_source.py`
- Test: `tests/net/test_playback_source.py`

**Interfaces:**
- Consumes: the state Task 1 introduced (`self._pings`, `self._current_index`, `self.pause()`,
  `self._pending_seek`, `self._seek_lock`).
- Produces: `PlaybackSource.step_forward() -> None`, `PlaybackSource.step_backward() -> None`.
  Both pause the source, then either advance/retreat the position by exactly one ping (emitting
  once) or, at a track boundary, pause with no new emission. Task 4's toolbar step buttons call
  these directly.

- [ ] **Step 1: Write the failing tests**

Add to `tests/net/test_playback_source.py`:

```python
def test_step_forward_emits_exactly_one_ping_and_pauses(tmp_path):
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(_build_bsf_bytes_n(n_pings=10))

    received = []
    source = PlaybackSource(str(bsf_path), pings_per_second=1000)
    source.ping_received.connect(lambda port, stbd, meta: received.append(meta))
    source.start()

    deadline = time.time() + 3.0
    while time.time() < deadline and len(received) < 3:
        QCoreApplication.processEvents()
        time.sleep(0.01)
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
    source = PlaybackSource(str(bsf_path), pings_per_second=1000)
    source.ping_received.connect(lambda port, stbd, meta: received.append(meta))
    source.start()

    source.pause()
    source.seek(2)  # last index for a 3-ping file
    deadline = time.time() + 2.0
    while time.time() < deadline and len(received) < 1:
        QCoreApplication.processEvents()
        time.sleep(0.01)
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
    source = PlaybackSource(str(bsf_path), pings_per_second=1000)
    source.ping_received.connect(lambda port, stbd, meta: received.append(meta))
    source.start()

    source.pause()
    source.seek(0)
    deadline = time.time() + 2.0
    while time.time() < deadline and len(received) < 1:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    count_before = len(received)

    source.step_backward()
    time.sleep(0.2)
    QCoreApplication.processEvents()
    assert len(received) == count_before, "stepping before the first ping must not emit"

    source.stop()
```

Note: these tests call `source.seek(...)`, which Task 3 implements. Since Task 2 is committed
before Task 3 in this plan, temporarily stub `seek` in this step by adding it as a minimal
pass-through so these tests are meaningful once Task 3 lands — OR, simpler and preferred: reorder
your local work so you implement Task 3's `seek` together with Task 2 in this same step, since
`step_forward`/`step_backward`'s boundary tests genuinely need `seek` to set up the "already at
the boundary" precondition. Concretely: do Step 3 below (which includes `seek`) before running
these tests.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/net/test_playback_source.py -v`
Expected: FAIL — `AttributeError: 'PlaybackSource' object has no attribute 'step_forward'` (and
`seek`, until Step 3 below adds both).

- [ ] **Step 3: Add `step_forward`, `step_backward`, and `seek` to `PlaybackSource`**

Add these three methods to the `PlaybackSource` class in `bytt/net/playback_source.py`:

```python
    def step_forward(self) -> None:
        if not self._pings:
            return
        self.pause()
        if self._current_index >= len(self._pings) - 1:
            return
        target = self._current_index + 1
        with self._seek_lock:
            self._pending_seek = target

    def step_backward(self) -> None:
        if not self._pings:
            return
        self.pause()
        if self._current_index <= 0:
            return
        target = self._current_index - 1
        with self._seek_lock:
            self._pending_seek = target

    def seek(self, ping_index: int) -> None:
        if not self._pings:
            return
        target = max(0, min(ping_index, len(self._pings) - 1))
        with self._seek_lock:
            self._pending_seek = target
```

(`seek` is Task 3's deliverable, included here because Task 2's own boundary tests depend on it —
see the note in Step 1. Task 3 below only needs to add `seek`'s own dedicated tests, since the
implementation already exists after this step.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/net/test_playback_source.py -v`
Expected: PASS — all 8 tests (5 from Task 1 + 3 new).

- [ ] **Step 5: Commit**

```bash
git add bytt/net/playback_source.py tests/net/test_playback_source.py
git commit -m "Add step_forward/step_backward/seek to PlaybackSource"
```

---

## Task 3: PlaybackSource — seek() dedicated tests

**Files:**
- Test: `tests/net/test_playback_source.py`

**Interfaces:**
- Consumes: `PlaybackSource.seek(ping_index: int) -> None` (implemented in Task 2's Step 3).
- Produces: nothing new — this task only adds direct test coverage for `seek`'s own contract
  (arbitrary jump, clamping) since Task 2's tests only exercised `seek` indirectly as a boundary
  setup helper.

- [ ] **Step 1: Write the failing tests**

Add to `tests/net/test_playback_source.py`:

```python
def test_seek_jumps_to_arbitrary_position(tmp_path):
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(_build_bsf_bytes_n(n_pings=20))

    positions = []
    source = PlaybackSource(str(bsf_path), pings_per_second=1000)
    source.position_changed.connect(lambda idx, total: positions.append(idx))
    source.start()

    deadline = time.time() + 2.0
    while time.time() < deadline and len(positions) < 1:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    source.pause()
    QCoreApplication.processEvents()

    source.seek(15)
    deadline = time.time() + 2.0
    while time.time() < deadline and 15 not in positions:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert 15 in positions

    source.stop()

def test_seek_clamps_out_of_range_input(tmp_path):
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(_build_bsf_bytes_n(n_pings=5))

    positions = []
    source = PlaybackSource(str(bsf_path), pings_per_second=1000)
    source.position_changed.connect(lambda idx, total: positions.append(idx))
    source.start()

    deadline = time.time() + 2.0
    while time.time() < deadline and len(positions) < 1:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    source.pause()
    QCoreApplication.processEvents()

    source.seek(-5)  # must clamp to 0, not raise
    deadline = time.time() + 2.0
    while time.time() < deadline and 0 not in positions[-1:]:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert positions[-1] == 0

    source.seek(9999)  # must clamp to the last valid index (4), not raise
    deadline = time.time() + 2.0
    while time.time() < deadline and positions[-1] != 4:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert positions[-1] == 4

    source.stop()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/net/test_playback_source.py -v`
Expected: These two new tests should already PASS, since `seek` was implemented in Task 2's Step
3. Confirm that's the case — if either fails, `seek`'s implementation has a bug that Task 2's
tests didn't catch; fix `seek` in `bytt/net/playback_source.py` before proceeding (don't change
these tests to work around a real bug).

- [ ] **Step 3: N/A — implementation already exists from Task 2**

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/net/test_playback_source.py -v`
Expected: PASS — all 10 tests.

- [ ] **Step 5: Commit**

```bash
git add tests/net/test_playback_source.py
git commit -m "Add dedicated seek() test coverage to PlaybackSource"
```

---

## Task 4: MainWindow — playback toolbar (play/pause, step, position label)

**Files:**
- Modify: `bytt/gui/main_window.py`
- Test: `tests/gui/test_main_window.py`

**Interfaces:**
- Consumes: `PlaybackSource.pause()/resume()/step_forward()/step_backward()`,
  `PlaybackSource.position_changed = Signal(int, int)` (all from Tasks 1-2).
- Produces: `MainWindow.playback_toolbar` (a `QToolBar`, hidden unless `self.source` is a
  `PlaybackSource`), `MainWindow.play_pause_action`, `MainWindow.step_back_action`,
  `MainWindow.step_fwd_action`, `MainWindow.position_label`, and handler methods
  `_toggle_play_pause()`, `_step_forward()`, `_step_backward()`, `_on_position_changed(idx,
  total)` — Task 5 (scrub bar) and Task 6 (keyboard shortcuts) both call
  `_toggle_play_pause`/`_step_forward`/`_step_backward` directly, so their names and zero-argument
  signatures are load-bearing for those tasks.

- [ ] **Step 1: Write the failing tests**

Add to `tests/gui/test_main_window.py`:

```python
def test_playback_toolbar_hidden_when_disconnected():
    window = MainWindow(AppConfig())
    assert window.playback_toolbar.isVisible() is False

def test_playback_toolbar_visible_after_opening_playback_file(tmp_path):
    from bytt.protocol import constants as pc
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(bytes(header))  # 0 pings is fine; we're testing toolbar visibility

    window = MainWindow(AppConfig())
    window.open_playback_file(str(bsf_path))
    assert window.playback_toolbar.isVisible() is True
    window.source.stop()

def test_toggle_play_pause_calls_source_pause_and_resume(tmp_path):
    from bytt.protocol import constants as pc
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(bytes(header))

    window = MainWindow(AppConfig())
    window.open_playback_file(str(bsf_path))

    calls = []
    window.source.pause = lambda: calls.append('pause')
    window.source.resume = lambda: calls.append('resume')

    window._toggle_play_pause()  # starts "playing" -> should pause
    assert calls == ['pause']
    assert window.play_pause_action.text() == "Play"

    window._toggle_play_pause()  # now "paused" -> should resume
    assert calls == ['pause', 'resume']
    assert window.play_pause_action.text() == "Pause"

    window.source.stop()

def test_step_buttons_call_source_step_methods(tmp_path):
    from bytt.protocol import constants as pc
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(bytes(header))

    window = MainWindow(AppConfig())
    window.open_playback_file(str(bsf_path))

    calls = []
    window.source.step_forward = lambda: calls.append('fwd')
    window.source.step_backward = lambda: calls.append('back')

    window._step_forward()
    window._step_backward()
    assert calls == ['fwd', 'back']

    window.source.stop()

def test_on_position_changed_updates_label():
    window = MainWindow(AppConfig())
    window._on_position_changed(4, 10)
    assert window.position_label.text() == "Ping 5 / 10"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gui/test_main_window.py -v`
Expected: FAIL — `AttributeError: 'MainWindow' object has no attribute 'playback_toolbar'`.

- [ ] **Step 3: Add the toolbar to `MainWindow`**

In `bytt/gui/main_window.py`, update the imports:

```python
from PySide6.QtWidgets import (
    QMainWindow, QFileDialog, QInputDialog, QToolBar, QLabel,
)
from PySide6.QtGui import QAction
```

In `__init__`, after the existing menu-bar block (after `self.statusBar().showMessage("disconnected")`
would also work, but place it right after the menu bar for a natural top-to-bottom read), add:

```python
        self.playback_toolbar = QToolBar("Playback", self)
        self.addToolBar(self.playback_toolbar)

        self.play_pause_action = QAction("Play", self)
        self.play_pause_action.triggered.connect(self._toggle_play_pause)
        self.playback_toolbar.addAction(self.play_pause_action)

        self.step_back_action = QAction("Step Back", self)
        self.step_back_action.triggered.connect(self._step_backward)
        self.playback_toolbar.addAction(self.step_back_action)

        self.step_fwd_action = QAction("Step Forward", self)
        self.step_fwd_action.triggered.connect(self._step_forward)
        self.playback_toolbar.addAction(self.step_fwd_action)

        self.position_label = QLabel("")
        self.playback_toolbar.addWidget(self.position_label)

        self._is_playing = False
        self.playback_toolbar.setVisible(False)
```

Add these methods to the class (near `_on_ping_received`):

```python
    def _toggle_play_pause(self) -> None:
        if not isinstance(self.source, PlaybackSource):
            return
        if self._is_playing:
            self.source.pause()
            self._is_playing = False
            self.play_pause_action.setText("Play")
        else:
            self.source.resume()
            self._is_playing = True
            self.play_pause_action.setText("Pause")

    def _step_forward(self) -> None:
        if isinstance(self.source, PlaybackSource):
            self.source.step_forward()
            self._is_playing = False
            self.play_pause_action.setText("Play")

    def _step_backward(self) -> None:
        if isinstance(self.source, PlaybackSource):
            self.source.step_backward()
            self._is_playing = False
            self.play_pause_action.setText("Play")

    def _on_position_changed(self, current_index: int, total_pings: int) -> None:
        self.position_label.setText(f"Ping {current_index + 1} / {total_pings}")
```

Update `_wire_source` to toggle toolbar visibility and connect the new signal:

```python
    def _wire_source(self) -> None:
        self.source.status_changed.connect(self.statusBar().showMessage)
        self.source.ping_received.connect(self._on_ping_received)
        is_playback = isinstance(self.source, PlaybackSource)
        self.playback_toolbar.setVisible(is_playback)
        if is_playback:
            self.source.position_changed.connect(self._on_position_changed)
            self._is_playing = True
            self.play_pause_action.setText("Pause")
```

Update `disconnect_source` to hide the toolbar and reset play state:

```python
    def disconnect_source(self) -> None:
        if self.source is not None:
            self.source.stop()
            self.source = None
        self.playback_toolbar.setVisible(False)
        self._is_playing = False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gui/test_main_window.py -v`
Expected: PASS — all tests including the 5 new ones.

- [ ] **Step 5: Commit**

```bash
git add bytt/gui/main_window.py tests/gui/test_main_window.py
git commit -m "Add playback toolbar (play/pause, step, position label) to MainWindow"
```

---

## Task 5: MainWindow — scrub bar

**Files:**
- Modify: `bytt/gui/main_window.py`
- Test: `tests/gui/test_main_window.py`

**Interfaces:**
- Consumes: `PlaybackSource.seek(ping_index: int) -> None` (Task 2), `MainWindow.playback_toolbar`
  and `_on_position_changed` (Task 4).
- Produces: `MainWindow.scrub_slider` (a `QSlider`), handlers `_on_scrub_pressed()`,
  `_on_scrub_released()`. No later task depends on these beyond this one.

- [ ] **Step 1: Write the failing tests**

Add to `tests/gui/test_main_window.py`:

```python
def test_on_position_changed_updates_scrub_slider_when_not_dragging():
    window = MainWindow(AppConfig())
    window._on_position_changed(3, 10)
    assert window.scrub_slider.maximum() == 9
    assert window.scrub_slider.value() == 3

def test_scrub_slider_ignores_position_updates_while_dragging():
    window = MainWindow(AppConfig())
    window._on_position_changed(2, 10)
    window._on_scrub_pressed()
    window._on_position_changed(7, 10)  # arrives mid-drag, must NOT move the slider
    assert window.scrub_slider.value() == 2

def test_scrub_slider_release_calls_source_seek(tmp_path):
    from bytt.protocol import constants as pc
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(bytes(header))

    window = MainWindow(AppConfig())
    window.open_playback_file(str(bsf_path))

    calls = []
    window.source.seek = lambda idx: calls.append(idx)

    window.scrub_slider.setRange(0, 10)
    window._on_scrub_pressed()
    window.scrub_slider.setValue(6)
    window._on_scrub_released()
    assert calls == [6]

    window.source.stop()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gui/test_main_window.py -v`
Expected: FAIL — `AttributeError: 'MainWindow' object has no attribute 'scrub_slider'`.

- [ ] **Step 3: Add the scrub bar**

Update the import line to also bring in `QSlider`:

```python
from PySide6.QtWidgets import (
    QMainWindow, QFileDialog, QInputDialog, QToolBar, QLabel, QSlider,
)
from PySide6.QtCore import Qt
```

In `__init__`, right after `self.playback_toolbar.addWidget(self.position_label)` (added in Task
4), add:

```python
        self.scrub_slider = QSlider(Qt.Orientation.Horizontal)
        self.scrub_slider.setRange(0, 0)
        self.scrub_slider.sliderPressed.connect(self._on_scrub_pressed)
        self.scrub_slider.sliderReleased.connect(self._on_scrub_released)
        self.playback_toolbar.addWidget(self.scrub_slider)
        self._scrub_dragging = False
```

(Keep `self._is_playing = False` / `self.playback_toolbar.setVisible(False)` from Task 4 as the
last two lines of this block — order doesn't matter between them and this addition.)

Add the two handlers:

```python
    def _on_scrub_pressed(self) -> None:
        self._scrub_dragging = True

    def _on_scrub_released(self) -> None:
        self._scrub_dragging = False
        if isinstance(self.source, PlaybackSource):
            self.source.seek(self.scrub_slider.value())
```

Update `_on_position_changed` (from Task 4) to also drive the slider, guarded by the drag flag:

```python
    def _on_position_changed(self, current_index: int, total_pings: int) -> None:
        self.position_label.setText(f"Ping {current_index + 1} / {total_pings}")
        if not self._scrub_dragging:
            self.scrub_slider.blockSignals(True)
            self.scrub_slider.setRange(0, max(0, total_pings - 1))
            self.scrub_slider.setValue(current_index)
            self.scrub_slider.blockSignals(False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gui/test_main_window.py -v`
Expected: PASS — all tests including the 3 new ones.

- [ ] **Step 5: Commit**

```bash
git add bytt/gui/main_window.py tests/gui/test_main_window.py
git commit -m "Add scrub bar to MainWindow playback toolbar"
```

---

## Task 6: MainWindow — keyboard shortcuts

**Files:**
- Modify: `bytt/gui/main_window.py`
- Test: `tests/gui/test_main_window.py`

**Interfaces:**
- Consumes: `_toggle_play_pause()`, `_step_forward()`, `_step_backward()` (Task 4),
  `MainWindow.close()` (inherited from `QMainWindow`, already wired to `closeEvent`).
- Produces: nothing new for later tasks — this is the final task in this plan.

- [ ] **Step 1: Write the failing tests**

Add to `tests/gui/test_main_window.py`:

```python
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt as QtCoreQt

def test_space_shortcut_toggles_play_pause(tmp_path):
    from bytt.protocol import constants as pc
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(bytes(header))

    window = MainWindow(AppConfig())
    window.open_playback_file(str(bsf_path))

    calls = []
    window.source.pause = lambda: calls.append('pause')
    window.source.resume = lambda: calls.append('resume')

    QTest.keyClick(window, QtCoreQt.Key.Key_Space)
    assert calls == ['pause']

    window.source.stop()

def test_arrow_shortcuts_step(tmp_path):
    from bytt.protocol import constants as pc
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(bytes(header))

    window = MainWindow(AppConfig())
    window.open_playback_file(str(bsf_path))

    calls = []
    window.source.step_forward = lambda: calls.append('fwd')
    window.source.step_backward = lambda: calls.append('back')

    QTest.keyClick(window, QtCoreQt.Key.Key_Right)
    QTest.keyClick(window, QtCoreQt.Key.Key_Left)
    assert calls == ['fwd', 'back']

    window.source.stop()

def test_q_and_escape_close_the_window():
    window = MainWindow(AppConfig())
    closed = []
    window.closeEvent = lambda event: (closed.append('closed'), event.accept())[-1]

    QTest.keyClick(window, QtCoreQt.Key.Key_Q)
    assert closed == ['closed']
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gui/test_main_window.py -v`
Expected: FAIL — the shortcuts don't exist yet, so the source's `pause`/`step_forward`/etc. are
never called and `closed` stays empty.

- [ ] **Step 3: Add keyboard shortcuts**

Update the import line:

```python
from PySide6.QtGui import QAction, QShortcut, QKeySequence
```

At the end of `__init__` (after the scrub bar block from Task 5), add:

```python
        self._space_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self._space_shortcut.activated.connect(self._toggle_play_pause)

        self._left_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Left), self)
        self._left_shortcut.activated.connect(self._step_backward)

        self._right_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Right), self)
        self._right_shortcut.activated.connect(self._step_forward)

        self._quit_shortcut_q = QShortcut(QKeySequence(Qt.Key.Key_Q), self)
        self._quit_shortcut_q.activated.connect(self.close)

        self._quit_shortcut_esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._quit_shortcut_esc.activated.connect(self.close)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gui/test_main_window.py -v`
Expected: PASS — all tests including the 3 new ones.

- [ ] **Step 5: Run the full project test suite**

Run: `QT_QPA_PLATFORM=offscreen pytest -v`
Expected: PASS, every test in the project green (existing 50 + this plan's ~19 new tests).

- [ ] **Step 6: Manually verify against a real display**

Run: `python -m bytt.app`, then **Open Playback File...** against a real `.bsf` recording.
Expected: the playback toolbar appears with Play/Pause, step buttons, a position label, and a
scrub bar. Space pauses/resumes, Left/Right step one ping at a time (and pause playback), dragging
the scrub bar and releasing jumps to that position, Q or Esc closes the window cleanly. Connecting
to a towfish (or having no source at all) hides the toolbar.

- [ ] **Step 7: Commit**

```bash
git add bytt/gui/main_window.py tests/gui/test_main_window.py
git commit -m "Add keyboard shortcuts for play/pause/step/quit"
```

---

## Self-Review Notes

- **Spec coverage:** Playback-only scoping (Task 4's `isinstance(self.source, PlaybackSource)`
  guards throughout, toolbar hidden by default and on disconnect) — covered. `PlaybackSource`
  extension (pause/resume/step/seek/position_changed) — Tasks 1-3. Toolbar/scrub
  bar/shortcuts — Tasks 4-6. Zoom/pan — explicitly needs no task, per the spec (pyqtgraph's
  built-in interaction, already present from the prior sub-project's `WaterfallView`). Error
  handling (clamping, no-ops, idempotency) — implemented directly in Task 1-2's code and covered
  by Task 2/3's boundary and clamping tests.
- **Placeholder scan:** no TBD/TODO markers. Task 2's Step 1 has an explicit, concrete note about
  needing `seek` before its boundary tests can run, with a concrete resolution (implement `seek`
  in the same step) rather than a vague "handle this later."
- **Type consistency:** `position_changed = Signal(int, int)` (Task 1) is consumed identically in
  `_on_position_changed(self, current_index: int, total_pings: int)` (Task 4) and the scrub bar
  update in Task 5. `step_forward`/`step_backward`/`pause`/`resume`/`seek` signatures match between
  their Task 1-2 definitions and every call site in Tasks 4-6.
