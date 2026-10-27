# BYTS Technical Specification

**Document:** Product & Technical Requirements Specification
**Product:** BYTS — side-scan sonar survey and data-acquisition software
**Company:** BLC
**Status:** Living — reflects implemented system
**Platform:** Desktop application, Python 3.11+

---

## Contents

0. [Product overview](#0-product-overview)
1. [Network architecture & protocol](#1-network-architecture--protocol)
2. [Waterfall: signal acquisition, processing & display](#2-waterfall-signal-acquisition-processing--display)
3. [Application & GUI](#3-application--gui)
4. [Cross-cutting requirements](#4-cross-cutting-requirements)
5. [Open items & known limitations](#5-open-items--known-limitations)

---

## 0. Product overview

### 0.1 Purpose

BYTS is a field-deployable application for operating a towed side-scan sonar ("towfish"), viewing its acoustic imagery in real time, recording survey sessions, and reviewing recorded sessions afterward. It is the operator's primary interface during a hydrographic or search-and-recovery survey.

### 0.2 System context

The towfish contains two independent instruments: a side-scan sonar (produces seabed imagery) and an inertial/GPS navigation unit (produces position and heading). A component inside the towfish, referred to in the protocol documentation as SatCenter, merges both instrument streams onto one TCP connection before they reach the operator's PC. BYTS is the only software running on the operator's PC; it does not run or manage SatCenter, which is embedded firmware, not a process BYTS launches.

**System actors:**

| Actor | Role |
|---|---|
| Towfish | Towed hardware unit containing the sonar transducer array and INS/GPS. Source of all survey data. |
| Embedded merge component | Firmware inside the towfish that combines sonar and navigation data onto a single outbound stream and exposes command/data TCP ports to operator software. |
| BYTS | Operator-facing application: this product. Connects to the towfish, displays imagery, issues sonar commands, records sessions, and reviews recordings. |
| Operator | Survey technician running BYTS on a laptop/PC aboard the survey vessel. |

### 0.3 Two operating modes

BYTS operates identically, from the GUI's point of view, against two different data sources:

- **Live mode** — a real-time TCP connection to a towfish.
- **Playback mode** — a previously recorded session file (`.bsf` container format) replayed at a selectable speed.

This is an architectural requirement, not an incidental feature: every consumer of ping data (the waterfall renderer, the navigation track, the coverage map) is written against one shared data contract, so a bug reproduced in playback is the same bug that would occur live, and every feature built against recorded data works unmodified against a live towfish.

---

## 1. Network architecture & protocol

### 1.1 Purpose

Establish and maintain the operator PC's connection to the towfish, decode its wire protocol into structured ping and status data, and issue sonar operating commands back to it — reliably enough that a survey does not need to be repeated because of an undetected silent failure.

### 1.2 Physical & transport layer

**Connection endpoints:**

| Channel | Direction | Transport | Port | Purpose |
|---|---|---|---|---|
| Data | Towfish → BYTS | TCP, persistent | `16129` | Streaming ping (imagery) and status packets |
| Command | BYTS → Towfish | TCP, persistent | `16128` | Sonar operating parameters (gain, range, run/stop) |

Both channels are independent TCP sockets, each read or written from a dedicated background thread so that a stall on one channel cannot block the other or the GUI thread. The command channel's absence of a connection does not imply the data channel is down, and vice versa — the two are tracked and surfaced as separate health states (§1.5).

> **⚠ Unverified against hardware.** No physical towfish has been available during development. All wire-protocol handling has been verified two ways: (a) replaying real recorded session files through the identical parsing code path used for live data, and (b) connecting the live socket client to the vendor's own SatCenter binary running under Wine, which accepted both connections cleanly. Neither test exercises a live sonar producing data on the far side of SatCenter — that a raw `connect()` succeeds is not evidence the sonar itself is reachable, which is precisely why §1.5's link-health signal exists as a separate check.

### 1.3 Wire packet format

Every packet — in either direction — shares one 24-byte header, little-endian:

**Packet header, 24 bytes:**

| Offset | Field | Type | Notes |
|---|---|---|---|
| 0 | packetIdentifier | u32 | Fixed magic value, used to resynchronize the byte stream after a partial read |
| 4 | headerSize | u16 | Always 24 |
| 6 | packetVersion | u16 | Protocol version (V1.0.9 at time of writing) |
| 8 | packetSize | u32 | Total packet length including header, content, and trailer |
| 12 | packetFlag | u16 | Bit 0: multi-packet frame. Bit 1: checksum present |
| 14 | identification | u16 | Frame ID, used to reassemble a multi-packet frame |
| 16 | totalPacket | u16 | Fragment count for this frame |
| 18 | packetNumber | u16 | 1-indexed fragment number |
| 20 | packetType | u32 | 3101 (ping), 166 (status), 107 (command) |

Content follows the header; a 4-byte trailer follows the content. When the checksum flag is set, the trailer is the byte-sum of every preceding byte, truncated to 32 bits. When unset, the trailer is a fixed sentinel value rather than a real checksum. Fragmented frames (`totalPacket > 1`) are reassembled by frame ID before the content is interpreted; a fragment that never completes is discarded after a 2-second timeout so a lost fragment cannot leak memory indefinitely.

#### 1.3.1 Packet type 3101 — side-scan ping (inbound)

The 104-byte body carries per-ping metadata (timestamp, ping number, sonar range, center frequency, sample rate, sample count) followed by interleaved raw amplitude samples for the port and starboard channels. Sonar range and sample count are read directly from this body — for live data this is a hardware-reported value, not a client-side estimate.

> **⚠ Vendor-documented but unverified field placement.** Navigation (longitude, latitude, heading, height — 24 bytes) is not a field the published protocol document describes for packet 3101. It occupies a block the sonar's own firmware leaves reserved; the embedded merge component fills that block with INS data before forwarding the packet. This substitution is confirmed by the vendor's own header comments, and independently cross-checked against a second Qt/C++ implementation of the same protocol maintained by the hardware team — both describe the identical byte layout. It has never been confirmed against a live towfish producing this field in practice.

#### 1.3.2 Packet type 166 — link status (inbound)

One content byte, read as two independent nibbles rather than a bitmask: the high nibble reports whether the sonar's own command network is reachable, the low nibble whether its data network is reachable. This is the towfish's self-assessment of its own two internal links, and is tracked as a signal distinct from "our socket is open" — a TCP `connect()` succeeding says only that the embedded merge component answered, not that it can currently reach the sonar behind it.

#### 1.3.3 Packet type 107 — sonar work command (outbound)

256 bytes total: 24-byte header, 228-byte content, 4-byte checksum. Content carries device/work mode, sync mode, sound speed, ship-speed source, towfish depth/height/heading sources, and two independent channel blocks (high-frequency and low-frequency), each with its own enable flag, range, signal type, pulse width, source level, center frequency, gain, spreading loss, and absorption coefficient, plus a single run/stop flag shared across both channels.

### 1.4 Recorded session format (`.bsf`)

Distinct from the live wire format — a separate container, not a capture of raw packets. A 1024-byte file header (carrying, among other fields, the sound-speed setting used for the recording) precedes a sequence of self-describing records, each tagged by a fixed record-type marker and its own length. Two record shapes exist: ping records (variable length, sample data beginning at a fixed offset) and navigation-fix records (always exactly 156 bytes). A navigation fix in this format has no companion field for heading or sonar range — those are physical properties of the live packet format that the file format does not carry.

> **NET-01.** BYTS must derive a per-ping sonar range for recorded sessions without a stored range field, since the file format does not carry one.
> **Resolved:** range is computed from the file's recorded sound-speed and each ping's own sample count and sample rate — `range = (sound_speed ÷ 2) × (sample_count ÷ sample_rate)` — the same relationship independently implemented in a second, unrelated codebase that reconstructs the identical live-packet field for the same purpose. Cross-checked against two real recordings: one holds a constant computed range across its full 10,679-ping duration, consistent with an operator who did not change the sonar's range setting mid-survey.

### 1.5 Connection health & failure modes

A raw socket connecting successfully is necessary but not sufficient evidence that the survey is actually working — the operator needs to know, at a glance, about three independent things that can each fail on their own:

**Independent health signals:**

| Signal | What it answers | Failure it catches |
|---|---|---|
| Data-socket state | Is the TCP connection to port 16129 open, and is data actually arriving? | A connection that opens but never receives a ping — indistinguishable from "working" by socket state alone |
| Command-socket state | Is the TCP connection to port 16128 open? | Commands silently failing to reach the sonar |
| Sonar self-reported link (packet 166) | Can the sonar itself reach its own command/data networks? | The embedded merge component answering the operator PC while it cannot reach the sonar behind it — a real, previously observed failure where the application reported "connected" and then received nothing, with no indication why |

A data connection that stops receiving pings for a configurable interval (default 5 seconds) after having previously received them degrades from "connected" to "connected, no data" rather than continuing to report full health — silence after a real connection is a distinct, and more dangerous, failure mode than never having connected at all, since an operator glancing at a "connected" label has no other cue that the survey has stopped producing useful data.

### 1.6 Recording

The live data stream is written to disk verbatim, byte for byte as received, rather than through any transformation. This is deliberate: a recording captures whatever the software received, independent of any decoding or display logic downstream, so a defect discovered in playback processing later can be diagnosed against the exact bytes the sonar actually sent, and a session can be replayed through a corrected decoder without having to survey the site again.

> **Alternative not taken.** An earlier architecture (the vendor's own reference implementation) recorded by instructing the embedded merge component to write its own file, controlled by editing that component's configuration and restarting it. That mechanism cannot survive the merge component moving from a PC process to embedded firmware — there is no file to edit and no process to restart. Recording directly on the operator side removes that dependency entirely and was verified not to lose data: position fixes arrive roughly once per second against sonar pings arriving roughly sixteen times per second, so the wire format has substantially more capacity than there is position data to carry, and recording the raw stream captures both without any risk of the two racing each other.

### 1.7 Same-network-segment validation

The towfish address and the operator PC's own address are both operator-configured. Before dialing out, BYTS checks that the two addresses share the same /24 network segment and surfaces a warning if they do not — a misconfigured PC address (for example, one left over from an unrelated virtual network adapter) otherwise produces a bare connection timeout with no diagnostic value at all, a failure mode that has previously cost real debugging time.

### 1.8 Configuration persistence

Connection settings (towfish address, operator PC address, command port, data port, source mode) persist to a plain-text INI file next to the user's home directory, editable without the application running. This is an explicit requirement, not a default: a field technician must be able to repoint the software at a different towfish address using a text editor, with no rebuild and no dependency on the application's own settings UI being reachable.

---

## 2. Waterfall: signal acquisition, processing & display

### 2.1 Purpose

Render the sonar's raw acoustic returns as a continuously scrolling image the operator can interpret in real time, apply operator-selectable enhancement to make weak or noisy returns legible, and derive a real-world coverage record of what area has actually been surveyed — as opposed to merely where the vessel has traveled.

### 2.2 Raw signal pipeline

Each ping delivers two independent channels (port and starboard), each an array of unsigned amplitude samples ordered by increasing distance from the towfish. Before any visual enhancement, raw samples are log-compressed and linearly rescaled against a calibrated amplitude window, rather than a fixed constant: the window is derived once per recording by sampling amplitude values spread across the whole file and taking the 2nd and 99.5th percentiles of their log-compressed distribution. This replaces a single hard-coded normalization constant with one that reflects what a specific file's actual gain setting recorded, since a fixed constant tuned for one gain setting visibly clips or washes out a recording made at a different one.

### 2.3 Enhancement pipeline (operator-controlled, applied per displayed frame)

**Enhancement stages, applied in this order:**

| Stage | Modes | Purpose |
|---|---|---|
| Denoise | Off, Median, Bilateral, NL-Means | Suppress background speckle. Bilateral is tuned specifically to preserve single- and few-pixel targets rather than erase them along with the noise — a hard-target return dropping from full brightness to near-background under an untuned bilateral filter was treated as a defect and corrected. |
| Automatic gain | On/off | 1st/99th percentile stretch of the current frame |
| Contrast | Off, Global histogram equalization, CLAHE (3 clip-limit tiers) | Local vs. global contrast expansion |
| Target enhancement | Off, Top-hat (bright), Top-hat (bright+dark), CFAR-style local contrast | Morphological detection of compact returns (debris, wrecks, mines) against a locally-estimated background, with an optional colored overlay marking detected target/shadow regions distinctly |
| Sharpen, gain, gamma, colour palette | Continuous / 5-palette selection | Final display tone mapping |

### 2.4 Rendering performance architecture

Enhancement is computationally heavier than the display's frame rate can absorb if run synchronously on every incoming ping, particularly at full survey resolution. This is handled with a background worker holding exactly one pending job slot rather than a queue:

- A newly arrived frame or a changed parameter replaces whatever job is currently pending rather than queuing behind it — the display only ever needs the most current result, never a backlog of stale ones.
- Each submitted job carries a monotonically increasing generation number; a result is only displayed if its generation is newer than whatever is currently shown, so a slow job that finishes after a faster, newer one cannot overwrite fresher data on screen.
- Parameters are split into "heavy" (denoise mode, contrast mode, target detection — anything that changes the underlying processed image) and "light" (gain, gamma, palette — anything that is a pure recolor of an already-processed image). Changing only a light parameter reuses the previously computed heavy result rather than re-running the full pipeline, cutting the common case of "the operator is just adjusting brightness" from a full reprocess to a lookup-table application.
- Work resolution is adaptively downscaled before the expensive stages and upscaled back for display when the frame exceeds a size budget — three separate budgets, since NL-Means denoising is dramatically more expensive per pixel than the rest of the pipeline and live acquisition has a tighter latency requirement than reviewing a static recording.

### 2.5 Coverage mapping

A track line alone — the vessel's path — does not answer "what area has been surveyed," since the sonar sees a strip on each side of the vessel, not a single point beneath it, and cannot see anything at all in a strip directly beneath the towfish (the "nadir gap," caused by the direct water-column return dominating before any seabed echo arrives). Coverage is rendered as a band on each side of the track, bounded by an inner (nadir) edge and an outer (maximum-range) edge, built from real per-fix data rather than a static assumption of a rectangle following the vessel:

> **WTF-01.** The outer coverage boundary must reflect the sonar's actual effective range for that specific ping, not merely the range the operator commanded it to use, since a real device's actual returned range and its configured range are not guaranteed to agree.
> **Implemented:** two independent, real signals are combined — the ping's own reported range (§1.3.1/§1.4, physically real for both live and playback) sets the distance scale, and where enough ping history has been buffered, the true drop-off point is measured directly from amplitude data (mean amplitude falling below a noise-floor threshold, scanned outward), rather than assumed to extend to the full reported range. Verified against a real recording: measured usable range settled at roughly 18–22 metres against a 50-metre configured setting, and remained stable across the recording rather than producing a discontinuity.

> **WTF-02.** Coverage geometry must remain correct through a vessel turn and must not lose previously recorded coverage when the vessel re-scans an already-surveyed area.
> **Implemented:** each segment of coverage between two consecutive fixes is rendered as an independent shape sharing exact corner coordinates with its neighbors (a shared travel-direction estimate per fix, not per segment, computed as the average of the fix's incoming and outgoing direction), composited with a fill rule under which overlapping shapes accumulate rather than cancel. Two distinct defects were found and corrected during verification against real recorded tracks: a wedge-shaped gap opening at the outer edge of sharp turns (root cause: adjacent segments computing independent, disagreeing corner positions for a point they shared), and coverage visibly disappearing wherever a track re-crossed itself (root cause: one continuous self-intersecting shape rendered under a fill rule that treats doubly-covered area as empty).

> **⚠ Deliberately not implemented as "measured."** The inner (nadir-gap) boundary is not derived from amplitude data. An initial attempt applied the same ping-to-ping-variance technique used for the outer edge, on the reasoning that a genuine water-column artifact should look identical from ping to ping while real seabed texture should not. Checked against a real recording, this did not hold: measured variance in the true near-range region ranged from roughly 0.055 to 0.25 throughout, never approaching the threshold the technique had been validated against using synthetic data, and it fired on the very first sample — collapsing the modeled gap to a few centimetres. Rather than re-tune the threshold against the one available recording, the near edge currently falls back to a fixed 8% of the measured range (or an unverified towfish-altitude field, when a live connection reports one), clearly distinct in the codebase from the measured outer edge. This is an open item; see §5.

### 2.6 Navigation track & waypoints

Position fixes project onto a local flat-earth coordinate system referenced to the first fix of the session, rather than a global projection — adequate for the scale of a single survey (kilometres, not the curvature-significant distances a global projection exists to handle) and simpler to reason about. Waypoints may be dropped at the vessel's current position and are displayed with a live distance-and-bearing readout computed from the current position, using standard great-circle formulas.

Vessel speed has no dedicated field anywhere in the wire protocol — confirmed independently against a second implementation of the same protocol, which hard-codes speed to zero rather than solving this. BYTS derives it from consecutive position fixes' arrival time and distance. This is accurate in live operation, where arrival time is real elapsed time; during accelerated playback it reflects playback speed rather than the original survey's speed, since there is no other reference to calibrate against — documented as an accepted approximation, not silently presented as measured.

---

## 3. Application & GUI

### 3.1 Purpose

Present the waterfall, navigation, and sonar-control functionality as a single coherent desktop application usable during an active survey — legible at a glance, operable with one hand on a keyboard while the other is on other equipment, and honest about the state of the connection at all times.

### 3.2 Technology

Built on PySide6 (Qt 6), a direct, in-process binding to the Qt C++ toolkit — not a subprocess or an abstraction layer over it; a widget created in the application is a real Qt object, and calls into it execute compiled Qt code directly. Plotting (the waterfall image and the navigation map) is built on pyqtgraph, a Qt-native plotting library, rather than a general-purpose plotting library, because the waterfall's update pattern — a new row appended to a scrolling raster many times per second — is exactly the workload pyqtgraph is built around and is not what general-purpose plotting libraries are optimized for.

> **Alternative considered.** PyQt6, from a separate vendor, provides an equivalent binding to the same underlying Qt toolkit and would have been functionally interchangeable for this application. PySide6 was selected specifically for its LGPL licensing, which does not impose the GPL/commercial-license choice PyQt6's licensing would otherwise place on this codebase.

### 3.3 Application structure

A single main window hosts a central scrolling waterfall display and three dockable, independently repositionable and hideable panels:

| Panel | Contents |
|---|---|
| Enhancement controls | All fields from §2.3 — denoise mode, contrast mode, target detection, gain/gamma/palette, per-channel (port/starboard) visibility toggles |
| Sonar control | Per-channel (high/low frequency) range and gain, channel enable, and a run/stop control, sent as protocol type-107 commands (§1.3.3) — sent explicitly on an Apply or Start/Stop action, never continuously as a control is adjusted, since these commands reach real hardware and an accidental flood of them carries real consequences a purely local display setting does not |
| GPS / navigation track | Track history, current position and heading, waypoints with live distance/bearing, and the coverage overlay from §2.5 |

### 3.4 Playback transport

A toolbar, visible only when a recorded session is open (not during a live connection, where it has no meaning), provides play/pause, single-ping step forward/backward, a scrub position slider, and a speed control (1× through 32×, plus an uncapped maximum). Reaching the end of a recording pauses playback in place rather than terminating the session, so that scrubbing backward or restarting from the beginning remains available without reopening the file.

### 3.5 Connection health display

The three independent health signals from §1.5 are shown as three separate indicators, not collapsed into one — a single merged "connected/disconnected" state cannot represent "the socket is open but the sonar behind it cannot be reached," which is precisely the failure mode §1.5 exists to catch, and merging them back into one indicator in the UI would silently discard the diagnostic value of having separated them in the first place.

### 3.6 Recording control

A single toggle starts and stops recording (§1.6), enabled only while a live connection is active. A recording in progress is shown with a persistent, high-visibility indicator distinct from ordinary status text, and recording is force-stopped automatically on disconnect or application close — an operator cannot leave a file handle open by forgetting to press stop.

### 3.7 Keyboard control

Primary playback and view operations (play/pause, step, speed, scrub-to-start, quit) are bound to single keys, on the expectation that an operator's attention during an active survey is on the imagery and instruments, not on locating a specific toolbar button.

---

## 4. Cross-cutting requirements

### 4.1 Source-agnosticism

No GUI component is permitted to know, or be able to detect through its interface, whether it is receiving data from a live towfish or a recorded file. Both sources expose an identical event contract. This is enforced structurally, not by convention: it is the reason a defect found by replaying a recorded file is trusted to be the same defect that would occur live, in the near-total absence of hardware to test against directly.

### 4.2 Verification standard for hardware-adjacent claims

Any value that ultimately depends on real hardware behavior — heading, towfish altitude, sonar-reported range, nadir-gap geometry — carries an explicit, code-level note distinguishing what has been confirmed against a real recording or a real device connection from what has not. A plausible-sounding value with no such note is treated as a defect in the documentation, not a minor omission, given the operational cost of an operator trusting a number that turns out to be untested.

### 4.3 Test strategy in the absence of hardware

Two real recorded sessions are used as ground truth for behavior that cannot otherwise be checked: a short recording confirmed to carry genuine navigation fixes, and a longer (413MB, ~10,700-ping, ~11-minute) recording independently confirmed against a second team's own investigation of the identical file. Automated tests use synthetically constructed packets and files built from the documented byte layouts; claims about real-world behavior — coverage geometry, computed range, connection handling against real vendor software — are additionally checked by direct execution against these recordings or against the vendor's reference server binary, not asserted from protocol documentation alone.

---

## 5. Open items & known limitations

| Item | State |
|---|---|
| Nadir-gap (near-edge coverage boundary) width | Estimated, not measured (§2.5). A measured approach analogous to the outer-edge technique is designed but not implemented. |
| Live heading and towfish-altitude fields | Parsed per the vendor-documented byte layout; never observed from a live towfish. |
| Full connect/disconnect/reconnect cycle against real hardware | Blocked on hardware availability, not on software readiness. |
| Multi-line survey coverage (saved/named tracks, free-form area measurement, alternate coordinate systems) | Out of current scope; the present design is single continuous session, one active track. |

---

*BYTS — BLC. This document describes the system as implemented and is maintained alongside it; sections marked unverified reflect the current state of hardware access, not an intended permanent limitation.*
