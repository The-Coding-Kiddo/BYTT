from bytt.nav.gps_track import GPSTrack, format_degrees

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
