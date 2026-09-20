import pytest
from PySide6.QtWidgets import QApplication
from bytt.gui.gps_panel import GpsPanel
from bytt.nav.gps_track import GPSTrack

@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app

def test_gps_panel_constructs_with_empty_track():
    track = GPSTrack()
    panel = GpsPanel(track)
    panel.refresh()  # must not raise on an empty track


def test_gps_panel_refresh_updates_track_curve_data():
    track = GPSTrack()
    track.add_fix(0, 41.47, 36.13)
    track.add_fix(1, 41.48, 36.14)
    panel = GpsPanel(track)
    panel.refresh()

    xs, ys = panel._track_curve.getData()
    assert len(xs) == 2
    assert len(ys) == 2


def test_gps_panel_heading_arrow_shown_only_when_heading_given():
    track = GPSTrack()
    track.add_fix(0, 41.47, 36.13)
    panel = GpsPanel(track)

    panel.refresh(heading=None)
    assert not panel._heading_visible

    panel.refresh(heading=90.0)
    assert panel._heading_visible

    panel.refresh(heading=None)
    assert not panel._heading_visible
