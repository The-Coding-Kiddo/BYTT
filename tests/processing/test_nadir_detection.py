import numpy as np
from bytt.processing.nadir_detection import detect_echo_edges


def _synthetic_channel(n_rows=50, near_len=10, real_len=70, far_len=20, seed=0):
    rng = np.random.default_rng(seed)
    n_samples = near_len + real_len + far_len
    rows = np.zeros((n_rows, n_samples))
    for r in range(n_rows):
        near = np.full(near_len, 0.8) + rng.normal(0, 0.001, near_len)  # flat artifact
        real = rng.uniform(0.3, 0.9, real_len)                          # real varying terrain
        far = np.full(far_len, 0.02) + rng.normal(0, 0.005, far_len)    # noise floor
        rows[r] = np.concatenate([near, real, far])
    return rows, near_len, near_len + real_len - 1


def test_detects_near_and_far_edges_on_synthetic_data():
    rows, expected_near, expected_far = _synthetic_channel()
    result = detect_echo_edges(rows)
    assert result is not None
    near_idx, far_idx = result
    assert abs(near_idx - expected_near) <= 2
    assert abs(far_idx - expected_far) <= 2


def test_returns_none_with_too_few_rows():
    rows, _, _ = _synthetic_channel(n_rows=2)
    assert detect_echo_edges(rows, min_rows=5) is None


def test_returns_none_for_empty_input():
    assert detect_echo_edges(np.zeros((10, 0))) is None
