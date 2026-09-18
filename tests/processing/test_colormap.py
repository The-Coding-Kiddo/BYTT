import numpy as np
from bytt.processing import colormap

def test_lut_names_and_palettes_match():
    assert set(colormap.LUT_NAMES) == set(colormap.LUT_PALETTES.keys())
    for name in colormap.LUT_NAMES:
        lut = colormap.LUT_PALETTES[name]
        assert lut.shape == (256, 3)
        assert lut.dtype == np.uint8

def test_build_lut_endpoints():
    stops = [(0.0, colormap.lut_stop(0, 0, 0)), (1.0, colormap.lut_stop(255, 255, 255))]
    lut = colormap.build_lut(stops)
    assert list(lut[0]) == [0, 0, 0]
    assert list(lut[255]) == [255, 255, 255]

def test_build_combined_lut_applies_gain_and_gamma():
    grayscale = colormap.LUT_PALETTES['Grayscale']
    combined = colormap.build_combined_lut(gain=1.0, gamma=1.0, colour_lut=grayscale)
    assert combined.shape == (256, 3)
    assert list(combined[0]) == [0, 0, 0]
    assert list(combined[255]) == [255, 255, 255]
