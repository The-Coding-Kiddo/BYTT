"""The scrolling waterfall display: combines a ping's port/starboard raw
channels into one display row, and a pyqtgraph-based scrolling image widget
that renders those rows (replaces the pygame-based FastWaterfall)."""
import threading
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Signal
from bytt.processing.colormap import LUT_PALETTES, LUT_NAMES, build_combined_lut
from bytt.processing.enhancement import DEFAULT_ENHANCE_PARAMS, enhance_pixels, tint


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
        row2d = raw_row_f32.reshape(1, -1)
        img8, _target_mask, _shadow_mask = enhance_pixels(row2d, self._enhance_params)
        row_uint8 = img8[0]
        rgb_row = self._combined_lut[row_uint8]
        self.image_buffer = np.roll(self.image_buffer, -1, axis=0)
        self.image_buffer[-1] = rgb_row
        self.rows_written += 1
        self._refresh()

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

    def _refresh(self) -> None:
        self._image_item.setImage(self.image_buffer, levels=(0, 255), autoLevels=False)
