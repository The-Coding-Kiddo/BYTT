"""Colour lookup tables for the waterfall display."""
import numpy as np


def lut_stop(r, g, b):
    return np.array([r, g, b], dtype=np.float32)


def build_lut(stops):
    lut = np.zeros((256, 3), dtype=np.uint8)
    for i in range(256):
        t = i / 255.0
        for j in range(len(stops) - 1):
            t0, c0 = stops[j]
            t1, c1 = stops[j + 1]
            if t0 <= t <= t1:
                alpha = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
                lut[i] = np.clip(c0 + alpha * (c1 - c0), 0, 255).astype(np.uint8)
                break
    return lut


_LUT_STOPS = {
    "Amber": [
        (0.00, lut_stop(0,   0,   0)),
        (0.35, lut_stop(80,  40,   0)),
        (0.60, lut_stop(210, 140,  10)),
        (0.80, lut_stop(255, 210,  60)),
        (1.00, lut_stop(255, 255, 255)),
    ],
    "Grayscale": [
        (0.00, lut_stop(0,   0,   0)),
        (1.00, lut_stop(255, 255, 255)),
    ],
    "Ice": [
        (0.00, lut_stop(0,   0,   10)),
        (0.35, lut_stop(0,   40,  95)),
        (0.65, lut_stop(0,  150, 210)),
        (0.85, lut_stop(140, 225, 255)),
        (1.00, lut_stop(255, 255, 255)),
    ],
    "Copper": [
        (0.00, lut_stop(0,   0,   0)),
        (0.40, lut_stop(90,  45,  25)),
        (0.70, lut_stop(200, 120,  70)),
        (0.90, lut_stop(240, 190, 140)),
        (1.00, lut_stop(255, 255, 255)),
    ],
    "Phosphor": [
        (0.00, lut_stop(0,   5,   0)),
        (0.35, lut_stop(0,   60,  15)),
        (0.65, lut_stop(30, 180,  40)),
        (0.85, lut_stop(150, 255, 130)),
        (1.00, lut_stop(255, 255, 255)),
    ],
}
LUT_NAMES    = list(_LUT_STOPS.keys())
LUT_PALETTES = {name: build_lut(stops) for name, stops in _LUT_STOPS.items()}


def build_combined_lut(gain, gamma, colour_lut):
    """Pre-compute gain + gamma + palette into a single 256x3 uint8 LUT so
    per-pixel colour mapping is a single gather: rgb = combined_lut[img8]."""
    x = np.arange(256, dtype=np.float32) / 255.0
    v = np.clip(x * gain, 0.0, 1.0)
    v = np.power(v, gamma)
    idx = (v * 255.0).astype(np.uint8)
    return colour_lut[idx]
