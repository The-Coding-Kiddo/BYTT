---
title: BYTT Python rewrite — architecture, GUI framework, and towfish networking
status: draft — approved by user 2026-09-18, networking facts provisional
---

## Why this document exists

We decided to build the HydroWaterDemo replacement (a Qt6/C++ side-scan sonar survey app) fully
in Python. `interactive_bsf_viewer.py` (3664 lines, pygame-based) is the working baseline: it
already parses the Hytem wire protocol, streams live data, plays back `.bsf` recordings, renders
a waterfall image, tracks GPS position, and runs a real-time OpenCV enhancement pipeline. This
spec covers the first sub-project of the full rewrite: **the overall architecture, GUI framework
choice, module breakdown, and towfish/SatCenter network integration** that the rest of the
existing logic gets migrated into.

Later, separate specs will cover: recording, sonar control panels (replacing the closed-source
`LibControlPanel`), and packaging/distribution. This document intentionally does not design those.

## Background (from existing docs, see `BYTT docs/`)

- The original app is Windows desktop, Qt6/C++. It talks to a towfish over TCP using the **Hytem
  protocol**: packet type 3101 (side-scan intensity), 3102 (pre-rendered image + nav), 107
  (command, sent by us), 166 (link status).
- Today, **SatCenter** runs as a middleman on the operator PC: sonar (5002) and INS (6001) both
  feed it, it merges INS position/heading into the 3101 packet's 24 `reserved2` bytes, and emits
  one merged stream to us on port **16129**. Commands travel the other way: us → SatCenter on
  **16128** → sonar on 5001.
- SatCenter is being relocated *inside* the towfish firmware — same protocol, same ports, just a
  different physical box. Our app therefore only ever needs one configurable address
  (`TowfishIP`) and the two ports we already use (16128 cmd, 16129 data); 5001/5002/6001 are
  internal to whatever box does the merge and are never our concern.
- **Networking facts here are the current working baseline** (from `architecture.md` and
  `satcenter-migration.md`) and are explicitly provisional — the user is gathering further
  first-hand information from the team that implemented the embedded SatCenter, and this spec
  (plus the `net/` module) will be revised as that comes in.
- Recording (start/stop) used to work by rewriting SatCenter's `Config.ini` and restarting it.
  That breaks once SatCenter is inside the towfish. Decided fix (out of scope here): record the
  merged wire stream directly on our side.

## GUI framework: PySide6 + pyqtgraph

Chosen over hand-rolled Qt rendering and over Dear PyGui.

- **PySide6** (official Qt-for-Python) gives native menus, toolbars, dialogs, and widgets —
  the closest realistic match to "full GUI like the Qt software we had" — and is cross-platform
  (Windows/Linux/macOS, per user decision to go fully cross-platform since this is Python).
- **pyqtgraph** handles the performance-sensitive real-time displays: the scrolling waterfall
  image and the waveform strip. It's purpose-built for exactly this kind of real-time
  instrument/scientific display, and removes most of the manual scaling/redraw/LUT code that
  `FastWaterfall` currently hand-rolls in pygame.
- `pygame` is dropped entirely. `numpy` and `opencv-python` remain (enhancement pipeline).
- Both the OpenCV enhancement pipeline and `.bsf` playback carry over as-is — they're proven,
  logic-only, and not GUI-framework-dependent.

## Project layout

```
BYTT-python/
├── pyproject.toml
├── bytt/
│   ├── app.py                  # entry point: QApplication, wires everything, shows MainWindow
│   ├── config.py               # loads/saves an .ini (Source=playback/towfish, TowfishIP, CmdPort, DataPort)
│   ├── protocol/
│   │   ├── constants.py        # magic bytes, packet type IDs, header offsets/sizes
│   │   ├── packets.py          # header/3101 parsing, checksum, nav-field extraction
│   │   └── commands.py         # NEW: builds type-107 command packets (start/stop, sonar params)
│   ├── net/
│   │   ├── live_client.py      # LiveClient — threaded socket, emits Qt signals
│   │   ├── command_client.py   # NEW: CommandClient — outbound socket on CmdPort
│   │   └── playback_source.py  # reads a .bsf file, feeds the same pipeline LiveClient feeds
│   ├── processing/
│   │   ├── enhancement.py      # OpenCV pipeline: denoise/AGC/CLAHE/sharpen/target-shadow suppression
│   │   ├── colormap.py         # LUT building (brown/prismy/gray/jet)
│   │   └── calibration.py      # calibrate_amplitude_range
│   ├── nav/gps_track.py        # GPSTrack + degree formatting
│   ├── bsf/file_io.py          # .bsf file header/ping loading
│   ├── gui/
│   │   ├── main_window.py      # QMainWindow: menu bar, toolbar, status bar, panel wiring
│   │   ├── waterfall_view.py   # pyqtgraph-based waterfall (replaces FastWaterfall)
│   │   ├── waveform_view.py    # pyqtgraph waveform strip
│   │   ├── gps_panel.py        # GPS track panel widget
│   │   ├── controls_panel.py   # gain/gamma/denoise/contrast/palette widgets (UI only for now)
│   │   ├── connect_dialog.py   # choose playback file vs. towfish IP
│   │   └── shortcuts.py        # SPACE/arrows/TAB/F1/M/L/Q keybindings, unchanged from today
│   └── util/logging.py
└── tests/
    ├── test_protocol_packets.py
    ├── test_live_client.py
    └── fixtures/                # small .bsf fixtures for tests — NOT the 46MB file currently in repo root
```

**Rationale:** `protocol/`, `processing/`, `nav/`, `bsf/` are pure logic — no sockets, no Qt — so
they port from the existing script with no behavior change and are independently testable. `net/`
owns threading/sockets. `gui/` is the only layer that knows about Qt/pyqtgraph. This is the normal
reason Python (or any language) projects split into a package of small, single-responsibility
modules instead of one large file: testability, replaceable pieces, and files small enough to
reason about — the existing file grew into one script because it started as one, not because
that's a Python norm.

**Housekeeping surfaced by this work (not core to the design, but should happen alongside it):**
`samsun kayalık.bsf` (46MB) and `venve/` are currently untracked in git and sitting in the repo
root — they should be moved out / gitignored rather than committed.

## Networking design

**No process management.** Unlike the old app, this app never launches/kills/reconfigures
SatCenter — since SatCenter is either a local passthrough today or embedded in the towfish
tomorrow, connecting is always just "open a socket to `TowfishIP`." That entire category of
choreography the C++ app carried is not needed.

**Config** (`bytt/config.py`), same shape as `HydroWaterDemo.ini` but trimmed to only what we use:
```ini
[Sonar]
TowfishIP = 192.168.1.16
CmdPort   = 16128
DataPort  = 16129
Source    = playback | towfish
```
No `[SonarDevice]`/`[HydroSonar]`/`[UserPara]` sections — those are SatCenter/vendor-internal.

**Unified ping source interface.** `LiveClient` (net) and `PlaybackSource` (reads a `.bsf` file,
replays pings at real timing, ~16 Hz) both implement the same interface: `start()`, `stop()`, and
Qt signals `ping_received(port_raw, stbd_raw, meta)` / `status_changed(str)`. Everything
downstream (`main_window`, waterfall, GPS panel) only ever talks to "the current ping source" and
cannot tell live from playback apart — the property that makes playback a trustworthy stand-in
for hardware testing today.

**CommandClient** is the send-side twin of `LiveClient`: opens a socket to `TowfishIP:CmdPort`,
and `protocol/commands.py` encodes type-107 packets (start/stop, sonar parameters). No UI drives
it in this phase — that's the control-panel spec — but `main_window.py` owns one instance so a
future panel just calls into it.

**Status/state machine** uses the already-parsed type-166 status packets (`data_up`/`cmd_up`
bits) as the real liveness signal, replacing the old app's throwaway-socket fake probe entirely.
Status bar states: `disconnected → connecting → connected (data+cmd) / connected (data only) /
connected (cmd only) / sonar link down`.

**Connect/disconnect is manual** in this phase (toolbar/menu action). No automatic
reconnect-with-backoff yet — flagged as a small, easy follow-on, not forgotten.

**Forward-compat hook for recording** (not built in this phase): both `LiveClient` and
`PlaybackSource` should expose the raw reassembled packet bytes via a signal, so a future recorder
can subscribe and write to disk without reworking the network layer.

## Testing approach

- `protocol/`, `processing/`, `nav/`, `bsf/` get unit tests since they're pure logic ported from
  code already exercised by real playback runs.
- `net/live_client.py` and `net/playback_source.py` get tests against small fixture `.bsf` files
  (not the 46MB file currently in the repo), covering packet reassembly, checksum handling, and
  the unified ping-source interface.
- GUI layer (`gui/`) is verified by running the app against playback, per the project's existing
  approach of using `.bsf` playback as the no-hardware verification path.

## Explicitly out of scope for this spec

- Recording implementation (file rotation, disk management) — separate spec.
- Sonar control panel UI and its wiring to `CommandClient` — separate spec.
- Docking/rearrangeable panel layout — not required now (confirmed with user); `gui/` widgets are
  structured so adding `QDockWidget` later is straightforward if wanted.
- Automatic reconnect-with-backoff.
- Packaging/distribution (PyInstaller etc.).
- Any change to the actual wire-protocol facts — those will be revised as the user gathers
  first-hand information from the team that implemented the embedded SatCenter.
