"""The scrolling waterfall display: combines a ping's port/starboard raw
channels into one display row, and a pyqtgraph-based scrolling image widget
that renders those rows (replaces the pygame-based FastWaterfall)."""
import numpy as np
import pyqtgraph as pg
from bytt.processing.colormap import LUT_PALETTES, build_combined_lut


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
    def __init__(self, max_rows=2000, width=1024, palette="Amber", parent=None):
        super().__init__(parent)
        self.max_rows = max_rows
        self.row_width = width
        self.rows_written = 0
        self.image_buffer = np.zeros((max_rows, width, 3), dtype=np.uint8)
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

    def add_row(self, row_uint8: np.ndarray) -> None:
        """row_uint8: 1D array of length self.row_width, dtype uint8 intensity."""
        if row_uint8.shape[0] != self.row_width:
            raise ValueError(
                f"add_row expected a row of length {self.row_width}, got {row_uint8.shape[0]}")
        rgb_row = self._combined_lut[row_uint8]
        self.image_buffer = np.roll(self.image_buffer, -1, axis=0)
        self.image_buffer[-1] = rgb_row
        self.rows_written += 1
        self._refresh()

    def _refresh(self) -> None:
        self._image_item.setImage(self.image_buffer, levels=(0, 255), autoLevels=False)
