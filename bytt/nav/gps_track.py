"""GPS track: converts lat/lon fixes into a local flat-earth XY track keyed
by ping index, for the waterfall's position readout and nav panel."""
import math


def format_degrees(value, pos_letter, neg_letter, decimals):
    letter = pos_letter if value >= 0 else neg_letter
    return f"{abs(value):.{decimals}f}°{letter}"


class GPSTrack:
    EARTH_R_M = 6371000.0

    def __init__(self):
        self.ref_lat   = None
        self.ref_lon   = None
        self.ping_idx  = []
        self.xs        = []
        self.ys        = []
        self._last_latlon = None

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
