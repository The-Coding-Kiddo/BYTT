import pytest
from PySide6.QtWidgets import QApplication, QWidget
from bytt.gui.gps_panel import GpsPanel
from bytt.nav.gps_track import GPSTrack

@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app

@pytest.fixture
def owner():
    # Gives each panel a Qt-owned parent so teardown is deterministic
    # (parentless top-level QDockWidgets built and dropped across many
    # tests in one process is a known pyqtgraph GC/teardown hazard).
    w = QWidget()
    yield w
    w.deleteLater()
    QApplication.processEvents()

def test_gps_panel_constructs_with_empty_track(owner):
    track = GPSTrack()
    panel = GpsPanel(track, owner)
    panel.refresh()  # must not raise on an empty track


def test_gps_panel_refresh_updates_track_curve_data(owner):
    track = GPSTrack()
    track.add_fix(0, 41.47, 36.13)
    track.add_fix(1, 41.48, 36.14)
    panel = GpsPanel(track, owner)
    panel.refresh()

    xs, ys = panel._track_curve.getData()
    assert len(xs) == 2
    assert len(ys) == 2


def test_gps_panel_heading_arrow_shown_only_when_heading_given(owner):
    track = GPSTrack()
    track.add_fix(0, 41.47, 36.13)
    panel = GpsPanel(track, owner)

    panel.refresh(heading=None)
    assert not panel._heading_visible

    panel.refresh(heading=90.0)
    assert panel._heading_visible

    panel.refresh(heading=None)
    assert not panel._heading_visible


def test_speed_and_heading_label_updates_on_refresh(owner):
    track = GPSTrack()
    track.add_fix(0, 41.47, 36.13)
    panel = GpsPanel(track, owner)
    panel.refresh(heading=270.0)
    assert "270" in panel.speed_heading_label.text()
    assert "kn" in panel.speed_heading_label.text()


def test_add_waypoint_and_it_appears_in_the_list(owner):
    track = GPSTrack()
    track.add_fix(0, 41.47, 36.13)
    panel = GpsPanel(track, owner)
    track.add_waypoint(41.48, 36.14, "wreck")
    panel.refresh()
    assert panel.waypoint_list.count() == 1
    assert "wreck" in panel.waypoint_list.item(0).text()


def test_remove_selected_waypoint(owner):
    track = GPSTrack()
    track.add_fix(0, 41.47, 36.13)
    panel = GpsPanel(track, owner)
    track.add_waypoint(41.48, 36.14, "wreck")
    panel.refresh()
    panel.waypoint_list.setCurrentRow(0)
    panel._on_remove_waypoint_clicked()
    assert track.waypoints == []
    assert panel.waypoint_list.count() == 0


def test_swath_coverage_survives_a_revisited_corridor(owner):
    # Regression test: a single self-intersecting shape spanning the whole
    # track (the old approach) gets "erased" wherever the boat re-scans a
    # spot, because Qt's default even-odd fill treats double-covered area
    # as outside the shape. Per-segment quads with winding fill must not
    # have this failure -- overlapping coverage stays filled.
    track = GPSTrack()
    lat, lon = 41.30, 36.33
    track.add_fix(0, lat, lon)
    track.add_swath_edge(track.xs[-1], track.ys[-1], 0.0, 50.0, near_range_m=5.0)
    for i in range(1, 6):
        lat += 0.0005
        track.add_fix(i, lat, lon)
        track.add_swath_edge(track.xs[-1], track.ys[-1], 0.0, 50.0, near_range_m=5.0)
    for i in range(6, 11):  # turn around, re-scan the same corridor
        lat -= 0.0005
        track.add_fix(i, lat, lon)
        track.add_swath_edge(track.xs[-1], track.ys[-1], 180.0, 50.0, near_range_m=5.0)

    panel = GpsPanel(track, owner)
    panel.refresh()

    from PySide6.QtCore import QPointF
    stbd_path = panel._swath_stbd_patch.path()
    port_path = panel._swath_port_patch.path()
    assert not stbd_path.isEmpty()
    assert not port_path.isEmpty()
    mid_y = track.ys[3]
    assert stbd_path.contains(QPointF(20.0, mid_y)) or port_path.contains(QPointF(-20.0, mid_y))
