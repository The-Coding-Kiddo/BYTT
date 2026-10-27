# GPS / Nav Track Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire GPS/nav position (from both `.bsf` playback and live packets) into the existing `GPSTrack` math, and display it in a new dockable panel — the "map that shows location and direction" feature.

**Architecture:** Both `PlaybackSource` and `parse_3101_body` are extended to attach a `meta['nav_fix']` dict (`{'lat', 'lon', 'heading', 'height'}` or `None`) to every ping they emit, using the same key shape regardless of source. `MainWindow` owns a `GPSTrack` instance (recreated per connection) plus a `_latest_heading` attribute, feeds every non-`None` fix into the track, and pushes updates into a new `GpsPanel(QDockWidget)` that plots the track with pyqtgraph.

**Tech Stack:** Python, PySide6, pyqtgraph, numpy — matches the rest of `bytt/`.

**Spec:** `docs/superpowers/specs/2026-09-20-gps-nav-panel-design.md` (this plan implements it in full; read it for the "why" behind each decision below).

## Global Constraints

- No `Co-Authored-By` trailers in any commit message, ever.
- Commit messages: concise, single-line, senior-engineer style.
- Real objects in tests, no mocks — matches the rest of this project's test suite.
- `extract_live_nav_fix`'s byte layout is inferred, not hardware-verified — code must carry a comment saying so (already true of the spec; carry it into the implementation).
- Do NOT `git add`/`git commit` anything under `docs/` — the user's own housekeeping commits added `/docs` to `.gitignore` and untracked the whole tree from git. Save this plan file to disk only; do not attempt to stage it.
- Every new/changed source file's tests must pass via `pytest` before moving to the next task.

---

### Task 1: `extract_live_nav_fix` + wire it into `parse_3101_body`

**Files:**
- Modify: `bytt/protocol/packets.py` (add `extract_live_nav_fix`, call it from `parse_3101_body`)
- Test: `tests/protocol/test_packets.py` (add tests; if this file doesn't exist, check `tests/protocol/` for the actual existing packets test file name and add to it instead)

**Interfaces:**
- Consumes: `pc.B3101_BODY_SIZE = 104` (already exists in `bytt/protocol/constants.py`); `extract_nav_fix`'s existing range-validation pattern (reused, not reinvented — see Step 3).
- Produces: `extract_live_nav_fix(body_and_data: bytes) -> dict | None` — `{'lat': float, 'lon': float, 'heading': float, 'height': float}` or `None`. `parse_3101_body`'s returned `meta` dict gains a `'nav_fix'` key holding this same value (or `None`), consumed by Task 4 (`MainWindow._on_ping_received`).

Byte layout within `body_and_data` (all little-endian, matches the spec's table):

| Offset | Field | Struct fmt |
|---|---|---|
| 80 | longitude | `<d` (8 bytes) |
| 88 | latitude | `<d` (8 bytes) |
| 96 | heading | `<f` (4 bytes) |
| 100 | height | `<f` (4 bytes) |

- [ ] **Step 1: Find the existing packets test file**

Run: `ls tests/protocol/`

Use whichever file already tests `bytt/protocol/packets.py` (e.g. `test_packets.py`). Add the new tests to it, following the existing style in that file (plain `def test_...():` functions, no fixtures needed — this is pure-function testing, no Qt involved).

- [ ] **Step 2: Write the failing tests**

```python
import struct
from bytt.protocol.packets import extract_live_nav_fix, parse_3101_body
from bytt.protocol import constants as pc


def _body_with_nav(lon, lat, heading, height, body_size=pc.B3101_BODY_SIZE):
    body = bytearray(body_size)
    struct.pack_into('<d', body, 80, lon)
    struct.pack_into('<d', body, 88, lat)
    struct.pack_into('<f', body, 96, heading)
    struct.pack_into('<f', body, 100, height)
    return bytes(body)


def test_extract_live_nav_fix_valid_data():
    body = _body_with_nav(lon=36.13, lat=41.47, heading=270.5, height=12.0)
    fix = extract_live_nav_fix(body)
    assert fix is not None
    assert abs(fix['lat'] - 41.47) < 1e-9
    assert abs(fix['lon'] - 36.13) < 1e-9
    assert abs(fix['heading'] - 270.5) < 1e-4
    assert abs(fix['height'] - 12.0) < 1e-4


def test_extract_live_nav_fix_rejects_out_of_range_lat_lon():
    body = _body_with_nav(lon=999.0, lat=999.0, heading=0.0, height=0.0)
    assert extract_live_nav_fix(body) is None


def test_extract_live_nav_fix_rejects_zero_zero():
    body = _body_with_nav(lon=0.0, lat=0.0, heading=0.0, height=0.0)
    assert extract_live_nav_fix(body) is None


def test_extract_live_nav_fix_rejects_truncated_data():
    body = bytes(50)  # shorter than offset 100 + 4 needed for height
    assert extract_live_nav_fix(body) is None


def test_parse_3101_body_includes_nav_fix_in_meta():
    n = 2
    body = bytearray(pc.B3101_BODY_SIZE + 2 * n * 2)
    syn = pc.B3101_SYNTIME_OFF
    struct.pack_into('<H', body, syn, 2024)  # year
    body[syn + 2] = 6   # month
    body[syn + 3] = 1   # day
    body[syn + 4] = 12  # hour
    body[syn + 5] = 0   # minute
    body[syn + 6] = 0   # second
    struct.pack_into('<I', body, pc.B3101_SAMPLELEN_OFF, n)
    struct.pack_into('<d', body, 80, 36.13)
    struct.pack_into('<d', body, 88, 41.47)
    struct.pack_into('<f', body, 96, 90.0)
    struct.pack_into('<f', body, 100, 5.0)

    _port, _stbd, meta = parse_3101_body(b'', bytes(body))
    assert meta['nav_fix'] is not None
    assert abs(meta['nav_fix']['lat'] - 41.47) < 1e-9


def test_parse_3101_body_nav_fix_none_when_out_of_range():
    n = 2
    body = bytearray(pc.B3101_BODY_SIZE + 2 * n * 2)
    struct.pack_into('<I', body, pc.B3101_SAMPLELEN_OFF, n)
    struct.pack_into('<d', body, 80, 999.0)
    struct.pack_into('<d', body, 88, 999.0)

    _port, _stbd, meta = parse_3101_body(b'', bytes(body))
    assert meta['nav_fix'] is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/protocol/test_packets.py -k nav_fix -v`
Expected: FAIL with `ImportError`/`AttributeError` (`extract_live_nav_fix` doesn't exist yet) and `KeyError: 'nav_fix'`.

- [ ] **Step 4: Implement `extract_live_nav_fix`, add to `parse_3101_body`**

In `bytt/protocol/packets.py`, add near `extract_nav_fix`:

```python
def extract_live_nav_fix(body_and_data):
    """Reads nav data embedded in a live 3101 packet's body -- SatCenter
    fills the sonar's own reserved2[24] field with lon/lat/heading/height
    (see hytem-data-protocol-v1.0.9.md / waterfall-vendor-sdk.md).

    UNVERIFIED against real hardware -- inferred from vendor docs only, no
    live towfish has ever produced this data. Treat with more suspicion
    than the .bsf file nav path (extract_nav_fix), which real recordings
    have confirmed correct."""
    if len(body_and_data) < 104:
        return None
    try:
        lon     = struct.unpack_from('<d', body_and_data, 80)[0]
        lat     = struct.unpack_from('<d', body_and_data, 88)[0]
        heading = struct.unpack_from('<f', body_and_data, 96)[0]
        height  = struct.unpack_from('<f', body_and_data, 100)[0]
    except struct.error:
        return None
    if not np.isfinite(lat) or not np.isfinite(lon):
        return None
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return None
    if abs(lat) < 1e-6 and abs(lon) < 1e-6:
        return None
    return {'lat': lat, 'lon': lon, 'heading': heading, 'height': height}
```

Then in `parse_3101_body`, after building `meta`, add the nav fix before `return`:

```python
    meta = {
        'ts':          f"{year}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}",
        'freq_kHz':    center_freq_hz / 1000.0,
        'n_samples':   n,
        'raw_sr':      sample_rate,
        'ping_number': ping_number,
        'max_range_m': sonar_range_cm / 100.0,
        'nav_fix':     extract_live_nav_fix(body_and_data),
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/protocol/test_packets.py -v`
Expected: PASS (all tests, including pre-existing ones — `parse_3101_body`'s signature/return shape is unchanged, only `meta` gained a key).

- [ ] **Step 6: Commit**

```bash
git add bytt/protocol/packets.py tests/protocol/test_packets.py
git commit -m "Add live-packet nav fix extraction to 3101 body parsing"
```

---

### Task 2: `PlaybackSource` nav-record wiring

**Files:**
- Modify: `bytt/net/playback_source.py`
- Test: `tests/net/test_playback_source.py`

**Interfaces:**
- Consumes: `load_all_pings(data) -> (pings, nav_records)` where `nav_records: list[(byte_offset, ping_index_before_it)]` (already returned, currently discarded — `bytt/bsf/file_io.py:17`); `extract_nav_fix(nav_bytes) -> (lat, lon, ts) | None` (`bytt/protocol/packets.py:28`); `pc.NAV_RECORD_SIZE = 156` (`bytt/protocol/constants.py:44`).
- Produces: every `meta` dict `PlaybackSource.ping_received` emits now includes `meta['nav_fix']` — either `None` (no nav record has occurred yet) or `{'lat': float, 'lon': float, 'heading': None, 'height': None}` (playback has no heading/height field, per the spec — these keys are always present but `None`, so downstream code never needs an `if 'heading' in nav_fix` check).

**Design:** `nav_records` is a list of `(byte_offset, ping_index_before_it)` sorted by file position (and therefore by `ping_index_before_it`, since the file is scanned linearly). During `_run`'s initial load, after `self._pings, self._nav_records = load_all_pings(self._data)`, walk `self._nav_records` and parse each one with `extract_nav_fix(self._data[offset:offset + pc.NAV_RECORD_SIZE])`, building a list of `(ping_index_before_it, parsed_fix_or_None)` — skip entries where parsing fails (leave the *previous* fix in effect, don't insert a `None` that would blank out an already-known position). Then, for any given `self._current_index`, "the most recent nav fix" is: the last entry in that list whose `ping_index_before_it <= self._current_index`. Since `_emit_current` already knows `self._current_index`, do a simple linear/bisect lookup there (a bisect is cleanest — reuse the pattern `GPSTrack.position_at_or_before` already uses, but this is a local list of `(ping_idx, fix)` tuples, not `GPSTrack` itself — `PlaybackSource` must not depend on `GPSTrack`, they're independent per the spec's file layering).

- [ ] **Step 1: Write the failing tests**

Add to `tests/net/test_playback_source.py` (uses the same `_build_ping_record`/struct-packing style already in that file):

```python
def _build_nav_record(lat, lon, ts=0.0):
    from bytt.protocol import constants as pc
    rec = bytearray(pc.NAV_RECORD_SIZE)
    struct.pack_into('<I', rec, 0, pc.BSF_RECORD_TYPE)
    struct.pack_into('<I', rec, 12, pc.NAV_RECORD_SIZE)
    struct.pack_into('<d', rec, pc.NAV_TS_OFFSET, ts)
    struct.pack_into('<d', rec, pc.NAV_LAT_OFFSET, lat)
    struct.pack_into('<d', rec, pc.NAV_LON_OFFSET, lon)
    return bytes(rec)


def test_pings_before_first_nav_record_have_no_nav_fix(tmp_path):
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(_build_bsf_bytes_n(n_pings=3))  # no nav records at all

    received = []
    source = PlaybackSource(str(bsf_path), pings_per_second=1000)
    source.ping_received.connect(lambda port, stbd, meta: received.append(meta))
    source.start()
    deadline = time.time() + 3.0
    while time.time() < deadline and len(received) < 3:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    source.stop()

    assert len(received) == 3
    assert all(m['nav_fix'] is None for m in received)


def test_nav_record_attaches_to_every_subsequent_ping_until_superseded(tmp_path):
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    ping = _build_ping_record(n_samples=4)
    nav1 = _build_nav_record(lat=41.0, lon=36.0)
    nav2 = _build_nav_record(lat=42.0, lon=37.0)
    # layout: ping, ping, nav1, ping, ping, nav2, ping
    body = ping + ping + nav1 + ping + ping + nav2 + ping
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(bytes(header) + body)

    received = []
    source = PlaybackSource(str(bsf_path), pings_per_second=1000)
    source.ping_received.connect(lambda port, stbd, meta: received.append(meta))
    source.start()
    deadline = time.time() + 3.0
    while time.time() < deadline and len(received) < 5:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    source.stop()

    assert len(received) == 5
    assert received[0]['nav_fix'] is None
    assert received[1]['nav_fix'] is None
    assert abs(received[2]['nav_fix']['lat'] - 41.0) < 1e-9
    assert abs(received[3]['nav_fix']['lat'] - 41.0) < 1e-9
    assert abs(received[4]['nav_fix']['lat'] - 42.0) < 1e-9
    assert received[4]['nav_fix']['heading'] is None
    assert received[4]['nav_fix']['height'] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/net/test_playback_source.py -k nav -v`
Expected: FAIL with `KeyError: 'nav_fix'`.

- [ ] **Step 3: Implement nav tracking in `PlaybackSource`**

In `bytt/net/playback_source.py`, add the import and the lookup logic:

```python
import bisect
from bytt.protocol.packets import extract_raw_channels, get_ping_meta, extract_nav_fix
from bytt.protocol import constants as pc
```

In `__init__`, add:
```python
        self._nav_fixes = []  # [(ping_index_before_it, {'lat', 'lon', 'heading', 'height'}), ...]
```

In `_run`, after loading pings:
```python
        try:
            self._data = path.read_bytes()
            self._pings, nav_records = load_all_pings(self._data)
        except Exception as e:
            self.status_changed.emit(f'playback error: {e}')
            return
        self._nav_fixes = []
        for offset, ping_idx_before in nav_records:
            fix = extract_nav_fix(self._data[offset:offset + pc.NAV_RECORD_SIZE])
            if fix is not None:
                lat, lon, _ts = fix
                self._nav_fixes.append(
                    (ping_idx_before, {'lat': lat, 'lon': lon, 'heading': None, 'height': None}))
```

Add a lookup helper and call it from `_emit_current`:

```python
    def _nav_fix_for(self, ping_index):
        if not self._nav_fixes:
            return None
        idx = bisect.bisect_right(self._nav_fixes, (ping_index, {}), key=lambda t: t[0])
        # bisect on tuples with a dict as the second element would compare
        # dicts on a tie -- use `key=` (Python 3.10+) to compare only the
        # ping-index component.
        if idx == 0:
            return None
        return self._nav_fixes[idx - 1][1]

    def _emit_current(self):
        offset, size = self._pings[self._current_index]
        ping = self._data[offset:offset + size]
        port_raw, stbd_raw = extract_raw_channels(ping)
        meta = get_ping_meta(ping)
        meta['nav_fix'] = self._nav_fix_for(self._current_index)
        self.ping_received.emit(port_raw, stbd_raw, meta)
        self.position_changed.emit(self._current_index, len(self._pings))
```

Note: `bisect.bisect_right(..., key=...)` requires Python 3.10+. Check the project's minimum Python version first — run `python3 --version` and check `pyproject.toml`/`setup.cfg` for a `python_requires`. If the project targets below 3.10, use a manual bisect instead:

```python
    def _nav_fix_for(self, ping_index):
        if not self._nav_fixes:
            return None
        keys = [t[0] for t in self._nav_fixes]
        idx = bisect.bisect_right(keys, ping_index)
        if idx == 0:
            return None
        return self._nav_fixes[idx - 1][1]
```

Prefer this manual-keys version regardless — it avoids the Python-version question entirely and is no less clear.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/net/test_playback_source.py -v`
Expected: PASS (all tests, including all pre-existing ones in the file).

- [ ] **Step 5: Commit**

```bash
git add bytt/net/playback_source.py tests/net/test_playback_source.py
git commit -m "Wire nav records into PlaybackSource ping metadata"
```

---

### Task 3: `GpsPanel` widget

**Files:**
- Create: `bytt/gui/gps_panel.py`
- Test: `tests/gui/test_gps_panel.py`

**Interfaces:**
- Consumes: `GPSTrack` (`bytt/nav/gps_track.py`) — `.has_data`, `.xs`, `.ys`, `.bounds()`, `.unproject(x, y)`, `._last_latlon` (tuple or `None` — used to know "is there a current fix to mark").
- Produces: `GpsPanel(gps_track: GPSTrack, parent=None)` (a `QDockWidget`), `.refresh(heading: float | None = None) -> None` — consumed by Task 4 (`MainWindow`).

**Design:** A `pyqtgraph.PlotWidget` with two `PlotDataItem`s (`_track_curve` — the accumulated line, `_current_marker` — a single-point scatter for "you are here") plus one `pg.ArrowItem` for heading, added/removed from the plot as needed (pyqtgraph has no clean "hide" for `ArrowItem` short of removing it, so track whether it's currently added). `refresh()` pulls `xs`/`ys` straight from `GPSTrack` (already in local flat-earth meters, exactly what the spec calls for) — no unit conversion needed for plotting; `unproject` is used only to relabel the axis ticks in degrees via a custom `pg.AxisItem` tick-string function.

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gui/test_gps_panel.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bytt.gui.gps_panel'`.

- [ ] **Step 3: Implement `GpsPanel`**

```python
"""Dockable GPS/nav track panel: plots the accumulated GPSTrack history,
the current position, and (live mode only) a heading arrow."""
import pyqtgraph as pg
from PySide6.QtWidgets import QDockWidget


class GpsPanel(QDockWidget):
    def __init__(self, gps_track, parent=None):
        super().__init__("GPS / Nav Track", parent)
        self.gps_track = gps_track

        self._plot_widget = pg.PlotWidget()
        self._plot = self._plot_widget.getPlotItem()
        self._plot.setAspectLocked(True)
        self._plot.showGrid(x=True, y=True, alpha=0.3)

        self._track_curve = self._plot.plot([], [], pen=pg.mkPen((80, 160, 255), width=2))
        self._current_marker = pg.ScatterPlotItem(
            size=12, brush=pg.mkBrush(255, 200, 0), pen=pg.mkPen(None))
        self._plot.addItem(self._current_marker)

        self._heading_arrow = pg.ArrowItem(angle=0, brush=pg.mkBrush(255, 60, 60))
        self._heading_visible = False

        self._x_axis = self._plot.getAxis('bottom')
        self._y_axis = self._plot.getAxis('left')
        self._x_axis.tickStrings = self._lon_tick_strings
        self._y_axis.tickStrings = self._lat_tick_strings

        self.setWidget(self._plot_widget)

    def _lon_tick_strings(self, values, scale, spacing):
        if self.gps_track.ref_lat is None:
            return [f"{v:.0f}" for v in values]
        out = []
        for v in values:
            _lat, lon = self.gps_track.unproject(v, 0.0)
            out.append(f"{lon:.5f}°")
        return out

    def _lat_tick_strings(self, values, scale, spacing):
        if self.gps_track.ref_lat is None:
            return [f"{v:.0f}" for v in values]
        out = []
        for v in values:
            lat, _lon = self.gps_track.unproject(0.0, v)
            out.append(f"{lat:.5f}°")
        return out

    def refresh(self, heading: float | None = None) -> None:
        track = self.gps_track
        self._track_curve.setData(track.xs, track.ys)

        if track.has_data:
            x, y = track.xs[-1], track.ys[-1]
            self._current_marker.setData([x], [y])
        else:
            self._current_marker.setData([], [])

        if heading is not None and track.has_data:
            x, y = track.xs[-1], track.ys[-1]
            self._heading_arrow.setStyle(angle=180 - heading)
            self._heading_arrow.setPos(x, y)
            if not self._heading_visible:
                self._plot.addItem(self._heading_arrow)
                self._heading_visible = True
        else:
            if self._heading_visible:
                self._plot.removeItem(self._heading_arrow)
                self._heading_visible = False
```

Note on the `180 - heading` angle conversion: `pg.ArrowItem`'s `angle` parameter points the arrow tip in the direction of `angle` degrees measured with pyqtgraph's own convention (0° = pointing left, counter-clockwise positive), while `heading` here is compass bearing (0° = North, clockwise positive). This mapping is a reasonable-effort conversion, not verified against real hardware output (heading itself is unverified per Task 1) — flag this to the reviewer as a "best effort, revisit with real data" note rather than treating it as exact.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gui/test_gps_panel.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bytt/gui/gps_panel.py tests/gui/test_gps_panel.py
git commit -m "Add GpsPanel widget for plotting the GPS track"
```

---

### Task 4: Wire `GPSTrack` + `GpsPanel` into `MainWindow`

**Files:**
- Modify: `bytt/gui/main_window.py`
- Test: `tests/gui/test_main_window.py`

**Interfaces:**
- Consumes: `GPSTrack()` (`bytt/nav/gps_track.py`), `GpsPanel(gps_track, parent=None)` + `.refresh(heading=None)` (Task 3), `meta['nav_fix']` (Tasks 1 & 2), `self.waterfall.rows_written` (already exists, `bytt/gui/waterfall_view.py:47`).
- Produces: `window.gps_track` (a `GPSTrack` instance, replaced on every `connect_towfish`/`open_playback_file`), `window._latest_heading`, `window.gps_panel` (a `GpsPanel`), a `View` menu entry toggling it.

- [ ] **Step 1: Write the failing tests**

Add to `tests/gui/test_main_window.py`:

```python
def test_main_window_has_gps_track_and_panel():
    window = MainWindow(AppConfig())
    from bytt.nav.gps_track import GPSTrack
    from bytt.gui.gps_panel import GpsPanel
    assert isinstance(window.gps_track, GPSTrack)
    assert isinstance(window.gps_panel, GpsPanel)
    assert window._latest_heading is None


def test_connect_towfish_and_open_playback_each_create_a_fresh_gps_track(tmp_path):
    from bytt.protocol import constants as pc
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(bytes(header))

    window = MainWindow(AppConfig())
    first_track = window.gps_track
    window.open_playback_file(str(bsf_path))
    assert window.gps_track is not first_track
    window.source.stop()

    second_track = window.gps_track
    window.open_playback_file(str(bsf_path))
    assert window.gps_track is not second_track
    window.source.stop()


def test_on_ping_received_feeds_nav_fix_into_gps_track_and_refreshes_panel():
    import numpy as np
    window = MainWindow(AppConfig())
    port_raw = np.linspace(0.0, 1.0, 512, dtype=np.float32)
    stbd_raw = np.linspace(1.0, 0.0, 512, dtype=np.float32)

    meta = {'nav_fix': {'lat': 41.47, 'lon': 36.13, 'heading': 90.0, 'height': 5.0}}
    window._on_ping_received(port_raw, stbd_raw, meta)

    assert window.gps_track.has_data
    assert window._latest_heading == 90.0


def test_on_ping_received_with_no_nav_fix_does_not_touch_gps_track():
    import numpy as np
    window = MainWindow(AppConfig())
    port_raw = np.linspace(0.0, 1.0, 512, dtype=np.float32)
    stbd_raw = np.linspace(1.0, 0.0, 512, dtype=np.float32)

    window._on_ping_received(port_raw, stbd_raw, {'nav_fix': None})
    assert not window.gps_track.has_data


def test_view_menu_has_gps_panel_toggle():
    window = MainWindow(AppConfig())
    view_menu = None
    for action in window.menuBar().actions():
        if action.text() == "&View":
            view_menu = action.menu()
    assert view_menu is not None
    texts = {a.text() for a in view_menu.actions()}
    assert any("GPS" in t for t in texts)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gui/test_main_window.py -k gps -v`
Expected: FAIL with `AttributeError: 'MainWindow' object has no attribute 'gps_track'`.

- [ ] **Step 3: Implement the wiring**

In `bytt/gui/main_window.py`, add imports:
```python
from bytt.nav.gps_track import GPSTrack
from bytt.gui.gps_panel import GpsPanel
```

In `__init__`, after the `ControlsPanel` block (after `view_menu.addAction(self.controls_panel.toggleViewAction())`), add:
```python
        self.gps_track = GPSTrack()
        self._latest_heading = None
        self.gps_panel = GpsPanel(self.gps_track, self)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.gps_panel)
        view_menu.addAction(self.gps_panel.toggleViewAction())
```

In `connect_towfish`, right after `self.disconnect_source()` (before creating the new `LiveClient`), add:
```python
        self.gps_track = GPSTrack()
        self._latest_heading = None
        self.gps_panel.gps_track = self.gps_track
```

Do the identical two-plus-one lines in `open_playback_file`, right after its own `self.disconnect_source()`.

In `_on_ping_received`, after the existing `self.waterfall.add_row(row_f32)` call (still inside the `try` block, so a bad nav fix can't break row display — matches the existing try/except's purpose):
```python
            nav_fix = meta.get('nav_fix')
            if nav_fix is not None:
                self.gps_track.add_fix(self.waterfall.rows_written, nav_fix['lat'], nav_fix['lon'])
                self._latest_heading = nav_fix.get('heading')
                self.gps_panel.refresh(heading=self._latest_heading)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gui/test_main_window.py -v`
Expected: PASS (all tests, including pre-existing ones).

- [ ] **Step 5: Full-suite sanity check**

Run: `pytest -q`
Expected: PASS, no regressions anywhere in the suite.

- [ ] **Step 6: Commit**

```bash
git add bytt/gui/main_window.py tests/gui/test_main_window.py
git commit -m "Wire GPSTrack and GpsPanel into MainWindow"
```

---

## Post-plan note (not a task, informational only)

The real recording at the repo root (`samsun kayalık.bsf`, gitignored, confirmed during spec-writing to contain 65 genuine nav fixes near 41.47°N/36.13°E) is a good manual sanity check once all four tasks are done: run the app, open that file, and confirm the GPS panel actually plots a track near Samsun, Turkey — but it is not a required part of any task's automated tests, since it isn't tracked in git and can't be assumed present in every environment.
