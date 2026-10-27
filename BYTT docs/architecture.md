---
title: How the system works — start here
status: living document. Last verified against code 2026-08-09.
note: |
  Code references here use function and symbol names, not line numbers, on purpose.
  A check on 2026-08-09 found about a third of the line-number citations across our docs
  pointed at the wrong thing after A1 shifted the lines. Names survive edits; line numbers do not.
---

This is the plain-English overview. Read this first. The detailed plan lives in
[SPEC.md](../SPEC.md), current status in [TASKS.md](../TASKS.md), and the wire format in
[knowledge/vendor/](vendor/).

## What the app is

A Windows desktop app (Qt 6 / C++) for side-scan sonar surveys. It draws two things: a scrolling
picture of the seabed, and where the boat was when each strip of that picture was taken.

## The three pieces

**The towfish** — the unit towed behind the boat. Two separate things live inside it:

- the **sonar**, which produces the seabed picture
- the **INS**, which produces position and heading

They are separate devices with separate network ports. This matters more than it sounds.

**SatCenter** — a middleman program. Today it runs on your PC. It is moving inside the towfish.

**Our app** — HydroWaterDemo.

## How data flows today

The sonar and the INS both send to SatCenter, on different ports. SatCenter combines them and sends
us a single stream.

```
        TOWFISH HARDWARE (192.168.1.16)
        ┌──────────────────────────────┐
        │  sonar ──── picture ─────────┼──── 5002 ──┐
        │                              │            │
        │  INS ────── position ────────┼──── 6001 ──┤
        └──────────────────────────────┘            │
                                                    ▼
                                            ┌───────────────┐
                                            │   SatCenter   │
                                            │  merges them  │
                                            └───────┬───────┘
                                                    │
                                          16129 ── one stream ──┐
                                                                ▼
                                                        ┌──────────────┐
                                                        │   our app    │
                                                        └──────────────┘

        commands travel the other way:  our app ── 16128 ─→ SatCenter ── 5001 ─→ sonar
```

### The merge is the important part

The sonar's picture packet (type 3101) has **24 spare bytes** in it that the sonar leaves empty. The
published spec calls that field `reserved2[24]`.

SatCenter fills those 24 bytes with the position data it got from the INS: longitude, latitude,
heading, and height. That is exactly 8 + 8 + 4 + 4 = 24 bytes. You can see the substitution in the
vendor's own header, `DefDataSideScan` in
[HytemDataFormatDef.h](../3rd/Hydro_3101d/include/HytemDataFormatDef.h) — the `reserved2` line is
commented out and the four navigation fields sit in its place.

So when our app reads position out of the sonar stream, **the position is only there because
SatCenter put it there.** Two inputs in, one stream out.

> Older versions of this file, and of
> [satcenter-migration.md](satcenter-migration.md), said position came from the sonar stream "not
> via SatCenter." That was wrong and it was the most dangerous sentence in our docs — believe it and
> you would remove SatCenter expecting nothing to change, then find every position reading zero.
> Corrected 2026-08-09.

## What SatCenter actually does for us

On the data path, exactly one job: the merge above.

Everything else our app does around SatCenter is ceremony that moves no data:

- kills it with `taskkill` and relaunches it — and the relaunch is currently switched off by a flag,
  so today it only kills
- rewrites its `Config.ini` file to point it at the towfish or at loopback
- opens a throwaway socket just to check it is alive, then reads nothing from it

There is also a set of functions that *look* like a SatCenter data bridge and are never called at
all: `satCenterConnection`, `processSatCenterData`, `connectToSatCenterSerial`, and the whole
`ConnectDialog` class. Names in this area do not reliably indicate what runs. Check for call sites.

## What "removing SatCenter" actually means

**It is not being deleted.** It is moving inside the towfish. Same software, same ports, same merge —
just on the far side of the cable. The vendor keeps providing everything it provides today.

So the job is not "replace SatCenter." It is two much smaller things:

1. stop launching and managing a local program, and
2. point our sockets at the towfish instead of at our own PC.

What that means per area:

| Area | After the move |
|---|---|
| Sonar picture | Unchanged. Same packets, same port. |
| Position / heading | Unchanged. Still merged in, still in those 24 bytes. |
| Which address we connect to | One value in `HydroWaterDemo.ini`. Already built — checkpoint A1. |
| Launching / killing the process | Delete it. Nothing to launch. |
| Editing SatCenter's config file | Delete it. The file is inside the towfish now. |
| **Recording on/off** | **Breaks.** See below. |

### The one thing that genuinely breaks

Recording works today by editing `FileStoreON=` in SatCenter's config file and then restarting
SatCenter so it re-reads the file. Look at `HydroMainWindow::toggleRecording` — that is literally
what it does.

Both halves of that trick stop working once SatCenter is inside the towfish: we cannot edit the file,
and we cannot restart the process. And there is no command to replace them — the protocol's command
packet (type 107) carries sonar settings only, nothing about storage.

The button is also already broken today, because the relaunch is switched off.

**The fix, decided 2026-08-09: record on our own side.** We already receive the full merged stream,
so writing it to disk *is* recording — no vendor involvement, no command to wait for, nothing
stranded inside the towfish.

The one worry was whether the stream carries everything the towfish would have stored. Measuring a
real recording settled it: pings arrive ~15× more often than position fixes, so the stream has far
more room for position than there is position to carry. Nothing is lost.

That is checkpoint A5 in [SPEC.md](../SPEC.md). Worth knowing before starting it: the recording part
is small, and the real work is file rotation and not filling the disk.

## Inside our app

The receiving path, in order:

```
NetSonarDataTcpClient   own thread, reads the socket, splits packets, pulls out nav
  → Tool                re-emits the packets as Qt signals
    → SonarDataPro      own thread, packet processing
      → HydroMainWindow the window, which fans out to:
          → SonarPlotWindow   the waterfall picture (vendor, closed source — Track B replaces this)
          → NaviOsgViewUI     the boat position and track
```

`NetSonarCmdTcpClient` runs the other direction on its own thread, sending start/stop and sonar
parameters.

Two details worth knowing because they surprise people:

- `HydroSonarControlWidget` opens its **own** separate socket to the towfish for gain and range
  control, bypassing `NetSonarCmdTcpClient` entirely. Two different command paths to one device.
- We handle nine packet types that the protocol document does not describe (2016, 2017, 3000, 3102,
  3103, 5000, 5001, 5004, 5005), and we ignore the one documented status packet, **166**, which
  reports whether the sonar's networks are up. Handling 166 is checkpoint A1b.

## Testing without hardware

`TcpSenderWindow` pretends to be the towfish and replays recorded files at our client code. This is
the main way work gets verified — no hardware needed. The recorded files currently live inside
`satcenter/`, which means they have to be moved somewhere else before that folder can be deleted.

**Know this before trusting a playback run (found 2026-08-09):** both in-repo `.bsf` fixtures contain
**no position data whatsoever** — 709 and 720 pings respectively, zero navigation records, and
nothing position-like inline in the ping metadata either. So playback against these two exercises the
picture and the range logic, but **not** positioning. The nav branch of `BsfPlaybackConverter` has
never run, and any past claim that playback showed a GPS fix needs re-checking.

A third recording (`20250530_093513.bsf`, supplied by a colleague, 413 MB, kept outside the repo)
*does* carry nav: 10679 pings and 696 fixes over 11 minutes. Measuring it produced a useful number:

> **Pings arrive at ~16 Hz, position fixes at ~1 Hz — about 15 pings per fix.**

That 1 Hz is the INS device's own update rate. It matters because the wire format carries one
position slot per ping, so it has roughly 15× more room than there is position data to carry. This is
what makes recording the wire stream on our side lossless for navigation — see A5 in
[SPEC.md](../SPEC.md).

Replaying that file also proved the whole nav chain works: position reaches the chart, the vessel
moves, and the status indicators go green. That had never been demonstrated before.

**One known blind spot in playback — do not verify this against it:**

| What | Why it is wrong in playback |
|---|---|
| Heading | Always `0.0°`. `buildHtmPing` writes only longitude and latitude; `heading` and `heigh` are never assigned, so they stay zero. |

That is a converter limitation, not a data problem — a live towfish would not have it.

> A second blind spot was claimed here on 2026-08-09 — that the converter discarded ~65% of each
> ping's samples — and **retracted the same day.** It came from comparing two different recordings'
> numbers. Measured per file, every record is read in full. Sample fidelity in playback is fine, and
> Track B's visual comparison against the vendor widget is trustworthy. See SPEC.md for the figures.

Use [tools/bsf-stats.js](../tools/bsf-stats.js) to check what any recording actually contains —
ping and fix counts, rates, and whether it has position data at all.

Note that the simulator serves **data only**. There is no command server in playback, so the command
client never connects. Any logic that treats "command socket not connected" as "towfish unreachable"
will wrongly tear down a working playback session.

## Closed-source parts we cannot edit

`3rd/Hydro_3101d/` (waterfall rendering), `3rd/controlPanel/` (settings panels), and
`3rd/hydrodockwidget/`. A hook blocks edits to `3rd/` and `satcenter/`. Track B is about replacing
the first of these with our own code.

## Where to go next

- [SPEC.md](../SPEC.md) — the checkpoint plan, both tracks, with acceptance criteria
- [TASKS.md](../TASKS.md) — current status at a glance, open questions
- [satcenter-migration.md](satcenter-migration.md) — deeper detail on Track A
- [waterfall-vendor-sdk.md](waterfall-vendor-sdk.md) — deeper detail on Track B
- [vendor/](vendor/) — the protocol and interface documents, written by our own colleagues
