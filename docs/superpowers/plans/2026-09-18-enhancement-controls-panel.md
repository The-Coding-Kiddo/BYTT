# Enhancement Controls Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the app a real, dockable enhancement-settings panel (palette/gain/gamma/denoise/
contrast/AGC/target-enhance/shadow/overlay/sharpen/HDR/reset/channel-toggles), and move
enhancement processing into `WaterfallView` with a ported background-worker mechanism so settings
changes reprocess the on-screen waterfall without blocking the GUI.

**Architecture:** `WaterfallView` gains a raw (pre-enhancement) ring buffer alongside its existing
RGB display buffer, and a single-job-slot background worker — ported directly from
`interactive_bsf_viewer.py`'s `_enhancement_worker`/`_update_enhancement`/`FastWaterfall`
(duplicate-job guard, generation numbers, grayscale cache, adaptive work-resolution budget) — that
reprocesses the whole on-screen buffer when settings change, delivering results via a Qt signal
instead of a per-frame poll loop. A new `ControlsPanel` (`QDockWidget`) exposes every setting and
emits a debounced `EnhanceParams` on change.

**Tech Stack:** Python, PySide6 (`QDockWidget`, `QComboBox`, `QDoubleSpinBox`, `QSpinBox`,
`QCheckBox`, `QTimer`), pyqtgraph, OpenCV (via the existing `bytt.processing.enhancement`), the
existing `bytt/gui/waterfall_view.py` and `bytt/gui/main_window.py`.

**Spec:** `docs/superpowers/specs/2026-09-18-enhancement-controls-panel-design.md`

## Global Constraints

- **Reuse the original's exact mechanism, don't redesign it.** Every constant, default, and the
  worker's job-submission/duplicate-guard/generation-ordering/grayscale-cache/work-budget logic
  must match `interactive_bsf_viewer.py` — cited line numbers are given per task; verify against
  them, don't invent new values.
- No new persistence layer — settings reset to defaults on every app launch.
- Retroactive reprocessing only covers the on-screen ring buffer (`max_rows`), never full-file
  history — that is a separate, already-parked future sub-project.
- Tests use real objects (real `WaterfallView`, real background threads, real Qt event-loop
  pumping via `QCoreApplication.processEvents()`) — no mocks, matching every existing test in this
  project.
- Cross-platform, no OS-specific code.

---

## Task 1: Port missing enhancement constants + `DEFAULT_ENHANCE_PARAMS`

**Files:**
- Modify: `bytt/processing/enhancement.py`
- Test: `tests/processing/test_enhancement.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `TARGET_KSIZE_MIN/MAX/STEP/DEFAULT`, `GAIN_DEFAULT/STEP/MIN/MAX`,
  `GAMMA_DEFAULT/STEP/MIN/MAX`, `DEFAULT_ENHANCE_PARAMS: EnhanceParams`. Tasks 2-6 all import
  from this module.

- [ ] **Step 1: Write the failing test**

Add to `tests/processing/test_enhancement.py`:

```python
def test_ported_constants_match_original_reference_values():
    from bytt.processing import enhancement as e
    assert (e.TARGET_KSIZE_MIN, e.TARGET_KSIZE_MAX, e.TARGET_KSIZE_STEP, e.TARGET_KSIZE_DEFAULT) \
        == (5, 51, 4, 15)
    assert (e.GAIN_DEFAULT, e.GAIN_STEP, e.GAIN_MIN, e.GAIN_MAX) == (1.0, 0.1, 0.1, 5.0)
    assert (e.GAMMA_DEFAULT, e.GAMMA_STEP, e.GAMMA_MIN, e.GAMMA_MAX) == (1.0, 0.05, 0.1, 3.0)

def test_default_enhance_params_matches_original_defaults():
    from bytt.processing.enhancement import DEFAULT_ENHANCE_PARAMS, CONTRAST_DEFAULT_IDX
    d = DEFAULT_ENHANCE_PARAMS
    assert d.noise_idx == 0
    assert d.contrast_idx == CONTRAST_DEFAULT_IDX
    assert d.agc is False
    assert d.target_idx == 0
    assert d.target_ksize == 15
    assert d.shadow_enh is False
    assert d.overlay is False
    assert d.sharpen is False
    assert d.gain == 1.0
    assert d.gamma == 1.0
    assert d.lut_idx == 0
    assert d.hdr is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/processing/test_enhancement.py -v`
Expected: FAIL — `AttributeError: module 'bytt.processing.enhancement' has no attribute 'TARGET_KSIZE_MIN'`

- [ ] **Step 3: Add the constants**

In `bytt/processing/enhancement.py`, add after the existing `TARGET_MODES = [...]` line (source
values from `interactive_bsf_viewer.py:196-199, 978, 985`):

```python
TARGET_KSIZE_MIN = 5
TARGET_KSIZE_MAX = 51
TARGET_KSIZE_STEP = 4
TARGET_KSIZE_DEFAULT = 15

GAIN_DEFAULT, GAIN_STEP, GAIN_MIN, GAIN_MAX = 1.0, 0.1, 0.1, 5.0
GAMMA_DEFAULT, GAMMA_STEP, GAMMA_MIN, GAMMA_MAX = 1.0, 0.05, 0.1, 3.0
```

At the end of the file, add:

```python
DEFAULT_ENHANCE_PARAMS = EnhanceParams(
    noise_idx=0, contrast_idx=CONTRAST_DEFAULT_IDX, agc=False, target_idx=0,
    target_ksize=TARGET_KSIZE_DEFAULT, shadow_enh=False, overlay=False, sharpen=False,
    gain=GAIN_DEFAULT, gamma=GAMMA_DEFAULT, lut_idx=0, fast=True, hdr=False,
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/processing/test_enhancement.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bytt/processing/enhancement.py tests/processing/test_enhancement.py
git commit -m "Port target-size/gain/gamma constants and default EnhanceParams"
```

---

## Task 2: `WaterfallView` — raw ring buffer + `add_row` raw-row contract

**Files:**
- Modify: `bytt/gui/waterfall_view.py`
- Modify: `tests/gui/test_waterfall_view.py`

**Interfaces:**
- Consumes: `bytt.processing.enhancement.DEFAULT_ENHANCE_PARAMS`, `enhance_pixels` (Task 1 +
  already-existing).
- Produces: `WaterfallView.add_row(raw_row_f32: np.ndarray) -> None` — **signature change**: was
  `uint8` (already-enhanced), now raw `float32` (pre-enhancement). `WaterfallView.raw_buffer:
  np.ndarray` shape `(max_rows, width)` float32. `WaterfallView._enhance_params: EnhanceParams`
  (mutable, read by `add_row`'s immediate per-row display path). Task 3 adds the background
  worker on top of this; Task 5 changes `MainWindow` to call this new `add_row` contract.

This task does NOT add the background worker yet — it only changes what `add_row` accepts and
adds the raw-data retention `add_row` needs to write into. The existing per-row-immediate-display
behavior continues working exactly as before, just enhancing raw input instead of receiving
pre-enhanced input.

- [ ] **Step 1: Write the failing tests**

Replace `test_waterfall_view_add_row_grows_image_buffer` and
`test_waterfall_view_add_row_raises_on_wrong_length_input` in `tests/gui/test_waterfall_view.py`
with:

```python
def test_waterfall_view_add_row_grows_image_buffer():
    view = WaterfallView(max_rows=10, width=8)
    row = np.linspace(0.0, 1.0, 8, dtype=np.float32)
    view.add_row(row)
    view.add_row(row)
    assert view.image_buffer.shape == (10, 8, 3)
    assert view.raw_buffer.shape == (10, 8)
    assert view.rows_written == 2

def test_waterfall_view_add_row_raises_on_wrong_length_input():
    view = WaterfallView(max_rows=10, width=8)
    bad_row = np.linspace(0.0, 1.0, 5, dtype=np.float32)
    with pytest.raises(ValueError):
        view.add_row(bad_row)

def test_waterfall_view_add_row_stores_raw_data_in_ring_buffer():
    view = WaterfallView(max_rows=3, width=4)
    row_a = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)
    row_b = np.array([0.5, 0.6, 0.7, 0.8], dtype=np.float32)
    view.add_row(row_a)
    view.add_row(row_b)
    assert np.allclose(view.raw_buffer[-1], row_b)
    assert np.allclose(view.raw_buffer[-2], row_a)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gui/test_waterfall_view.py -v`
Expected: FAIL — `TypeError` or a `ValueError` about dtype/shape, since `add_row` currently applies
the colour LUT directly to its input (`self._combined_lut[row_uint8]`), which breaks on a
`float32` array indexing a LUT.

- [ ] **Step 3: Update `WaterfallView`**

In `bytt/gui/waterfall_view.py`, update the imports:

```python
import numpy as np
import pyqtgraph as pg
from bytt.processing.colormap import LUT_PALETTES, build_combined_lut
from bytt.processing.enhancement import DEFAULT_ENHANCE_PARAMS, enhance_pixels
```

Replace the `__init__` and `add_row` methods:

```python
    def __init__(self, max_rows=2000, width=1024, palette="Amber", parent=None):
        super().__init__(parent)
        self.max_rows = max_rows
        self.row_width = width
        self.rows_written = 0
        self.raw_buffer = np.zeros((max_rows, width), dtype=np.float32)
        self.image_buffer = np.zeros((max_rows, width, 3), dtype=np.uint8)
        self._enhance_params = DEFAULT_ENHANCE_PARAMS
        self._combined_lut = build_combined_lut(gain=1.0, gamma=1.0,
                                                  colour_lut=LUT_PALETTES[palette])
        self._plot = self.addPlot()
        self._plot.invertY(True)
        self._image_item = pg.ImageItem(axisOrder='row-major')
        self._plot.addItem(self._image_item)
        self._image_item.setImage(self.image_buffer, levels=(0, 255))

    def set_palette(self, name: str, gain: float = 1.0, gamma: float = 1.0) -> None:
        self._combined_lut = build_combined_lut(gain=gain, gamma=gamma,
                                                  colour_lut=LUT_PALETTES[name])
        self._refresh()

    def add_row(self, raw_row_f32: np.ndarray) -> None:
        """raw_row_f32: 1D float32 array of length self.row_width, PRE-enhancement data."""
        if raw_row_f32.shape[0] != self.row_width:
            raise ValueError(
                f"add_row expected a row of length {self.row_width}, got {raw_row_f32.shape[0]}")
        self.raw_buffer = np.roll(self.raw_buffer, -1, axis=0)
        self.raw_buffer[-1] = raw_row_f32
        row2d = raw_row_f32.reshape(1, -1)
        img8, _target_mask, _shadow_mask = enhance_pixels(row2d, self._enhance_params)
        row_uint8 = img8[0]
        rgb_row = self._combined_lut[row_uint8]
        self.image_buffer = np.roll(self.image_buffer, -1, axis=0)
        self.image_buffer[-1] = rgb_row
        self.rows_written += 1
        self._refresh()

    def _refresh(self) -> None:
        self._image_item.setImage(self.image_buffer, levels=(0, 255), autoLevels=False)
```

Note for verification: `DEFAULT_ENHANCE_PARAMS.contrast_idx` is `CONTRAST_DEFAULT_IDX` (2,
"CLAHE Low"), so `add_row` now runs real CLAHE on every row by default, including in the small
`width=8`/`width=4` test buffers above. OpenCV's CLAHE is expected to handle small/single-row
inputs without crashing (its tile grid clamps to the image size), but this is worth confirming
empirically — if any test in this step fails with an OpenCV error rather than the expected
assertion failure, report it rather than silently changing the default; don't guess.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gui/test_waterfall_view.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bytt/gui/waterfall_view.py tests/gui/test_waterfall_view.py
git commit -m "Add raw ring buffer to WaterfallView, change add_row to accept pre-enhancement data"
```

---

## Task 3: `WaterfallView` — background worker (job submission, duplicate-guard, generation ordering)

**Files:**
- Modify: `bytt/gui/waterfall_view.py`
- Modify: `tests/gui/test_waterfall_view.py`

**Interfaces:**
- Consumes: `WaterfallView.raw_buffer`, `_enhance_params` (Task 2).
- Produces: `WaterfallView.set_enhance_params(params: EnhanceParams) -> None`,
  `WaterfallView.live_mode: bool` (default `False`), signals `enhancement_ready = Signal(object,
  int)` (rgb-or-None, generation) and `enhancement_failed = Signal(str)`. Task 4 layers the
  grayscale cache + work-budget downscaling into this task's worker method. Task 7 connects
  `ControlsPanel.params_changed` to `set_enhance_params` and sets `live_mode` from
  `MainWindow.connect_towfish`/`open_playback_file`.

This ports `_enhancement_worker`/`_update_enhancement` (`interactive_bsf_viewer.py:1446-1591`):
single job slot (not a queue — a new submission overwrites whatever's waiting), a
`(data_generation, shape, params)` signature that skips resubmission when unchanged, and
generation numbers so only the freshest completed result is ever displayed.

- [ ] **Step 1: Write the failing tests**

Add to `tests/gui/test_waterfall_view.py`:

```python
import time
from PySide6.QtCore import QCoreApplication
from bytt.processing.enhancement import EnhanceParams

def _params(**overrides):
    from bytt.processing.enhancement import DEFAULT_ENHANCE_PARAMS
    return DEFAULT_ENHANCE_PARAMS._replace(**overrides)

def test_set_enhance_params_triggers_enhancement_ready_with_correct_image():
    view = WaterfallView(max_rows=5, width=8)
    for i in range(5):
        row = np.full(8, (i + 1) / 10.0, dtype=np.float32)
        view.add_row(row)

    received = []
    view.enhancement_ready.connect(lambda rgb, gen: received.append((rgb, gen)))
    view.set_enhance_params(_params(gain=2.0, contrast_idx=0))

    deadline = time.time() + 3.0
    while time.time() < deadline and not received:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert received, "worker never emitted enhancement_ready"
    rgb, gen = received[-1]
    assert rgb is not None
    assert rgb.shape == (5, 8, 3)
    assert gen >= 1

def test_duplicate_job_is_not_resubmitted():
    view = WaterfallView(max_rows=5, width=8)
    row = np.full(8, 0.5, dtype=np.float32)
    view.add_row(row)

    params = _params(gain=1.5)
    view.set_enhance_params(params)
    deadline = time.time() + 3.0
    while time.time() < deadline and view._displayed_generation < 1:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    gen_after_first = view._job_generation

    # No new data (no add_row) and identical params -> must not resubmit.
    view.set_enhance_params(params)
    QCoreApplication.processEvents()
    assert view._job_generation == gen_after_first

def test_only_latest_generation_is_ever_displayed():
    view = WaterfallView(max_rows=5, width=8)
    row = np.full(8, 0.5, dtype=np.float32)
    view.add_row(row)

    view.set_enhance_params(_params(gain=1.0))
    view.set_enhance_params(_params(gain=3.0))  # supersedes the first before it's necessarily done

    deadline = time.time() + 3.0
    while time.time() < deadline and view._displayed_generation < view._job_generation:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert view._displayed_generation == view._job_generation
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gui/test_waterfall_view.py -v`
Expected: FAIL — `AttributeError: 'WaterfallView' object has no attribute 'set_enhance_params'`

- [ ] **Step 3: Add the worker to `WaterfallView`**

Update imports at the top of `bytt/gui/waterfall_view.py`:

```python
import threading
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Signal
from bytt.processing.colormap import LUT_PALETTES, LUT_NAMES, build_combined_lut
from bytt.processing.enhancement import DEFAULT_ENHANCE_PARAMS, enhance_pixels, tint
```

Add class attributes and constructor additions (append to the end of `__init__` from Task 2):

```python
class WaterfallView(pg.GraphicsLayoutWidget):
    enhancement_ready = Signal(object, int)   # rgb: np.ndarray | None, generation: int
    enhancement_failed = Signal(str)

    def __init__(self, max_rows=2000, width=1024, palette="Amber", parent=None):
        super().__init__(parent)
        self.max_rows = max_rows
        self.row_width = width
        self.rows_written = 0
        self.raw_buffer = np.zeros((max_rows, width), dtype=np.float32)
        self.image_buffer = np.zeros((max_rows, width, 3), dtype=np.uint8)
        self._enhance_params = DEFAULT_ENHANCE_PARAMS
        self._combined_lut = build_combined_lut(gain=1.0, gamma=1.0,
                                                  colour_lut=LUT_PALETTES[palette])
        self._plot = self.addPlot()
        self._plot.invertY(True)
        self._image_item = pg.ImageItem(axisOrder='row-major')
        self._plot.addItem(self._image_item)
        self._image_item.setImage(self.image_buffer, levels=(0, 255))

        self.live_mode = False
        self._data_generation = 0
        self._job_lock = threading.Lock()
        self._job = None
        self._job_sig = None
        self._job_generation = 0
        self._displayed_generation = 0
        self._job_event = threading.Event()
        self._worker_thread = threading.Thread(target=self._enhancement_worker, daemon=True)
        self._worker_thread.start()
        self.enhancement_ready.connect(self._on_enhancement_ready)
```

Add `self._data_generation += 1` as the first line inside `add_row` (from Task 2), right after the
length check — this is what lets the worker's job signature detect "the buffer changed since the
last submitted job":

```python
    def add_row(self, raw_row_f32: np.ndarray) -> None:
        """raw_row_f32: 1D float32 array of length self.row_width, PRE-enhancement data."""
        if raw_row_f32.shape[0] != self.row_width:
            raise ValueError(
                f"add_row expected a row of length {self.row_width}, got {raw_row_f32.shape[0]}")
        self._data_generation += 1
        self.raw_buffer = np.roll(self.raw_buffer, -1, axis=0)
        self.raw_buffer[-1] = raw_row_f32
        row2d = raw_row_f32.reshape(1, -1)
        img8, _target_mask, _shadow_mask = enhance_pixels(row2d, self._enhance_params)
        row_uint8 = img8[0]
        rgb_row = self._combined_lut[row_uint8]
        self.image_buffer = np.roll(self.image_buffer, -1, axis=0)
        self.image_buffer[-1] = rgb_row
        self.rows_written += 1
        self._refresh()
```

Add the new methods:

```python
    def set_enhance_params(self, params) -> None:
        self._enhance_params = params._replace(fast=self.live_mode)
        self._submit_job(force=True)

    def _submit_job(self, force: bool = False) -> None:
        raw_snapshot = self.raw_buffer.copy()
        job_sig = (self._data_generation, raw_snapshot.shape, self._enhance_params)
        if not force and job_sig == self._job_sig:
            return
        with self._job_lock:
            self._job_generation += 1
            self._job = (raw_snapshot, self._enhance_params, self._job_generation,
                         self._data_generation)
            self._job_sig = job_sig
        self._job_event.set()

    def _enhancement_worker(self) -> None:
        last_done_gen = 0
        while True:
            self._job_event.wait()
            self._job_event.clear()
            with self._job_lock:
                job = self._job
            if job is None or job[2] <= last_done_gen:
                continue
            raw, params, gen, _data_gen = job
            last_done_gen = gen
            try:
                img8, target_mask, shadow_mask = enhance_pixels(raw, params)
                colour_lut = LUT_PALETTES[LUT_NAMES[params.lut_idx]]
                combined_lut = build_combined_lut(params.gain, params.gamma, colour_lut)
                rgb = combined_lut[img8]
                if params.overlay:
                    rgb = rgb.copy()
                    rgb = tint(rgb, target_mask, (60, 255, 140))
                    rgb = tint(rgb, shadow_mask, (255, 60, 200))
                self.enhancement_ready.emit(rgb, gen)
            except Exception:
                self.enhancement_ready.emit(None, gen)

    def _on_enhancement_ready(self, rgb, gen: int) -> None:
        if gen <= self._displayed_generation:
            return
        self._displayed_generation = gen
        if rgb is None:
            self.enhancement_failed.emit("enhancement pipeline error")
            return
        if rgb.shape[:2] == self.image_buffer.shape[:2]:
            self.image_buffer = rgb
            self._refresh()
```

`_enhancement_worker` runs for the lifetime of the process (a daemon thread with no explicit stop
— this matches the original's `_enhancement_worker`, which also never terminates independently of
the app itself; `WaterfallView` is constructed once and lives for the whole app session, same as
`BSFViewer` did).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gui/test_waterfall_view.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bytt/gui/waterfall_view.py tests/gui/test_waterfall_view.py
git commit -m "Add background enhancement worker to WaterfallView"
```

---

## Task 4: `WaterfallView` — grayscale cache + adaptive work-resolution budget

**Files:**
- Modify: `bytt/gui/waterfall_view.py`
- Modify: `tests/gui/test_waterfall_view.py`

**Interfaces:**
- Consumes: `bytt.processing.enhancement.heavy_key`, `NOISE_MODES` (already exist).
- Produces: nothing new for later tasks — this layers optimization into Task 3's worker without
  changing its public interface (`set_enhance_params`, `enhancement_ready`, `live_mode` are
  unchanged).

Ports the grayscale cache and adaptive downscale from `_enhancement_worker`
(`interactive_bsf_viewer.py:1465-1517`), and the work-budget constants
(`interactive_bsf_viewer.py:202-204`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/gui/test_waterfall_view.py`:

```python
def test_grayscale_cache_skips_pipeline_when_only_light_params_change(monkeypatch):
    import bytt.gui.waterfall_view as wv_module
    call_count = {"n": 0}
    real_enhance = wv_module.enhance_pixels
    def counting_enhance(*args, **kwargs):
        call_count["n"] += 1
        return real_enhance(*args, **kwargs)
    monkeypatch.setattr(wv_module, "enhance_pixels", counting_enhance)

    view = WaterfallView(max_rows=5, width=8)
    row = np.full(8, 0.5, dtype=np.float32)
    view.add_row(row)  # add_row's own per-row call also goes through enhance_pixels

    calls_before_jobs = call_count["n"]
    view.set_enhance_params(_params(gain=1.0))
    deadline = time.time() + 3.0
    while time.time() < deadline and view._displayed_generation < view._job_generation:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    calls_after_first_job = call_count["n"]
    assert calls_after_first_job > calls_before_jobs  # first job: real pipeline ran

    # Only gain changes (a "light" param, not in HEAVY_FIELDS) -> cache hit, no new pipeline call.
    view.set_enhance_params(_params(gain=2.5))
    deadline = time.time() + 3.0
    while time.time() < deadline and view._displayed_generation < view._job_generation:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert call_count["n"] == calls_after_first_job

    # A "heavy" param (contrast_idx) changes -> cache miss, pipeline runs again.
    view.set_enhance_params(_params(gain=2.5, contrast_idx=0))
    deadline = time.time() + 3.0
    while time.time() < deadline and view._displayed_generation < view._job_generation:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert call_count["n"] > calls_after_first_job

def test_large_buffer_gets_downscaled_and_upscaled_back_to_original_shape():
    # 50*2000 = 100,000px. That's under WORK_BUDGET_PX (1,500,000) and
    # WORK_BUDGET_PX_LIVE (600,000), but over WORK_BUDGET_PX_NLM (40,000) --
    # so selecting NL-Means denoise (NOISE_MODES index 3) is what forces a
    # real downscale-then-upscale-back within a test that stays fast.
    view = WaterfallView(max_rows=50, width=2000)
    for _ in range(50):
        row = np.random.rand(2000).astype(np.float32)
        view.add_row(row)

    received = []
    view.enhancement_ready.connect(lambda rgb, gen: received.append(rgb))
    view.set_enhance_params(_params(gain=1.0, noise_idx=3))  # 3 == NOISE_MODES.index("NL-Means")
    deadline = time.time() + 5.0
    while time.time() < deadline and not received:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert received
    assert received[-1] is not None
    assert received[-1].shape == (50, 2000, 3)  # full original resolution after upscale-back
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gui/test_waterfall_view.py -v`
Expected: `test_grayscale_cache_skips_pipeline_when_only_light_params_change` and
`test_large_buffer_gets_downscaled_and_upscaled_back_to_original_shape` FAIL (grayscale cache
provides no speedup yet since it doesn't exist — the light-param-only case will show a pipeline
call increase where the test expects none; the downscale test should still functionally pass
shape-wise since `enhance_pixels` naturally preserves input shape even without a budget system —
if it unexpectedly passes already, that's fine, it just means Step 3 doesn't change its behavior,
only the cache test's behavior. Confirm which is which before writing Step 3.)

- [ ] **Step 3: Add the cache and work-budget to `_enhancement_worker`**

Update imports:

```python
import cv2
import threading
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Signal
from bytt.processing.colormap import LUT_PALETTES, LUT_NAMES, build_combined_lut
from bytt.processing.enhancement import (
    DEFAULT_ENHANCE_PARAMS, enhance_pixels, tint, heavy_key, NOISE_MODES,
)
```

Add module-level constants (values from `interactive_bsf_viewer.py:202-204`):

```python
WORK_BUDGET_PX = 1_500_000       # paused, seeking, or ordinary file playback
WORK_BUDGET_PX_NLM = 40_000      # NL-Means is too slow above this size
WORK_BUDGET_PX_LIVE = 600_000    # genuine live acquisition only
```

Add cache state to the end of `__init__` (after the worker-thread setup from Task 3):

```python
        self._cached_enhanced = None
        self._cached_target_mask = None
        self._cached_shadow_mask = None
        self._cached_heavy_key = None
        self._cached_data_generation = -1
```

Replace `_enhancement_worker`'s job-processing body (everything inside the `try:` block) with:

```python
            raw, params, gen, data_gen = job
            last_done_gen = gen
            try:
                fh, fw = raw.shape
                hk = heavy_key(params)
                use_cache = (
                    self._cached_enhanced is not None
                    and data_gen == self._cached_data_generation
                    and hk == self._cached_heavy_key
                    and self._cached_enhanced.shape[:2] == (fh, fw)
                )
                if use_cache:
                    img8 = self._cached_enhanced
                    target_mask = self._cached_target_mask
                    shadow_mask = self._cached_shadow_mask
                else:
                    if NOISE_MODES[params.noise_idx] == "NL-Means":
                        budget = WORK_BUDGET_PX_NLM
                    elif params.fast:
                        budget = WORK_BUDGET_PX_LIVE
                    else:
                        budget = WORK_BUDGET_PX
                    scale = min(1.0, (budget / float(fh * fw)) ** 0.5)
                    if scale < 1.0:
                        ww, wh = max(1, int(fw * scale)), max(1, int(fh * scale))
                        work = cv2.resize(raw, (ww, wh), interpolation=cv2.INTER_AREA)
                        p_scaled = params._replace(
                            target_ksize=max(3, int(round(params.target_ksize * scale)) | 1))
                    else:
                        work = raw
                        p_scaled = params
                    img8, target_mask, shadow_mask = enhance_pixels(work, p_scaled)
                    if scale < 1.0:
                        interp = cv2.INTER_LINEAR if params.fast else cv2.INTER_LANCZOS4
                        img8 = cv2.resize(img8, (fw, fh), interpolation=interp)
                        if target_mask is not None:
                            target_mask = cv2.resize(
                                target_mask.astype(np.uint8), (fw, fh),
                                interpolation=cv2.INTER_NEAREST).astype(bool)
                        if shadow_mask is not None:
                            shadow_mask = cv2.resize(
                                shadow_mask.astype(np.uint8), (fw, fh),
                                interpolation=cv2.INTER_NEAREST).astype(bool)
                    self._cached_enhanced = img8
                    self._cached_target_mask = target_mask
                    self._cached_shadow_mask = shadow_mask
                    self._cached_heavy_key = hk
                    self._cached_data_generation = data_gen

                colour_lut = LUT_PALETTES[LUT_NAMES[params.lut_idx]]
                combined_lut = build_combined_lut(params.gain, params.gamma, colour_lut)
                rgb = combined_lut[img8]
                if params.overlay:
                    rgb = rgb.copy()
                    rgb = tint(rgb, target_mask, (60, 255, 140))
                    rgb = tint(rgb, shadow_mask, (255, 60, 200))
                self.enhancement_ready.emit(rgb, gen)
            except Exception:
                self.enhancement_ready.emit(None, gen)
```

Note this uses `params.fast` (not `self.live_mode` directly) to choose the live-vs-file budget —
`set_enhance_params` (Task 3) already substitutes `params._replace(fast=self.live_mode)` before
submitting, so `params.fast` is always in sync with the view's current mode by the time a job
reaches the worker.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gui/test_waterfall_view.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bytt/gui/waterfall_view.py tests/gui/test_waterfall_view.py
git commit -m "Add grayscale cache and adaptive work-resolution budget to enhancement worker"
```

---

## Task 5: `MainWindow` — simplify `_on_ping_received` to the raw-row contract

**Files:**
- Modify: `bytt/gui/main_window.py`
- Modify: `tests/gui/test_main_window.py` (verify existing tests still pass; add one new test)

**Interfaces:**
- Consumes: `WaterfallView.add_row(raw_row_f32)` (Task 2), `WaterfallView.live_mode` (Task 3).
- Produces: nothing new for later tasks — Task 7 builds on the resulting `_on_ping_received` by
  swapping its hardcoded `port_on=True, stbd_on=True` for panel-driven state.

- [ ] **Step 1: Write the failing test**

Add to `tests/gui/test_main_window.py`:

```python
def test_on_ping_received_no_longer_imports_enhance_pixels_directly():
    import bytt.gui.main_window as mw_module
    assert not hasattr(mw_module, "enhance_pixels")
    assert not hasattr(mw_module, "_DEFAULT_ENHANCE_PARAMS")
```

The existing tests `test_on_ping_received_adds_one_row_without_raising` and
`test_on_ping_received_reports_error_via_status_bar_on_malformed_input` (already in this file)
exercise `_on_ping_received` end-to-end against real port/starboard arrays and are expected to
keep passing unmodified after this change — they test observable behavior
(`waterfall.rows_written`, the status bar message), not `MainWindow`'s internal enhancement
plumbing, so nothing about their assertions needs to change.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gui/test_main_window.py -v`
Expected: `test_on_ping_received_no_longer_imports_enhance_pixels_directly` FAILS, since
`main_window.py` currently does import `enhance_pixels` and define `_DEFAULT_ENHANCE_PARAMS`.

- [ ] **Step 3: Simplify `MainWindow`**

Remove the line `from bytt.processing.enhancement import EnhanceParams, enhance_pixels` and the
`_DEFAULT_ENHANCE_PARAMS = EnhanceParams(...)` block entirely from `bytt/gui/main_window.py`.

Replace `_on_ping_received` with:

```python
    def _on_ping_received(self, port_raw, stbd_raw, meta) -> None:
        # build_display_row interpolates EACH channel to channel_w samples,
        # then concatenates them with a gap in between, so the resulting row
        # length is 2*channel_w + gap. Solve for channel_w so that total
        # equals self.waterfall.row_width, which is what add_row() requires.
        try:
            gap = 8
            channel_w = (self.waterfall.row_width - gap) // 2
            row_f32 = build_display_row(
                port_raw, stbd_raw, channel_w=channel_w,
                port_on=True, stbd_on=True, gap=gap, interp_xs_cache=self._interp_cache,
            )
            self.waterfall.add_row(row_f32)
        except Exception as e:
            self.statusBar().showMessage(f"ping display error: {e}")
```

(`port_on=True, stbd_on=True` stays hardcoded here for now — Task 7 replaces these with
panel-driven state once `ControlsPanel` exists.)

In `connect_towfish`, after `self.source = LiveClient(host, data_port, parent=self)`, add:

```python
        self.waterfall.live_mode = True
```

In `open_playback_file`, after `self.source = PlaybackSource(path)`, add:

```python
        self.waterfall.live_mode = False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gui/test_main_window.py -v`
Expected: PASS — all tests in this file, including the pre-existing ones.

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `QT_QPA_PLATFORM=offscreen pytest -v`
Expected: PASS, no failures anywhere in the project.

- [ ] **Step 6: Commit**

```bash
git add bytt/gui/main_window.py tests/gui/test_main_window.py
git commit -m "Simplify MainWindow._on_ping_received to the raw-row WaterfallView contract"
```

---

## Task 6: `ControlsPanel` (new file)

**Files:**
- Create: `bytt/gui/controls_panel.py`
- Test: `tests/gui/test_controls_panel.py`

**Interfaces:**
- Consumes: `bytt.processing.enhancement` (`EnhanceParams`, `DEFAULT_ENHANCE_PARAMS`,
  `NOISE_MODES`, `CONTRAST_MODES`, `TARGET_MODES`, the `GAIN_*`/`GAMMA_*`/`TARGET_KSIZE_*`
  constants — all from Task 1), `bytt.processing.colormap.LUT_NAMES`.
- Produces: `ControlsPanel(QDockWidget)` with signals `params_changed = Signal(object)`
  (`EnhanceParams`) and `channels_changed = Signal(bool, bool)` (port_on, stbd_on), method
  `current_params() -> EnhanceParams`, method `reset_to_defaults() -> None`. Task 7 instantiates
  this and connects both signals in `MainWindow`.

- [ ] **Step 1: Write the failing tests**

Create `tests/gui/test_controls_panel.py`:

```python
import time
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication
from bytt.gui.controls_panel import ControlsPanel
from bytt.processing.enhancement import DEFAULT_ENHANCE_PARAMS

@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app

def _wait_for(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline and not predicate():
        QCoreApplication.processEvents()
        time.sleep(0.01)
    return predicate()

def test_current_params_matches_defaults_on_construction():
    panel = ControlsPanel()
    p = panel.current_params()
    assert p.noise_idx == DEFAULT_ENHANCE_PARAMS.noise_idx
    assert p.contrast_idx == DEFAULT_ENHANCE_PARAMS.contrast_idx
    assert p.agc == DEFAULT_ENHANCE_PARAMS.agc
    assert p.target_idx == DEFAULT_ENHANCE_PARAMS.target_idx
    assert p.target_ksize == DEFAULT_ENHANCE_PARAMS.target_ksize
    assert p.shadow_enh == DEFAULT_ENHANCE_PARAMS.shadow_enh
    assert p.overlay == DEFAULT_ENHANCE_PARAMS.overlay
    assert p.sharpen == DEFAULT_ENHANCE_PARAMS.sharpen
    assert p.gain == DEFAULT_ENHANCE_PARAMS.gain
    assert p.gamma == DEFAULT_ENHANCE_PARAMS.gamma
    assert p.lut_idx == DEFAULT_ENHANCE_PARAMS.lut_idx
    assert p.hdr == DEFAULT_ENHANCE_PARAMS.hdr

def test_changing_gain_emits_params_changed_after_debounce():
    panel = ControlsPanel()
    received = []
    panel.params_changed.connect(lambda p: received.append(p))
    panel.gain_spin.setValue(3.5)
    assert _wait_for(lambda: len(received) > 0, timeout=1.0)
    assert received[-1].gain == 3.5

def test_rapid_successive_changes_only_fire_once_after_settling():
    panel = ControlsPanel()
    received = []
    panel.params_changed.connect(lambda p: received.append(p))
    for v in (1.5, 2.0, 2.5, 3.0):
        panel.gain_spin.setValue(v)
        QCoreApplication.processEvents()
    assert _wait_for(lambda: len(received) > 0, timeout=1.0)
    time.sleep(0.3)
    QCoreApplication.processEvents()
    assert len(received) == 1
    assert received[0].gain == 3.0

def test_reset_to_defaults_resets_every_field_and_emits():
    panel = ControlsPanel()
    panel.gain_spin.setValue(4.0)
    panel.contrast_combo.setCurrentIndex(0)
    panel.agc_check.setChecked(True)
    received = []
    panel.params_changed.connect(lambda p: received.append(p))
    panel.reset_to_defaults()
    assert received
    p = received[-1]
    assert p.gain == DEFAULT_ENHANCE_PARAMS.gain
    assert p.contrast_idx == DEFAULT_ENHANCE_PARAMS.contrast_idx
    assert p.agc == DEFAULT_ENHANCE_PARAMS.agc

def test_channel_toggles_emit_channels_changed():
    panel = ControlsPanel()
    received = []
    panel.channels_changed.connect(lambda port_on, stbd_on: received.append((port_on, stbd_on)))
    panel.port_check.setChecked(False)
    assert received[-1] == (False, True)
    panel.stbd_check.setChecked(False)
    assert received[-1] == (False, False)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `mkdir -p tests/gui && pytest tests/gui/test_controls_panel.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bytt.gui.controls_panel'`

- [ ] **Step 3: Create `bytt/gui/controls_panel.py`**

```python
"""Dockable panel exposing the enhancement pipeline's settings plus
Port/Starboard channel toggles -- ported from interactive_bsf_viewer.py's
TAB controls panel (interactive_bsf_viewer.py:2085-2116)."""
from PySide6.QtWidgets import (
    QDockWidget, QWidget, QFormLayout, QComboBox, QDoubleSpinBox, QSpinBox,
    QCheckBox, QPushButton,
)
from PySide6.QtCore import Signal, QTimer
from bytt.processing.enhancement import (
    EnhanceParams, DEFAULT_ENHANCE_PARAMS, NOISE_MODES, CONTRAST_MODES, TARGET_MODES,
    GAIN_DEFAULT, GAIN_STEP, GAIN_MIN, GAIN_MAX,
    GAMMA_DEFAULT, GAMMA_STEP, GAMMA_MIN, GAMMA_MAX,
    TARGET_KSIZE_MIN, TARGET_KSIZE_MAX, TARGET_KSIZE_STEP, TARGET_KSIZE_DEFAULT,
)
from bytt.processing.colormap import LUT_NAMES


class ControlsPanel(QDockWidget):
    params_changed = Signal(object)         # EnhanceParams
    channels_changed = Signal(bool, bool)   # port_on, stbd_on

    def __init__(self, parent=None):
        super().__init__("Enhancement Controls", parent)
        body = QWidget(self)
        form = QFormLayout(body)

        self.palette_combo = QComboBox()
        self.palette_combo.addItems(LUT_NAMES)
        form.addRow("Palette", self.palette_combo)

        self.gain_spin = QDoubleSpinBox()
        self.gain_spin.setRange(GAIN_MIN, GAIN_MAX)
        self.gain_spin.setSingleStep(GAIN_STEP)
        self.gain_spin.setValue(GAIN_DEFAULT)
        form.addRow("Gain", self.gain_spin)

        self.gamma_spin = QDoubleSpinBox()
        self.gamma_spin.setRange(GAMMA_MIN, GAMMA_MAX)
        self.gamma_spin.setSingleStep(GAMMA_STEP)
        self.gamma_spin.setValue(GAMMA_DEFAULT)
        form.addRow("Gamma", self.gamma_spin)

        self.noise_combo = QComboBox()
        self.noise_combo.addItems(NOISE_MODES)
        form.addRow("Denoise", self.noise_combo)

        self.contrast_combo = QComboBox()
        self.contrast_combo.addItems(CONTRAST_MODES)
        self.contrast_combo.setCurrentIndex(DEFAULT_ENHANCE_PARAMS.contrast_idx)
        form.addRow("Contrast", self.contrast_combo)

        self.agc_check = QCheckBox()
        form.addRow("AGC", self.agc_check)

        self.target_combo = QComboBox()
        self.target_combo.addItems(TARGET_MODES)
        form.addRow("Target enhance", self.target_combo)

        self.target_ksize_spin = QSpinBox()
        self.target_ksize_spin.setRange(TARGET_KSIZE_MIN, TARGET_KSIZE_MAX)
        self.target_ksize_spin.setSingleStep(TARGET_KSIZE_STEP)
        self.target_ksize_spin.setValue(TARGET_KSIZE_DEFAULT)
        form.addRow("Target kernel size", self.target_ksize_spin)

        self.shadow_check = QCheckBox()
        form.addRow("Shadow enhance", self.shadow_check)

        self.overlay_check = QCheckBox()
        form.addRow("Overlay", self.overlay_check)

        self.sharpen_check = QCheckBox()
        form.addRow("Sharpen", self.sharpen_check)

        self.hdr_check = QCheckBox()
        form.addRow("HDR", self.hdr_check)

        self.reset_button = QPushButton("Reset to Defaults")
        form.addRow(self.reset_button)

        self.port_check = QCheckBox()
        self.port_check.setChecked(True)
        form.addRow("Port channel", self.port_check)

        self.stbd_check = QCheckBox()
        self.stbd_check.setChecked(True)
        form.addRow("Starboard channel", self.stbd_check)

        body.setLayout(form)
        self.setWidget(body)

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(150)
        self._debounce_timer.timeout.connect(self._emit_params_changed)

        for widget, signal_name in (
            (self.palette_combo, 'currentIndexChanged'),
            (self.gain_spin, 'valueChanged'),
            (self.gamma_spin, 'valueChanged'),
            (self.noise_combo, 'currentIndexChanged'),
            (self.contrast_combo, 'currentIndexChanged'),
            (self.agc_check, 'stateChanged'),
            (self.target_combo, 'currentIndexChanged'),
            (self.target_ksize_spin, 'valueChanged'),
            (self.shadow_check, 'stateChanged'),
            (self.overlay_check, 'stateChanged'),
            (self.sharpen_check, 'stateChanged'),
            (self.hdr_check, 'stateChanged'),
        ):
            getattr(widget, signal_name).connect(self._schedule_params_changed)

        self.reset_button.clicked.connect(self.reset_to_defaults)
        self.port_check.stateChanged.connect(self._emit_channels_changed)
        self.stbd_check.stateChanged.connect(self._emit_channels_changed)

    def _schedule_params_changed(self, *_args) -> None:
        self._debounce_timer.start()

    def current_params(self) -> EnhanceParams:
        return EnhanceParams(
            noise_idx=self.noise_combo.currentIndex(),
            contrast_idx=self.contrast_combo.currentIndex(),
            agc=self.agc_check.isChecked(),
            target_idx=self.target_combo.currentIndex(),
            target_ksize=self.target_ksize_spin.value(),
            shadow_enh=self.shadow_check.isChecked(),
            overlay=self.overlay_check.isChecked(),
            sharpen=self.sharpen_check.isChecked(),
            gain=self.gain_spin.value(),
            gamma=self.gamma_spin.value(),
            lut_idx=self.palette_combo.currentIndex(),
            fast=False,  # overridden by WaterfallView.set_enhance_params based on live_mode
            hdr=self.hdr_check.isChecked(),
        )

    def _emit_params_changed(self) -> None:
        self.params_changed.emit(self.current_params())

    def _emit_channels_changed(self, *_args) -> None:
        self.channels_changed.emit(self.port_check.isChecked(), self.stbd_check.isChecked())

    def reset_to_defaults(self) -> None:
        d = DEFAULT_ENHANCE_PARAMS
        self.palette_combo.setCurrentIndex(d.lut_idx)
        self.gain_spin.setValue(d.gain)
        self.gamma_spin.setValue(d.gamma)
        self.noise_combo.setCurrentIndex(d.noise_idx)
        self.contrast_combo.setCurrentIndex(d.contrast_idx)
        self.agc_check.setChecked(d.agc)
        self.target_combo.setCurrentIndex(d.target_idx)
        self.target_ksize_spin.setValue(d.target_ksize)
        self.shadow_check.setChecked(d.shadow_enh)
        self.overlay_check.setChecked(d.overlay)
        self.sharpen_check.setChecked(d.sharpen)
        self.hdr_check.setChecked(d.hdr)
        self._emit_params_changed()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gui/test_controls_panel.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bytt/gui/controls_panel.py tests/gui/test_controls_panel.py
git commit -m "Add ControlsPanel with enhancement settings and channel toggles"
```

---

## Task 7: Wire `ControlsPanel` into `MainWindow`

**Files:**
- Modify: `bytt/gui/main_window.py`
- Modify: `tests/gui/test_main_window.py`

**Interfaces:**
- Consumes: `ControlsPanel` (Task 6), `WaterfallView.set_enhance_params` (Task 3).
- Produces: `MainWindow.controls_panel: ControlsPanel`. Final task in this plan.

- [ ] **Step 1: Write the failing tests**

Add to `tests/gui/test_main_window.py`:

```python
def test_controls_panel_exists_and_is_docked():
    window = MainWindow(AppConfig())
    assert window.controls_panel is not None
    assert window.dockWidgetArea(window.controls_panel) is not None

def test_view_menu_has_controls_panel_toggle():
    window = MainWindow(AppConfig())
    menu_titles = [m.title() for m in window.menuBar().findChildren(type(window.menuBar().addMenu("_probe")))]
    # Simpler, robust check: the panel's own toggle action must exist and be
    # associated with the panel's dock widget.
    toggle = window.controls_panel.toggleViewAction()
    assert toggle is not None
    assert toggle.isCheckable()

def test_params_changed_reaches_waterfall_set_enhance_params(tmp_path, monkeypatch):
    from bytt.protocol import constants as pc
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(bytes(header))

    window = MainWindow(AppConfig())
    window.open_playback_file(str(bsf_path))

    calls = []
    window.waterfall.set_enhance_params = lambda p: calls.append(p)
    window.controls_panel.gain_spin.setValue(2.5)

    import time
    from PySide6.QtCore import QCoreApplication
    deadline = time.time() + 1.0
    while time.time() < deadline and not calls:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert calls
    assert calls[-1].gain == 2.5
    window.source.stop()

def test_channels_changed_updates_ping_received_channel_state(tmp_path):
    from bytt.protocol import constants as pc
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(bytes(header))

    window = MainWindow(AppConfig())
    window.open_playback_file(str(bsf_path))

    window.controls_panel.port_check.setChecked(False)
    assert window._port_on is False
    assert window._stbd_on is True
    window.source.stop()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gui/test_main_window.py -v`
Expected: FAIL — `AttributeError: 'MainWindow' object has no attribute 'controls_panel'`

- [ ] **Step 3: Wire `ControlsPanel` into `MainWindow`**

Update imports at the top of `bytt/gui/main_window.py`:

```python
from bytt.gui.controls_panel import ControlsPanel
```

In `MainWindow.__init__`, after the `self.waterfall = WaterfallView()` /
`self.setCentralWidget(self.waterfall)` lines, add:

```python
        self.controls_panel = ControlsPanel(self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.controls_panel)
        self.controls_panel.params_changed.connect(self.waterfall.set_enhance_params)
        self.controls_panel.channels_changed.connect(self._on_channels_changed)
        self._port_on = True
        self._stbd_on = True

        view_menu = self.menuBar().addMenu("&View")
        view_menu.addAction(self.controls_panel.toggleViewAction())
```

(Place this before the existing `connect_menu = self.menuBar().addMenu("&Connect")` block or
after it — order between the two top-level menus doesn't matter; keep the rest of `__init__`
unchanged around this insertion.)

Add the new handler method (near `_on_position_changed`):

```python
    def _on_channels_changed(self, port_on: bool, stbd_on: bool) -> None:
        self._port_on = port_on
        self._stbd_on = stbd_on
```

Update `_on_ping_received` (from Task 5) to use this state instead of the hardcoded values:

```python
    def _on_ping_received(self, port_raw, stbd_raw, meta) -> None:
        try:
            gap = 8
            channel_w = (self.waterfall.row_width - gap) // 2
            row_f32 = build_display_row(
                port_raw, stbd_raw, channel_w=channel_w,
                port_on=self._port_on, stbd_on=self._stbd_on, gap=gap,
                interp_xs_cache=self._interp_cache,
            )
            self.waterfall.add_row(row_f32)
        except Exception as e:
            self.statusBar().showMessage(f"ping display error: {e}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gui/test_main_window.py -v`
Expected: PASS — all tests including the 4 new ones.

- [ ] **Step 5: Run the full project test suite**

Run: `QT_QPA_PLATFORM=offscreen pytest -v`
Expected: PASS, every test in the project green.

- [ ] **Step 6: Manually verify against a real display**

Run: `venve/bin/python -m bytt.app`, then **Open Playback File...** against a real `.bsf`
recording. Expected: an "Enhancement Controls" dock panel appears (right side by default),
showing Palette/Gain/Gamma/Denoise/Contrast/AGC/Target enhance/Target kernel size/Shadow
enhance/Overlay/Sharpen/HDR/Reset to Defaults/Port channel/Starboard channel. Changing any control
visibly reprocesses the on-screen waterfall within roughly the 150ms debounce window plus however
long the background worker takes; dragging a spin box's value up/down doesn't flood the pipeline
with one job per intermediate tick. Reset to Defaults returns every field to its original value
and the display updates accordingly. Unchecking Port or Starboard channel removes that channel
from new incoming rows. The `View` menu's checkbox toggles the panel's visibility and it can be
undocked/redocked/closed like a normal Qt dock widget.

- [ ] **Step 7: Commit**

```bash
git add bytt/gui/main_window.py tests/gui/test_main_window.py
git commit -m "Wire ControlsPanel into MainWindow"
```

---

## Self-Review Notes

- **Spec coverage:** Section A (worker/job mechanism port: duplicate-guard, generation ordering,
  grayscale cache, work budget) — Tasks 2-4. Section B (`ControlsPanel`, all 12 `EnhanceParams`
  fields + Reset + Port/Starboard, exact original ranges/defaults) — Tasks 1 + 6. `MainWindow`
  wiring (dock, View menu toggle, `params_changed`/`channels_changed` connections,
  `_on_ping_received` simplification) — Tasks 5 + 7. Error handling (`enhancement_failed` signal
  on pipeline exception) — Task 3. Debounce behavior — Task 6, verified end-to-end in Task 7.
  Retroactive re-render scoped to the on-screen ring buffer only (no full-file rewind) — inherent
  to the design, nothing in any task reaches outside `raw_buffer`.
- **Placeholder scan:** no TBD/TODO markers. Task 4's Step 1 flags an intentionally-approximate
  first draft of one test (`test_large_buffer_gets_downscaled_and_upscaled_back_to_original_shape`)
  and gives the exact concrete correction needed before running it — this is a documented,
  resolved ambiguity with a specific fix, not an unresolved placeholder.
- **Type consistency:** `WaterfallView.add_row(raw_row_f32)` signature is consistent from its
  Task 2 introduction through every later task and `MainWindow`'s two call sites (Tasks 5, 7).
  `EnhanceParams`/`DEFAULT_ENHANCE_PARAMS` field names match between Task 1's definition, Task 3's
  `set_enhance_params`, Task 4's `heavy_key`/`NOISE_MODES` usage, and Task 6's `current_params()`
  construction. `enhancement_ready`/`enhancement_failed` signal signatures are defined once (Task
  3) and never redefined. `params_changed`/`channels_changed` signal signatures match between
  Task 6's definition and Task 7's connections.
