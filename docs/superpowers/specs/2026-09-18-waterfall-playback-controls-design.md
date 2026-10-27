---
title: Waterfall interaction & playback controls — design
status: draft — approved by user 2026-09-18
---

## Why this document exists

The architecture skeleton (see `2026-09-18-python-rewrite-architecture-design.md` and its
implementation) proved the full pipeline works end-to-end: connect to a towfish or open a `.bsf`
file, and pings flow through parsing → enhancement → the pyqtgraph waterfall. But running it
surfaced the obvious gap: none of the original pygame tool's interactive controls exist yet. You
can open a file and watch it play through once, with no way to pause, step, scrub, or zoom.

This is sub-project 2 of the full HydroWaterDemo replacement (per the architecture spec's
decomposition): **waterfall interaction & playback controls.** The other remaining sub-projects
(enhancement controls panel, GPS/nav panel, sonar hardware control panel, keybindings/help panel)
are out of scope here and get their own specs.

## Scope decision: playback-only controls

Play/pause, step, and the scrub bar only make sense against a `.bsf` file being replayed — a live
towfish feed has no "past" to step back into or "future" to scrub forward to; it just streams
continuously. So these controls are **enabled only when the active source is a `PlaybackSource`**,
and hidden/disabled when connected live or disconnected. `LiveClient` is untouched by this spec.

Zoom/pan is the one control that applies to both modes (you can zoom into a live-streaming
waterfall too), and it's covered by pyqtgraph's built-in `PlotItem` mouse interaction (wheel zoom,
click-drag pan, right-click reset) — already free with the `WaterfallView` built in the prior
sub-project. No new code needed for zoom.

## PlaybackSource extension

`bytt/net/playback_source.py`'s `PlaybackSource` (built in the architecture sub-project) currently
just streams every ping at a fixed rate on a background thread with no way to pause or seek. It
gains:

- **`pause()` / `resume()`** — implemented via a `threading.Event` the playback loop checks between
  pings (not by stopping the thread). `pause()` sets the event and the loop blocks before emitting
  the next ping; `resume()` clears it and the loop continues from exactly where it left off. Both
  are idempotent — calling either when already in that state is a no-op.
- **`step_forward()` / `step_backward()`** — emits exactly one ping at `current_index ± 1` and
  leaves the source paused (implicitly pausing first if it was playing). No-ops at the last/first
  ping respectively (no wraparound, no error).
- **`seek(ping_index)`** — jumps to an arbitrary position and emits that ping immediately. Clamps
  out-of-range input to `[0, total_pings - 1]` rather than raising — a scrub-bar drag past either
  end of the track shouldn't crash playback.
- **New signal `position_changed(current_index: int, total_pings: int)`** — emitted on every ping
  (from normal playback, step, or seek), driving the scrub bar and a "Ping 340 / 795" label.
  `ping_received` is unchanged and still fires alongside it.

This is a real extension to an already-built, already-tested module — not a rewrite. The existing
`start()`/`stop()`/`ping_received`/`status_changed` interface (shared with `LiveClient`) is
untouched.

## GUI: toolbar, scrub bar, keyboard shortcuts

**Toolbar**, visible/enabled only when `self.source` is a `PlaybackSource` (set on
`open_playback_file()`, hidden on `connect_towfish()` and `disconnect_source()`):

- Play/Pause button — toggles between the two states, icon reflects current state; calls
  `source.pause()` / `source.resume()`.
- Step-back / step-forward buttons — call `source.step_backward()` / `source.step_forward()`.
- Position label ("Ping 340 / 795") — updated from `position_changed`.

**Scrub bar** — a `QSlider` (horizontal), range `0..total_pings`, value driven by
`position_changed`. To avoid fighting a manual drag, the slider only accepts external updates
while the user isn't actively dragging it (tracked via Qt's `sliderPressed`/`sliderReleased`
signals); releasing it calls `source.seek(value)` once.

**Keyboard shortcuts**, via `QShortcut`, matching the original pygame tool's bindings for muscle
memory — and routing through the exact same methods the toolbar buttons call, no duplicate logic:

| Key | Action |
|---|---|
| `Space` | Toggle play/pause |
| `Left` / `Right` | Step backward / forward |
| `Q` / `Esc` | Quit (triggers `closeEvent`, already wired to clean up sources) |

## Error handling

- `seek()` clamps rather than raises on out-of-range input.
- `step_forward()`/`step_backward()` at a track boundary are no-ops.
- `pause()`/`resume()` are idempotent.
- Pause/resume never re-loads or loses position — it's a thread-local block/unblock, not a
  stop/restart.

## Testing approach

Consistent with the rest of the project: real objects, no mocks.

- **`PlaybackSource`**: pause genuinely halts `ping_received` emission (assert no new pings arrive
  for a bounded wait while paused); resume continues from the same position; step_forward/backward
  each emit exactly one ping and update `position_changed` correctly; seek jumps to an arbitrary
  index and clamps out-of-range input; boundary behavior at ping 0 and the last ping.
- **GUI**: toolbar button clicks call the right `PlaybackSource` methods, constructed against a
  real `MainWindow` + real `PlaybackSource` over a synthetic `.bsf` (matching the existing test
  patterns in `tests/gui/test_main_window.py`); scrub bar doesn't accept external updates mid-drag
  and calls `seek` exactly once on release; keyboard shortcuts trigger the same code path as their
  toolbar buttons; toolbar visibility toggles correctly across playback / live / disconnected
  states.

## Explicitly out of scope for this spec

- Enhancement controls panel (denoise/contrast/target-shadow/etc.) — separate spec.
- GPS/nav track panel — separate spec.
- Sonar hardware control panel (gain/range/start-stop via `CommandClient`) — separate spec.
- F1 keybindings reference panel — separate spec (small, low-risk, can piggyback later).
- Any change to `LiveClient` or the live-mode data path.
