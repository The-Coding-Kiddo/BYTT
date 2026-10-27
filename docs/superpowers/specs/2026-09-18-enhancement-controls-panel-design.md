---
title: Enhancement controls panel — design
status: draft — approved by user 2026-09-18
---

## Why this document exists

The full OpenCV enhancement pipeline (`bytt/processing/enhancement.py`) was ported faithfully from
`interactive_bsf_viewer.py` in an earlier sub-project and is fully tested — but `MainWindow`
currently hardcodes one fixed `EnhanceParams` value with no UI to change it. This is sub-project 3
of the full HydroWaterDemo replacement: a real controls panel exposing every enhancement setting,
matching the original pygame tool's TAB panel.

**Critical constraint driving this whole design: reuse the original's already-solved
implementation, don't redesign it.** The original (`interactive_bsf_viewer.py`) already has a
correct, working background-worker + duplicate-job-guard + generation-numbered mechanism for
reprocessing the on-screen waterfall when settings change (`_enhancement_worker`,
`_update_enhancement`, `FastWaterfall`, lines 918-1594). This spec ports that mechanism into the
new PySide6/pyqtgraph stack — the only genuine adaptation is delivery (Qt signals instead of a
per-frame poll loop from a pygame game loop we don't have).

## Scope decisions (confirmed with user)

- **Layout**: a real `QDockWidget` sidebar (native Qt dock/float/close behavior, toggled from a
  View menu), not the original's TAB-toggle overlay.
- **Persistence**: settings do NOT persist across app restarts — always reset to the same
  defaults on launch. Simpler; no config-file settings layer needed for this sub-project.
- **Retroactive re-render**: changing a setting reprocesses whatever is currently in the
  waterfall's own on-screen ring buffer (the `max_rows` window — e.g. last 2000 rows), for both
  live and playback modes identically. This does **not** mean scrubbing/reprocessing arbitrarily
  far back through an entire `.bsf` file — that's a separate, already-parked future sub-project
  (the "waterfall doesn't rewind on backward step" gap flagged in an earlier review) and is
  explicitly out of scope here.
- **Panel contents**: the 12 `EnhanceParams` fields, plus a "Reset to Defaults" button and
  Port/Starboard channel toggles — matching the original's single unified control panel
  (`_build_control_rows`, `interactive_bsf_viewer.py:2085-2116`) for exactly this subset. Playback
  transport controls (Play/Pause/Speed/Zoom/Home/GPS-panel-toggle/Live-mode/Recording/Fullscreen/
  Screenshot) already live elsewhere (playback toolbar) or are out of scope for other reasons
  (Recording, GPS panel, Live mode — separate sub-projects). Marks (drag-to-select regions,
  save/load/clear) are a wholly separate, unscoped subsystem — not touched by this spec at all.

## Architecture: port `FastWaterfall` + `_enhancement_worker` into `WaterfallView`

### What moves from `MainWindow` into `WaterfallView`

Today, `MainWindow._on_ping_received` calls `enhance_pixels` itself per-ping and hands
`WaterfallView.add_row` an already-enhanced `uint8` row. This changes: **enhancement moves into
`WaterfallView`**, which becomes responsible end-to-end for "given raw rows and current settings,
produce and display an enhanced image" — exactly the job `FastWaterfall` + `BSFViewer`'s worker
did together in the original, just merged into one widget instead of split across a widget and its
owning window.

### Raw ring buffer (ports `FastWaterfall`, lines 918-958)

`WaterfallView` gains a raw float32 ring buffer (`(max_rows, width)`), storing pre-enhancement
data for every row currently on screen — parallel to the existing `image_buffer`. `add_row`'s
signature changes: it now takes the **raw** float32 row (not `uint8`). Every call:
1. Writes the row into the raw ring buffer (head-pointer style, as the original does — O(1) per
   row, no copy — rather than the current `np.roll`-per-row approach, which copies the whole
   buffer on every single row).
2. Runs the existing per-row-cheap path (`enhance_pixels` on a single row) for immediate display,
   exactly as `MainWindow` does today — this keeps live-ping latency unaffected; only the
   *historical* reprocess is backgrounded.
3. Marks a `raw_changed` flag (ports `FastWaterfall.raw_changed`).

### Background worker (ports `_enhancement_worker`, lines 1446-1536)

A single background thread, blocked on a `threading.Event` (zero polling, instant wake on
`.set()`), holding exactly **one job slot** (not a queue — a new submission simply overwrites
whatever's waiting, so a burst of rapid changes collapses to "only the latest matters," exactly
like the original). Ports directly, unchanged in logic:

- **Job signature + duplicate-job guard**: `job_sig = (data_generation, buffer_shape, params)`.
  A new job is only submitted if `job_sig` differs from the last submitted one — if nothing
  changed, don't resubmit.
- **Generation numbers**: every submitted job gets an incrementing generation; every computed
  result carries that generation. The GUI only ever swaps in a result whose generation is newer
  than what's currently displayed — a result that finishes computing after a *newer* job has
  already been submitted (and possibly already completed) is silently discarded. This is what
  makes "new pings arrived while the worker was mid-reprocess" a non-issue with **no manual
  index-merging required**: every job recomputes the whole current buffer fresh from a snapshot
  taken at submission time, and only the freshest completed result ever wins.
- **Grayscale cache**: reuses `bytt.processing.enhancement.heavy_key` (already exists, ported in
  an earlier sub-project) to detect when only "light" params (gain/gamma/palette) changed — skips
  the expensive noise/contrast/target pipeline entirely and just recolorizes the last computed
  grayscale result. This is what makes gain/gamma/palette changes feel instant even on a full
  2000-row buffer.
- **Adaptive work-resolution budget** (`WORK_BUDGET_PX = 1_500_000`, `WORK_BUDGET_PX_NLM =
  40_000`, `WORK_BUDGET_PX_LIVE = 600_000` — exact values from `interactive_bsf_viewer.py:202-204`):
  downscales the buffer before the expensive pipeline when it exceeds the budget for the current
  mode, upscales the result back afterward. `WORK_BUDGET_PX_NLM` applies whenever denoise mode is
  NL-Means (much slower per-pixel than the other options); `WORK_BUDGET_PX_LIVE` applies when
  connected live to a towfish (keeps reprocessing from falling behind incoming packets);
  `WORK_BUDGET_PX` otherwise (playback, paused, or settled).

**Delivery — the one real adaptation.** The original's main loop calls `_update_enhancement()`
every rendered pygame frame to poll for a ready result. We have no such loop. Instead: the worker
thread emits a Qt signal `enhancement_ready(rgb: np.ndarray, generation: int)` when a job
completes; `WaterfallView` connects this directly to a slot performing the same
`generation > displayed_generation` check and swap. This removes polling entirely — Qt's signal
queuing already delivers cross-thread notifications safely and promptly, which is strictly better
than the original's frame-driven poll, not a downgrade.

### `WaterfallView`'s new public surface

- `add_row(raw_row_f32: np.ndarray) -> None` — signature change (was `uint8`, now raw `float32`).
- `set_enhance_params(params: EnhanceParams) -> None` — updates the params used for all *future*
  rows immediately, and submits a background reprocess job for the current on-screen buffer
  (subject to the duplicate-job guard above — calling this rapidly, e.g. while a spin box's arrow
  is held down, does not flood the worker).

### `MainWindow` changes

`_on_ping_received` simplifies: it builds the raw row via `build_display_row` and calls
`self.waterfall.add_row(raw_row)` — it no longer imports or calls `enhance_pixels` directly, that
responsibility has moved into `WaterfallView`.

## `ControlsPanel` (new `QDockWidget`)

Widget mapping, using the original's exact ranges/defaults/steps
(`interactive_bsf_viewer.py:192-199, 978-985, 1990-2002`) — not new ones:

| Field | Widget | Range / options | Default |
|---|---|---|---|
| Palette | `QComboBox` | `LUT_NAMES` (5 palettes) | index 0 |
| Gain | `QDoubleSpinBox` | 0.1–5.0, step 0.1 | 1.0 |
| Gamma | `QDoubleSpinBox` | 0.1–3.0, step 0.05 | 1.0 |
| Denoise | `QComboBox` | `NOISE_MODES` (Off/Median/Bilateral/NL-Means) | Off |
| Contrast | `QComboBox` | `CONTRAST_MODES` (Off/Global HistEq/CLAHE Low/Med/High) | CLAHE Low (idx 2) |
| AGC | `QCheckBox` | — | off |
| Target enhance | `QComboBox` | `TARGET_MODES` (Off/Top-Hat Bright/Top-Hat Bright+Dark/CFAR Contrast) | Off |
| Target kernel size | `QSpinBox` | 5–51, step 4 (odd sizes) | 15 |
| Shadow enhance | `QCheckBox` | — | off |
| Overlay (target/shadow tint) | `QCheckBox` | — | off |
| Sharpen | `QCheckBox` | — | off |
| HDR | `QCheckBox` | — | off |
| **Reset to Defaults** | `QPushButton` | — | resets all 12 fields above (matches `_act_reset_settings`, `interactive_bsf_viewer.py:1990-2002`) |
| **Port channel** | `QCheckBox` | on/off | on |
| **Starboard channel** | `QCheckBox` | on/off | on |

Numeric fields use `QDoubleSpinBox`/`QSpinBox` rather than sliders — the original stepped these
via discrete +/- actions, not continuous dragging, so a spin box matches that interaction model
and avoids float-scaling complexity a `QSlider` would need.

Every control change is debounced through one `QTimer` (~150ms) before emitting
`params_changed = Signal(object)` carrying a freshly-built `EnhanceParams` — connected to
`WaterfallView.set_enhance_params`. Port/Starboard toggles are separate from `EnhanceParams`
(they affect `build_display_row`'s `port_on`/`stbd_on` in `MainWindow`, not the enhancement
pipeline) and emit their own signal, `channels_changed = Signal(bool, bool)`, connected directly
in `MainWindow` (currently hardcoded `True, True`).

A `View` menu entry (`self.controls_panel.toggleViewAction()`) shows/hides the dock — free,
standard Qt behavior, no custom code needed.

## Error handling

- The worker wraps its pipeline call in try/except (ports the original's pattern at
  `interactive_bsf_viewer.py:1531-1535`): on exception, it emits `enhancement_ready(None,
  generation)` instead of crashing the thread; `WaterfallView`'s slot treats a `None` payload as
  "reprocess failed" and reports it via a signal `MainWindow` connects to the status bar, rather
  than silently doing nothing or crashing.
- `set_enhance_params` with a `None`/malformed `params` is a programmer error, not a runtime
  condition to guard — `EnhanceParams` is a `namedtuple` constructed only by `ControlsPanel` from
  validated widget state, so no additional validation layer is needed here.

## Testing approach

Consistent with the rest of this project: real objects, no mocks.

- **Worker mechanism**: submit two rapidly-differing jobs and confirm only the latest one's
  result is ever displayed (generation ordering); submit the same params/data twice and confirm
  the second submission is skipped (duplicate-job guard, verifiable via a call-count on the
  pipeline function); change only gain/gamma/palette and confirm the expensive pipeline stage is
  skipped (grayscale cache hit) while gain/gamma/palette still visibly change the result; confirm
  a buffer larger than the work budget gets downscaled and the result is still the correct final
  size after upscale-back.
- **`WaterfallView.add_row`**: confirm the raw buffer retains data correctly across the ring
  buffer's wraparound (head-pointer arithmetic), and that immediate per-row display still works
  independent of any in-flight background reprocess.
- **`ControlsPanel`**: each control change (after the debounce timer fires) emits `params_changed`
  with the expected `EnhanceParams`; Reset button resets all 12 fields to their exact defaults;
  Port/Starboard checkboxes emit `channels_changed` correctly; rapid successive changes (e.g.
  holding a spin box's up-arrow) only fire once after settling, not once per intermediate tick.
- **`MainWindow` wiring**: `_on_ping_received` no longer imports `enhance_pixels`; the dock's View
  menu toggle actually shows/hides the panel.

## Explicitly out of scope for this spec

- Persisting settings across app restarts.
- Retroactive reprocessing beyond the on-screen ring buffer (full-file rewind/scrub) — separate,
  already-parked future sub-project.
- Marks/annotation subsystem (drag-to-select, save/load/clear marks) — wholly separate, unscoped.
- Recording, GPS/nav panel, sonar hardware control panel, F1 keybindings panel — each its own
  already-identified future sub-project.
