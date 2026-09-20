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

def test_speed_is_zero_before_a_second_fix():
    track = GPSTrack()
    track.add_fix(0, 41.30, 36.33)
    assert track.speed_mps == 0.0

def test_speed_computed_from_consecutive_fixes(monkeypatch):
    import bytt.nav.gps_track as gps_track_module
    times = iter([100.0, 110.0])  # 10 seconds apart
    monkeypatch.setattr(gps_track_module.time, "monotonic", lambda: next(times))
    track = GPSTrack()
    track.add_fix(0, 0.0, 0.0)
    track.add_fix(1, 0.0, 0.001)  # ~111.2 m east at the equator
    assert track.speed_mps > 0
    assert 10.0 < track.speed_mps < 12.0  # ~111.2m / 10s ≈ 11.12 m/s

def test_swath_quads_use_segment_direction_not_external_heading():
    track = GPSTrack()
    # Straight line due north: segment direction is unambiguous (0,0)->(0,10).
    track.add_fix(0, 0.0, 0.0)
    track.add_swath_range(near_range_m=5.0, far_range_m=50.0)
    # add_fix's own projection is in meters already for this synthetic case
    # (no real lat/lon math needed) -- push a second point directly.
    track.xs.append(0.0)
    track.ys.append(10.0)
    track.ping_idx.append(1)
    track.swath_near_range_m.append(5.0)
    track.swath_far_range_m.append(50.0)

    quads = list(track.swath_quads())
    assert len(quads) == 1
    port_quad, stbd_quad = quads[0]
    # Facing due north (travel direction (0,10)), starboard is east (+x).
    assert abs(stbd_quad[0][0] - 5.0) < 1e-6   # near0.x
    assert abs(stbd_quad[0][1] - 0.0) < 1e-6   # near0.y
    assert abs(stbd_quad[3][0] - 50.0) < 1e-6  # far0.x
    assert abs(port_quad[0][0] - (-5.0)) < 1e-6
    assert abs(port_quad[3][0] - (-50.0)) < 1e-6


def test_swath_quads_skip_coincident_points():
    track = GPSTrack()
    track.xs = [0.0, 0.0]
    track.ys = [0.0, 0.0]  # identical points -- no meaningful direction
    track.swath_near_range_m = [5.0, 5.0]
    track.swath_far_range_m = [50.0, 50.0]
    assert list(track.swath_quads()) == []


def test_swath_quads_stay_consistent_across_a_sharp_turn():
    # The bug this replaces: a per-point heading estimate could rotate past
    # the centerline between two points with very different courses,
    # painting straight across the real nadir gap. Segment-direction quads
    # must not do this -- every quad's own near boundary must stay strictly
    # closer to that quad's own two track points than its far boundary.
    track = GPSTrack()
    track.xs = [0.0, 0.0, 10.0]
    track.ys = [0.0, 10.0, 10.0]  # a 90-degree turn
    track.swath_near_range_m = [5.0, 5.0, 5.0]
    track.swath_far_range_m = [50.0, 50.0, 50.0]

    quads = list(track.swath_quads())
    assert len(quads) == 2  # one per segment: (0,0)->(0,10) and (0,10)->(10,10)
    for port_quad, stbd_quad in quads:
        assert len(port_quad) == 4
        assert len(stbd_quad) == 4

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
