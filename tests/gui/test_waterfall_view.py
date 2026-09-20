import time
import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication
from bytt.gui.waterfall_view import build_display_row, WaterfallView
from bytt.processing.enhancement import EnhanceParams

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
    row = np.linspace(0.0, 1.0, 8, dtype=np.float32)
    view.add_row(row)
    view.add_row(row)
    assert view.image_buffer.shape == (10, 8, 3)
    assert view.raw_buffer.shape == (10, 8)
    assert view.rows_written == 2


def test_waterfall_view_row_width_attribute_does_not_shadow_qwidget_width():
    view = WaterfallView(max_rows=10, width=8)
    assert view.row_width == 8
    # QWidget.width() must remain callable — this is the bug being guarded
    # against (assigning a plain int to `self.width` shadows the bound method).
    assert callable(view.width)


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


def test_detect_channel_echo_edges_returns_none_before_buffer_is_full():
    view = WaterfallView(max_rows=50, width=100)
    row = np.random.rand(100).astype(np.float32)
    view.add_row(row)  # far fewer than max_rows

    result = view.detect_channel_echo_edges(channel_w=46, gap=8, n_rows=50)
    assert result == {'port': None, 'stbd': None}


def test_detect_channel_echo_edges_finds_real_edges_once_buffer_is_full():
    channel_w, gap = 46, 8
    width = channel_w * 2 + gap
    view = WaterfallView(max_rows=20, width=width)
    rng = np.random.default_rng(0)
    for _ in range(20):
        near = np.full(6, 0.8) + rng.normal(0, 0.001, 6)
        real = rng.uniform(0.3, 0.9, 30)
        far = np.full(10, 0.02) + rng.normal(0, 0.005, 10)
        # port is stored far-to-near (reversed) in the display row.
        port = np.concatenate([far, real, near])
        gap_zeros = np.zeros(gap, dtype=np.float32)
        stbd = np.concatenate([near, real, far])
        row = np.concatenate([port, gap_zeros, stbd]).astype(np.float32)
        view.add_row(row)

    result = view.detect_channel_echo_edges(channel_w=channel_w, gap=gap, n_rows=20)
    assert result['port'] is not None
    assert result['stbd'] is not None
    near_idx, far_idx = result['stbd']
    assert 4 <= near_idx <= 8
    assert 33 <= far_idx <= 37
