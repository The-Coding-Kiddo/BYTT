from bytt.nav.gps_track import GPSTrack, format_degrees, haversine_distance_m, bearing_deg

def test_haversine_distance_known_points():
    # ~1 degree of latitude is ~111.19 km
    d = haversine_distance_m(0.0, 0.0, 1.0, 0.0)
    assert 111_000 < d < 111_400

def test_bearing_due_north():
    b = bearing_deg(0.0, 0.0, 1.0, 0.0)
    assert abs(b - 0.0) < 0.01

def test_bearing_due_east():
    b = bearing_deg(0.0, 0.0, 0.0, 1.0)
    assert abs(b - 90.0) < 0.01

def test_add_and_remove_waypoint():
    track = GPSTrack()
    wp_id = track.add_waypoint(41.5, 36.2, "wreck")
    assert len(track.waypoints) == 1
    track.remove_waypoint(wp_id)
    assert track.waypoints == []

def test_bearing_distance_to_waypoint_requires_current_position():
    track = GPSTrack()
    wp_id = track.add_waypoint(41.31, 36.33, "mark")
    assert track.bearing_distance_to_waypoint(wp_id) is None
    track.add_fix(0, 41.30, 36.33)
    result = track.bearing_distance_to_waypoint(wp_id)
    assert result is not None
    dist, brg = result
    assert dist > 0
    assert abs(brg - 0.0) < 1.0  # due north-ish, since only lat differs


def test_first_fix_becomes_origin():
    track = GPSTrack()
    track.add_fix(ping_idx=0, lat=41.30, lon=36.33)
    assert track.has_data
    x, y, ping_idx, i = track.position_at_or_before(0)
    assert (x, y) == (0.0, 0.0)
    assert ping_idx == 0

def test_position_at_or_before_returns_none_when_empty():
    assert GPSTrack().position_at_or_before(5) is None

def test_history_upto_counts_fixes_at_or_before():
    track = GPSTrack()
    track.add_fix(0, 41.30, 36.33)
    track.add_fix(5, 41.31, 36.34)
    track.add_fix(10, 41.32, 36.35)
    assert track.history_upto(7) == 2
    assert track.history_upto(10) == 3
    assert track.history_upto(-1) == 0

def test_format_degrees():
    assert format_degrees(41.3, 'N', 'S', 2) == "41.30°N"
    assert format_degrees(-36.3, 'E', 'W', 1) == "36.3°W"
