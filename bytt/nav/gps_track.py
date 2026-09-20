"""GPS track: converts lat/lon fixes into a local flat-earth XY track keyed
by ping index, for the waterfall's position readout and nav panel."""
import math
import time


def format_degrees(value, pos_letter, neg_letter, decimals):
    letter = pos_letter if value >= 0 else neg_letter
    return f"{abs(value):.{decimals}f}°{letter}"


_EARTH_R_M = 6371000.0


def haversine_distance_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * _EARTH_R_M * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    y = math.sin(dlambda) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlambda)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


class GPSTrack:
    EARTH_R_M = 6371000.0

    def __init__(self):
        self.ref_lat   = None
        self.ref_lon   = None
        self.ping_idx  = []
        self.xs        = []
        self.ys        = []
        self._last_latlon = None
        self.waypoints = []  # [{'id', 'lat', 'lon', 'name'}, ...]
        self._next_waypoint_id = 1
        self.speed_mps = 0.0
        self._last_fix_time = None
        # Near (nadir-gap) / far (usable-range) sonar reach in effect at
        # each track point, in meters. Parallel to xs/ys.
        self.swath_near_range_m = []
        self.swath_far_range_m = []

    def _project(self, lat, lon):
        if self.ref_lat is None:
            self.ref_lat, self.ref_lon = lat, lon
        dlat = math.radians(lat - self.ref_lat)
        dlon = math.radians(lon - self.ref_lon)
        y = dlat * self.EARTH_R_M
        x = dlon * self.EARTH_R_M * math.cos(math.radians(self.ref_lat))
        return x, y

    def unproject(self, x, y):
        lat = self.ref_lat + math.degrees(y / self.EARTH_R_M)
        coslat = max(0.01, math.cos(math.radians(self.ref_lat)))
        lon = self.ref_lon + math.degrees(x / (self.EARTH_R_M * coslat))
        return lat, lon

    def add_fix(self, ping_idx, lat, lon):
        # The wire protocol carries no speed field at all -- even the
        # original Qt app never solved this, it hardcodes speed to 0.0
        # (see HydroMainWindow::setBoatPosition). We derive it ourselves
        # from consecutive fixes' wall-clock arrival time. Accurate in live
        # mode (arrival time == real time); in fast-forwarded/instant
        # playback this reads as playback speed, not the original survey
        # speed -- an accepted simplification, not a bug.
        now = time.monotonic()
        if self._last_latlon is not None and self._last_fix_time is not None:
            elapsed = now - self._last_fix_time
            if elapsed > 0:
                dist = haversine_distance_m(self._last_latlon[0], self._last_latlon[1], lat, lon)
                self.speed_mps = dist / elapsed
        self._last_fix_time = now
        x, y = self._project(lat, lon)
        self.ping_idx.append(ping_idx)
        self.xs.append(x)
        self.ys.append(y)
        self._last_latlon = (lat, lon)

    @property
    def has_data(self):
        return len(self.xs) > 0

    def bounds(self):
        if not self.xs:
            return (-10, 10, -10, 10)
        return (min(self.xs), max(self.xs), min(self.ys), max(self.ys))

    def position_at_or_before(self, ping_idx):
        if not self.ping_idx:
            return None
        lo, hi = 0, len(self.ping_idx) - 1
        best = None
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.ping_idx[mid] <= ping_idx:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        if best is None:
            return None
        return self.xs[best], self.ys[best], self.ping_idx[best], best

    def add_swath_range(self, near_range_m, far_range_m):
        """Records the near (nadir-gap) / far (usable-range) sonar reach in
        effect for the most recently added fix. Call once per add_fix call
        to keep this list positionally aligned with xs/ys.

        far_range_m (or near_range_m) may be None when it hasn't actually
        been measured for this fix yet (e.g. the caller's own detector
        needs more buffered data first) -- swath_quads() then draws no
        coverage for this point rather than assuming the full configured
        range was reached before that's been checked.

        Deliberately does NOT take a heading. An earlier version placed
        each point's swath boundary using an externally-supplied heading
        estimate (course-made-good in playback, since .bsf carries no real
        heading) -- but a quad connecting point i to point i+1 then used
        TWO DIFFERENT heading estimates, one per point, which could rotate
        past the centerline and paint straight across the real nadir gap
        whenever the course changed between them. swath_quads() below fixes
        this by deriving direction from the track's own recorded positions
        instead, which is unambiguous and needs no heading at all."""
        self.swath_near_range_m.append(near_range_m)
        self.swath_far_range_m.append(far_range_m)

    def _swath_point_direction(self, i, n):
        """Unit travel direction AT point i, as the average of the incoming
        segment (i-1 -> i) and outgoing segment (i -> i+1) directions --
        the standard line-join technique (a miter join). This is what lets
        the quad ending at point i and the quad starting at it agree on
        exactly where i's near/far corners are.

        An earlier version gave each quad its own segment's direction
        independently, with no such agreement -- fine on a straight line,
        but on a turn the two quads meeting at a shared point used two
        different directions, and since the far edge is much farther from
        the track than the near edge, that same angular gap opens into a
        visibly wide wedge cut out at the far edge, worst exactly where the
        boat turns sharpest. Returns None only if no direction at all can
        be determined (a single isolated point)."""
        vecs = []
        if i > 0:
            dx, dy = self.xs[i] - self.xs[i - 1], self.ys[i] - self.ys[i - 1]
            length = math.hypot(dx, dy)
            if length > 1e-9:
                vecs.append((dx / length, dy / length))
        if i < n - 1:
            dx, dy = self.xs[i + 1] - self.xs[i], self.ys[i + 1] - self.ys[i]
            length = math.hypot(dx, dy)
            if length > 1e-9:
                vecs.append((dx / length, dy / length))
        if not vecs:
            return None
        ux = sum(v[0] for v in vecs) / len(vecs)
        uy = sum(v[1] for v in vecs) / len(vecs)
        norm = math.hypot(ux, uy)
        if norm < 1e-9:
            # The two segments point in very nearly opposite directions (a
            # near-180-degree reversal) -- their average cancels out, so
            # there's no well-defined single direction. Fall back to
            # whichever single segment is available rather than a
            # degenerate zero vector.
            return vecs[-1]
        return ux / norm, uy / norm

    def swath_quads(self):
        """Yields (port_quad, stbd_quad) for each track segment -- each a
        list of 4 (x, y) corners: near_i, near_{i+1}, far_{i+1}, far_i.

        Both endpoints of a segment use that POINT's own shared direction
        (see _swath_point_direction), not the segment's direction -- this
        is what lets neighboring quads share exact corner points instead of
        leaving a wedge-shaped gap between them at every turn."""
        n = min(len(self.xs), len(self.swath_near_range_m))
        corners = [None] * n
        for i in range(n):
            near_m, far_m = self.swath_near_range_m[i], self.swath_far_range_m[i]
            if near_m is None or far_m is None:
                continue  # not measured yet for this point -- no coverage claim
            d = self._swath_point_direction(i, n)
            if d is None:
                continue
            sx, sy = d[1], -d[0]  # starboard: travel direction rotated -90 degrees
            x, y = self.xs[i], self.ys[i]
            corners[i] = {
                'stbd_near': (x + sx * near_m, y + sy * near_m),
                'stbd_far':  (x + sx * far_m,  y + sy * far_m),
                'port_near': (x - sx * near_m, y - sy * near_m),
                'port_far':  (x - sx * far_m,  y - sy * far_m),
            }
        for i in range(n - 1):
            c0, c1 = corners[i], corners[i + 1]
            if c0 is None or c1 is None:
                continue
            stbd_quad = [c0['stbd_near'], c1['stbd_near'], c1['stbd_far'], c0['stbd_far']]
            port_quad = [c0['port_near'], c1['port_near'], c1['port_far'], c0['port_far']]
            yield port_quad, stbd_quad

    def add_waypoint(self, lat, lon, name=""):
        wp_id = self._next_waypoint_id
        self._next_waypoint_id += 1
        self.waypoints.append({'id': wp_id, 'lat': lat, 'lon': lon, 'name': name})
        return wp_id

    def remove_waypoint(self, wp_id):
        self.waypoints = [w for w in self.waypoints if w['id'] != wp_id]

    def waypoint_xy(self, wp_id):
        """Projects a waypoint into the same local flat-earth xy the track uses."""
        wp = next((w for w in self.waypoints if w['id'] == wp_id), None)
        if wp is None:
            return None
        return self._project(wp['lat'], wp['lon'])

    def bearing_distance_to_waypoint(self, wp_id):
        """(distance_m, bearing_deg) from the current (last) position to the
        given waypoint, or None if there is no current position or no such
        waypoint."""
        if self._last_latlon is None:
            return None
        wp = next((w for w in self.waypoints if w['id'] == wp_id), None)
        if wp is None:
            return None
        lat0, lon0 = self._last_latlon
        dist = haversine_distance_m(lat0, lon0, wp['lat'], wp['lon'])
        brg = bearing_deg(lat0, lon0, wp['lat'], wp['lon'])
        return dist, brg

    def history_upto(self, ping_idx):
        if not self.ping_idx:
            return 0
        lo, hi = 0, len(self.ping_idx)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.ping_idx[mid] <= ping_idx:
                lo = mid + 1
            else:
                hi = mid
        return lo
