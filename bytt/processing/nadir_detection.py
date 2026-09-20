"""Finds the real near (nadir-gap) and far (noise-floor) edges of usable
sonar echo from the actual amplitude data, instead of guessing.

The nadir gap (directly under the towfish) is dominated by the water-column
echo -- a fixed artifact that looks nearly identical from ping to ping. Real
seabed, even weakly reflective, varies ping to ping because the boat moves
over different terrain. So the near edge is where ping-to-ping variance
rises out of that flat artifact. The far edge is simpler: past the sonar's
real range, there's nothing left but noise, so mean amplitude drops."""
import numpy as np


def detect_echo_edges(channel_rows_near_to_far, variance_threshold=0.01,
                       noise_floor=0.05, min_rows=5):
    """channel_rows_near_to_far: 2D array-like (n_rows, n_samples), one row
    per ping, samples ordered from nearest-the-boat (index 0) to farthest
    (index -1), for a single channel.

    Returns (near_edge_idx, far_edge_idx) as sample indices into that array,
    or None if there aren't enough rows buffered yet to make a reliable call."""
    arr = np.asarray(channel_rows_near_to_far, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[0] < min_rows or arr.shape[1] == 0:
        return None

    col_std = arr.std(axis=0)
    col_mean = arr.mean(axis=0)
    n = len(col_std)

    near_edge_idx = n - 1
    for i in range(n):
        if col_std[i] > variance_threshold:
            near_edge_idx = i
            break

    far_edge_idx = 0
    for i in range(n - 1, -1, -1):
        if col_mean[i] > noise_floor:
            far_edge_idx = i
            break

    return near_edge_idx, far_edge_idx
