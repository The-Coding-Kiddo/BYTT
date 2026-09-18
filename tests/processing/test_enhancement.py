import numpy as np
from bytt.processing.enhancement import EnhanceParams, enhance_pixels, heavy_key

def _default_params(**overrides):
    base = dict(noise_idx=0, contrast_idx=0, agc=False, target_idx=0,
                target_ksize=9, shadow_enh=False, overlay=0, sharpen=False,
                gain=1.0, gamma=1.0, lut_idx=0, fast=True, hdr=False)
    base.update(overrides)
    return EnhanceParams(**base)

def test_enhance_pixels_returns_uint8_same_shape():
    raw = np.random.rand(64, 128).astype(np.float32)
    out, target_mask, shadow_mask = enhance_pixels(raw, _default_params())
    assert out.dtype == np.uint8
    assert out.shape == raw.shape

def test_enhance_pixels_handles_all_zero_input():
    raw = np.zeros((32, 32), dtype=np.float32)
    out, target_mask, shadow_mask = enhance_pixels(raw, _default_params())
    assert out.shape == (32, 32)
    assert out.dtype == np.uint8

def test_heavy_key_ignores_non_heavy_fields():
    p1 = _default_params(gain=1.0)
    p2 = _default_params(gain=2.0)  # gain is not a HEAVY_FIELDS member
    assert heavy_key(p1) == heavy_key(p2)
    p3 = _default_params(sharpen=True)
    assert heavy_key(p1) != heavy_key(p3)

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
