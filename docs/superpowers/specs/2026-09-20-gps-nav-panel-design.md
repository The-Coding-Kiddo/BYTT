---
title: GPS / nav track panel — design
status: draft — approved by user 2026-09-20
---

## Why this document exists

`GPSTrack` (`bytt/nav/gps_track.py`) — the math for turning lat/lon fixes into a local flat-earth
track, keyed by ping index — was built and tested in the very first architecture plan. Nothing in
the app has ever called it: `extract_nav_fix` (the `.bsf`-file nav parser) is never invoked, and
there is no live-packet nav parser at all. This spec closes that gap end to end: extracting
position from both playback and live sources, feeding `GPSTrack`, and displaying it in a real
dockable panel — the "map that shows location and direction" from the original app.

## Confirmed before design: real data exists

The repo's actual recording (`samsun kayalık.bsf`) contains 65 real nav records with valid
coordinates (~41.47°N, 36.13°E — Samsun, Turkey), confirmed by running `load_all_pings` +
`extract_nav_fix` against it directly. Playback nav has real data to build and test against today.

Live nav does **not** have an equivalent: no live towfish has ever been connected, and per
`architecture.md`, the live packet's position fields have never been observed in practice — the
byte layout below is inference from vendor documentation, not confirmed against real hardware.
This is called out explicitly rather than treated as settled fact, and is expected to need
correction once real hardware is available.

## Scope: both playback and live nav (confirmed with user)

Both paths are in scope for this spec, despite live-mode parsing being new/unverified code —
explicitly requested rather than deferring live to a later pass.

## Data flow

### Playback (`.bsf` files) — wiring already-tested code

`extract_nav_fix(nav_bytes)` already correctly parses the file's separate 156-byte nav records
(`NAV_RECORD_SIZE`, `NAV_LAT_OFFSET=80`, `NAV_LON_OFFSET=88`, `NAV_TS_OFFSET=64`,
`NAV_FIELD_FMT='<d'` — all in `bytt/protocol/constants.py`) into `(lat, lon, ts)`. `load_all_pings`
already returns `nav_records: list[(offset, ping_index_before_it)]` — currently discarded.

`PlaybackSource` gains logic to walk `nav_records` alongside `pings` as it plays back: it tracks
"the most recent nav fix encountered so far" and attaches it (as `meta['nav_fix'] = {'lat':...,
'lon':..., 'heading': None, 'height': None}`) to every ping's `meta` dict from that point forward,
until a later nav record replaces it. Pings before the first nav record get `meta['nav_fix'] =
None`.

### Live (towfish) — new, explicitly unverified code

Per `architecture.md` and `waterfall-vendor-sdk.md`: the 3101 packet's 104-byte body ends with 24
bytes SatCenter fills with navigation data, replacing the sonar's own `reserved2[24]` field. Byte
layout within `body_and_data` (offsets already established relative to `B3101_BODY_SIZE=104`,
since `B3101_SAMPLERATE_OFF=72` (4 bytes) + `B3101_SAMPLELEN_OFF=76` (4 bytes) end at offset 80,
and the nav region fills the remaining 24 bytes to reach the 104-byte body size):

| Offset (within body_and_data) | Field | Type |
|---|---|---|
| 80 | longitude | `<d` (8 bytes) |
| 88 | latitude | `<d` (8 bytes) |
| 96 | heading | `<f` (4 bytes) |
| 100 | height | `<f` (4 bytes) |

New function `extract_live_nav_fix(body_and_data: bytes) -> dict | None` in
`bytt/protocol/packets.py`, returning `{'lat':..., 'lon':..., 'heading':..., 'height':...}` (same
key shape as the playback path's `meta['nav_fix']`, so downstream code never needs to know which
source it came from) or `None` on insufficient length or out-of-range lat/lon (same validation
`extract_nav_fix` already applies — reused, not reinvented). `parse_3101_body` calls this and adds
the result to its returned `meta` dict as `meta['nav_fix']`.

**This byte layout is a documented inference, not a verified fact** — the code carries a comment
saying so, and this is tracked as a known area to revisit once real hardware/SatCenter is
available to test against (matching the project's existing convention of flagging inferred-vs-
confirmed facts throughout `BYTT docs/`).

### Feeding `GPSTrack`

`MainWindow` owns a `self.gps_track = GPSTrack()` instance. In `_on_ping_received`, after the
existing waterfall row handling, if `meta.get('nav_fix')` is not `None`, call
`self.gps_track.add_fix(ping_idx, nav_fix['lat'], nav_fix['lon'])` — using
`self.waterfall.rows_written` as `ping_idx` (an already-available running counter, present in both
live and playback modes; playback's `meta` has no built-in ping-number field, so this is the
existing value already used for exactly this "current position in the stream" purpose elsewhere).

**Heading is not part of `GPSTrack`'s data model** (it only stores position history, and changing
its already-tested contract is out of scope here). Heading is transient, "what direction right
now," not track history — `MainWindow` keeps it as a plain instance attribute,
`self._latest_heading = nav_fix.get('heading')`, updated on every ping and passed directly to
`self.gps_panel.refresh(heading=self._latest_heading)` alongside the `GPSTrack` state.

A **fresh `GPSTrack()`** is created every time `connect_towfish` or `open_playback_file` runs —
otherwise a new session's fixes would get projected against a stale reference point from a
previous session, producing nonsense coordinates.

## The panel

A new `bytt/gui/gps_panel.py`, `GpsPanel(QDockWidget)`, containing a `pyqtgraph.PlotWidget`:

- The accumulated track: a line/scatter `PlotDataItem` built from `GPSTrack.xs`/`GPSTrack.ys`.
- Current position: a separate, visually distinct marker (different color/size) at the latest fix.
- Heading arrow: a small rotated marker at the current position, shown **only when heading is
  available** (`nav_fix['heading'] is not None`) — in practice this means it never appears during
  `.bsf` playback (the file format has no heading field) and only appears in live mode, once/if
  live nav parsing turns out to be correct.
- Axis tick labels in real degrees, computed via `GPSTrack.unproject(x, y)` — matches the
  original's degree-labeled grid rather than showing raw local meters.

**`MainWindow` owns `GPSTrack`** — `GpsPanel` never creates or holds its own copy. `GpsPanel`
receives a reference to the shared `GPSTrack` instance at construction
(`GpsPanel(gps_track, parent=...)`) and exposes one method, `refresh(heading: float | None = None)`,
which redraws the plot from `GPSTrack`'s current state (`xs`/`ys`/latest fix/etc.) and draws the
heading arrow only if `heading` is not `None`. `MainWindow` calls
`self.gps_panel.refresh(heading=self._latest_heading)` immediately after every
`self.gps_track.add_fix(...)` call in `_on_ping_received`. This keeps a single source of truth for
track data instead of two copies drifting apart.

Docked via `self.addDockWidget(...)` and a `View` menu toggle (`gps_panel.toggleViewAction()`),
same pattern as the enhancement `ControlsPanel` — visible by default.

## Error handling

`extract_nav_fix` and `extract_live_nav_fix` both already return `None` (never raise) on malformed
or out-of-range data — a bad or missing nav fix simply means that ping doesn't update the track;
it never interrupts ping/row processing, which continues via the existing try/except in
`_on_ping_received`.

## Testing approach

Real objects, no mocks, consistent with the rest of this project:

- `extract_live_nav_fix`: synthetic packet bytes at the documented offsets — a valid fix, an
  out-of-range lat/lon, and truncated/insufficient-length data.
- `PlaybackSource`: verify a nav record occurring between two pings gets correctly attached to
  every subsequent ping's `meta['nav_fix']` until superseded — using the real
  `samsun kayalık.bsf` file (already confirmed to contain genuine nav records) as one fixture, plus
  a small synthetic fixture for the "no nav records at all" edge case.
- `GpsPanel`: feeding fixes updates the plot's underlying data; the heading arrow renders only when
  heading is provided and not otherwise.
- `MainWindow`: a fresh `GPSTrack` is created on each `connect_towfish`/`open_playback_file` call
  (verify by checking `id(window.gps_track)` changes, or that `has_data` resets to `False`).

## Explicitly out of scope for this spec

- Verifying the live nav byte layout against real hardware — not possible until real
  hardware/SatCenter access exists. Tracked as a known follow-up, not blocking this spec.
- Any UI for manually correcting/editing a track (e.g. the original's drag-to-mark feature) —
  belongs to the separate, unscoped "marks" subsystem.
- Persisting the track across app restarts — no persistence layer exists anywhere in this app yet,
  consistent with the enhancement panel's same decision.
