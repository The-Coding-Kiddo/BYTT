"""The scrolling waterfall display: combines a ping's port/starboard raw
channels into one display row, and a pyqtgraph-based scrolling image widget
that renders those rows (replaces the pygame-based FastWaterfall)."""
import cv2
import threading
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Signal
from bytt.processing.colormap import LUT_PALETTES, LUT_NAMES, build_combined_lut
from bytt.processing.enhancement import (
    DEFAULT_ENHANCE_PARAMS, enhance_pixels, tint, heavy_key, NOISE_MODES,
)

WORK_BUDGET_PX = 1_500_000       # paused, seeking, or ordinary file playback
WORK_BUDGET_PX_NLM = 40_000      # NL-Means is too slow above this size
WORK_BUDGET_PX_LIVE = 600_000    # genuine live acquisition only


def build_display_row(port_raw, stbd_raw, channel_w, port_on, stbd_on, gap,
                       interp_xs_cache):
    n = len(port_raw)
    key = (n, channel_w)
    if key not in interp_xs_cache:
        interp_xs_cache[key] = np.linspace(0, n - 1, channel_w)
    xs = interp_xs_cache[key]
    xi = np.arange(n)
    parts = []
    if port_on:
        parts.append(np.interp(xs, xi, port_raw).astype(np.float32))
    if port_on and stbd_on:
        parts.append(np.zeros(gap, dtype=np.float32))
    if stbd_on:
        parts.append(np.interp(xs, xi, stbd_raw).astype(np.float32))
    if not parts:
        return np.zeros(channel_w, dtype=np.float32)
    return np.concatenate(parts)


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

        self._cached_enhanced = None
        self._cached_target_mask = None
        self._cached_shadow_mask = None
        self._cached_heavy_key = None
        self._cached_data_generation = -1

    def set_palette(self, name: str, gain: float = 1.0, gamma: float = 1.0) -> None:
        self._combined_lut = build_combined_lut(gain=gain, gamma=gamma,
                                                  colour_lut=LUT_PALETTES[name])
        self._refresh()

    def add_row(self, raw_row_f32: np.ndarray) -> None:
        """raw_row_f32: 1D float32 array of length self.row_width, PRE-enhancement data."""
        if raw_row_f32.shape[0] != self.row_width:
            raise ValueError(
                f"add_row expected a row of length {self.row_width}, got {raw_row_f32.shape[0]}")
        self._data_generation += 1
        self.raw_buffer = np.roll(self.raw_buffer, -1, axis=0)
        self.raw_buffer[-1] = raw_row_f32
        self.rows_written += 1
        self._submit_job()

    def set_enhance_params(self, params) -> None:
        self._enhance_params = params._replace(fast=self.live_mode)
        self._submit_job(force=False)

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

    def _refresh(self) -> None:
        self._image_item.setImage(self.image_buffer, levels=(0, 255), autoLevels=False)
