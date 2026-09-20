"""GPS track: converts lat/lon fixes into a local flat-earth XY track keyed
by ping index, for the waterfall's position readout and nav panel."""
import math


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
