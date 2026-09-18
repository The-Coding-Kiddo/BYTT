import numpy as np
import pytest
from PySide6.QtWidgets import QApplication
from bytt.gui.waterfall_view import build_display_row, WaterfallView

@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app

def test_build_display_row_concatenates_both_channels_with_gap():
    port = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    stbd = np.array([4.0, 5.0, 6.0], dtype=np.float32)
    cache = {}
    row = build_display_row(port, stbd, channel_w=3, port_on=True, stbd_on=True,
                             gap=2, interp_xs_cache=cache)
    assert row.shape == (3 + 2 + 3,)
    assert np.allclose(row[3:5], 0.0)

def test_build_display_row_single_channel_only():
    port = np.array([1.0, 2.0], dtype=np.float32)
    stbd = np.array([4.0, 5.0], dtype=np.float32)
    cache = {}
    row = build_display_row(port, stbd, channel_w=2, port_on=True, stbd_on=False,
                             gap=2, interp_xs_cache=cache)
    assert row.shape == (2,)

def test_waterfall_view_add_row_grows_image_buffer():
    view = WaterfallView(max_rows=10, width=8)
    row = np.full(8, 200, dtype=np.uint8)
    view.add_row(row)
    view.add_row(row)
    assert view.image_buffer.shape == (10, 8, 3)
    assert view.rows_written == 2


def test_waterfall_view_row_width_attribute_does_not_shadow_qwidget_width():
    view = WaterfallView(max_rows=10, width=8)
    assert view.row_width == 8
    # QWidget.width() must remain callable — this is the bug being guarded
    # against (assigning a plain int to `self.width` shadows the bound method).
    assert callable(view.width)


def test_waterfall_view_add_row_raises_on_wrong_length_input():
    view = WaterfallView(max_rows=10, width=8)
    bad_row = np.full(5, 200, dtype=np.uint8)
    with pytest.raises(ValueError):
        view.add_row(bad_row)
