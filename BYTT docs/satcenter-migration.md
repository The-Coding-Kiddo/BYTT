---
title: SatCenter.exe → direct-integration migration — grounded facts
status: living document, update as the migration proceeds
---

## 2026-07-27 — vendor documentation received, reframes this whole track

Two vendor documents now sit in [vendor/](vendor/):
[towfish-interface-manual.md](vendor/towfish-interface-manual.md) and
[hytem-data-protocol-v1.0.9.md](vendor/hytem-data-protocol-v1.0.9.md).

**SatCenter is not being deleted — it is being relocated into the towfish's FPGA board**
(interface manual §1, line 5). The vendor keeps providing every function SatCenter provides today;
it just runs on the far side of the wire. So this track is *not* "reimplement SatCenter." It is:

1. stop launching/killing a local `SatCenter.exe`, and
2. point our already-working client sockets at the towfish instead of at our own PC.

### The one line that matters

[mainwindow.cpp:1231](../Widget/mainwindow.cpp#L1231):

```cpp
m_tool->initTcpClient(m_localIP, 16128, m_localIP, 16129);
```

with `m_localIP = ConfigManager::getLocalIPAddress()`
([mainwindow.cpp:1383](../Widget/mainwindow.cpp#L1383)).

We already speak the correct protocol on the correct ports — 16128 (command) and 16129 (data) are
exactly what the manual's Table 1.3.1 specifies for "user software." We aim them at our own machine
only because SatCenter is currently local. **Changing `m_localIP` to a towfish IP read from our own
config file is the core of Track A.** The manual explicitly asks for this (§1.1.1, line 35: "use a
separate config file for your operator software to read the IP address/port number").

This also makes the liveness probe collapse into nothing. The throwaway `127.0.0.1:16128` socket at
[mainwindow.cpp:625](../Widget/mainwindow.cpp#L625) becomes a *real* connection to the towfish
command port — probe and working connection become the same object. No `taskkill`, no `QProcess`,
no 2.5s sleep.

### Port map under the new architecture (manual Table 1.3.1)

Towfish is the server at `192.168.1.X`; our PC is the client at `192.168.1.Y`.

| Port | Purpose | Do we use it? |
|---|---|---|
| 16128 | Side-scan work command receiving | Yes — [mainwindow.cpp:1231](../Widget/mainwindow.cpp#L1231) |
| 16129 | Side-scan data upload | Yes — same line, and [mainwindow.cpp:259](../Widget/mainwindow.cpp#L259) |
| 6003 | INS data receiving | **No** — we currently parse nav out of the 3101 stream |
| 16125 | Raw sonar data upload | **No** |

All four are ours to use — we are the user software (confirmed 2026-07-27). The two we don't use are
available if we want them; 6003 in particular may become the intended nav path once SatCenter is
inside the towfish.

Ports 5001/5002 (manual Table 1.1) are SatCenter↔sonar-device and become internal to the towfish — no
longer our concern. Ports 5011/5012/6011 (`[HydroSonar]`, Table 1.2) belong to the **vendor's own**
display-and-control software, not to us.

### Which consumer are we? — RESOLVED 2026-07-27

The manual appeared to give two conflicting tables for "SatCenter ↔ user software" (Table 1.2 =
5011/5012/6011, Table 1.3.1 = 16128/16129/6003/16125). They are not in conflict — they describe **two
different consumers**. The vendor's annotated `Config.ini` (screenshot supplied by the user; the
annotations were lost in the markdown transcription of the manual) labels each section:

| Section | Address | Vendor annotation (translated) |
|---|---|---|
| `[HydroSonar]` | 192.168.1.31 : 5011 / 5012 / 6011 | SatCenter ↔ **display-and-control software HydroSonar** |
| `[UserSoftware]` | 192.168.1.31 : 16128 / 16129 / 16125 | SatCenter ↔ **user software** |
| `[SonarDevice]` | 192.168.1.16 : 5001 / 5002 | SatCenter ↔ **sonar device** |
| (INSDataIP/Port) | 192.168.1.16 : 6001 | SatCenter ↔ **INS device** |

**We are the user software — confirmed by the user, 2026-07-27 ("we are the user software 100%").**
So our ports are **16128 (cmd), 16129 (data), 16125 (raw), 6003 (INS)**, and `[HydroSonar]` /
5011/5012/6011 belongs to the vendor's own display-and-control product and is none of our business.
This matches what the code already does at
[mainwindow.cpp:1231](../Widget/mainwindow.cpp#L1231) — the existing port choice was correct.

Worth knowing for the naming confusion: our app identifies itself as `HydroSonar`
([main.cpp:41](../main.cpp#L41) shared-memory key) and the repo is `HydroSonarUI_QT`, which collides
with the vendor's `[HydroSonar]` section name. **The name collision is historical and means nothing.**
Do not use it to infer which port set applies.

### What the two addresses mean

- **192.168.1.31** — the machine SatCenter runs on (today: the operator PC)
- **192.168.1.16** — the physical hardware, both sonar device and INS

Since SatCenter relocates *into the towfish*, the address serving 16128/16129 becomes the towfish's
own address. Manual Table 1.1 shows SatCenter as a client at 192.168.1.16 connecting to a server at
192.168.1.16 — loopback inside the towfish, which is what you would expect once embedded. **Strong
candidate for the towfish IP: `192.168.1.16`.** Not yet confirmed with the vendor — see open
questions.

### A0 open questions — closed by these documents

- **What writes `satcenter/SatCenterData/NNNNNNNNNN/`?** SatCenter alone, and barely. 42 folders,
  **40 completely empty**; only `0000000040/1_20250916_101642.bsf` and
  `0000000041/2_20250916_104827.bsf` (~75 MB each, both 2025-09-16), plus `File.num`
  (`Folder=42`, `File=2`). Zero references to that path anywhere in `Core/` or `Widget/`.
  The manual's items (20)–(26) (`StoragePathSrc`, `DataFileSavePath`, `FileStoreON`,
  `AutoDeleteFileON`) are all SatCenter settings that now execute *inside the towfish* — recording
  becomes towfish-side storage. Our local folder tree is dead by relocation. **Keep the two `.bsf`
  files as `TcpSenderWindow` playback fixtures; the folder mechanism goes.**
- **Are `getMasterControlPort()`/`getINSControlPort()` live?** No — and it is worse than "the getters
  are unused." **`ConnectDialog` is never instantiated at all.** `connectDialog` is declared at
  [mainwindow.h:179](../Widget/mainwindow.h#L179) and set to `nullptr` at
  [mainwindow.cpp:30](../Widget/mainwindow.cpp#L30); those are its only two occurrences in the
  codebase. No `new ConnectDialog`, no call to any of its four getters. Its Config.ini read/write
  logic ([ConnectDialog.cpp:132-190](../Widget/MainView/ConnectDialog.cpp#L132)) never runs.
  The vendor agrees it is obsolete: manual item (15) documents `INSDataNetType` as **0/1/2 only**
  (UDP / TCP_Client / TCP_Server) — **serial mode 3 is gone**, and `[SerialPort]` is not described
  in the manual's configuration section at all. Delete without hesitation.
- **Does anything outside HydroWaterDemo need SatCenter.exe?** Moot for the *function* — it survives
  inside the towfish. What remains is logistics, still needing the user/vendor: see "Open questions
  for the user" below.

Same dead-code pattern found one layer out during this pass: `setupSatCenterSignals()`
([mainwindow.cpp:805](../Widget/mainwindow.cpp#L805)) has **no call sites** — declaration and
definition only. That is what makes `onSatCenterDataReceived` → `processSatCenterData` unreachable,
independently confirming the "Dead / unwired code" section below.

### Open questions for the user (not greppable)

1. ~~Do we have towfish firmware with SatCenter embedded yet?~~ **Yes — confirmed by the user,
   2026-07-27.** The firmware exists and is deployed. Importantly, the two documents in
   [vendor/](vendor/) were written by **our own colleagues who implemented the embedded SatCenter**,
   not by an external vendor. Two consequences: the documents carry implementer authority rather
   than marketing-spec authority, and every remaining question below can be answered by asking that
   team directly. A4 is gated on getting hands on a towfish, not on firmware readiness.
2. **Confirm the towfish IP is `192.168.1.16`.** Strong inference (see "What the two addresses mean"
   above), not yet vendor-confirmed. The manual only ever writes `192.168.1.X`. Also confirm what the
   PC address should be — the operator machine is currently `192.168.1.39`, already on the right
   segment, so "keep what we have" may be the answer.
3. ~~Table 1.2 vs Table 1.3.1 — which port set is our software meant to use?~~ **Resolved
   2026-07-27:** two different consumers; we are the user software (16128/16129/16125/6003),
   confirmed by the user. See the section above.

Not blocking, but unverified: **we have never observed sonar data actually arriving over 16128/16129
from SatCenter.** The 2026-07-27 run log shows `"NetSonarDataTcpClient" connected`, which proves only
that the TCP handshake succeeded — no 3101 packets followed. All working playback to date is our own
`TcpSenderWindow` simulator on 16129. Worth keeping in mind before treating "connected" as evidence
that the data path works.

## What SatCenter.exe actually does in the current app (verified by reading code, not assumed)

`HydroMainWindow::doActionConnect()` ([mainwindow.cpp:595-701](../Widget/mainwindow.cpp#L595)) is the
real "Connect" flow, and it is almost entirely process/config choreography, not a data bridge:

1. `ConfigManager::setSonarDeviceToTowfish(configPath)` — rewrites `satcenter/Config.ini`'s
   `[SonarDevice]` section to point at the real towfish (192.168.1.16).
2. `killExistingSatCenter()` + `launchSatCenter()` — `taskkill /F /IM SatCenter.exe`, then spawns it
   again via `QProcess` ([mainwindow.cpp:1418-1487](../Widget/mainwindow.cpp#L1418)).
3. After a fixed 2.5s delay, opens a **throwaway** `QTcpSocket` probe to `127.0.0.1:16128`
   (`[UserSoftware] RemoteControlPort` in Config.ini) purely to check SatCenter came back up.
   Nothing is read or written on this socket — it exists only as a liveness signal.
4. On success, starts listening for `Tool::recvedHytem3101` — which arrives via
   `NetSonarDataTcpClient` talking **directly** to the towfish's sonar data port. SatCenter is not
   in this path.
5. If no `Hytem3101` packet arrives within 5s, calls `ConfigManager::setSonarDeviceToLocal(...)` to
   flip Config.ini back to loopback/playback mode and relaunches SatCenter again.

GPS/INS position (`setupRealGpsConnection`, [mainwindow.cpp:139-191](../Widget/mainwindow.cpp#L139))
comes from `NetSonarDataTcpClient::navDataReceived`, parsed out of the sonar packet stream (see
`Tool::examineGPSAtOffset5380` in `Core/Tool.cpp`).

> **Corrected 2026-08-09 — this used to say "not from SatCenter either." That was wrong, and it is
> the most load-bearing correction in this document.**
>
> The nav fields are in the stream *because SatCenter puts them there.* The published 3101 — "the
> data structure uploaded by the sonar" — ends its fixed portion with `u8 reserved2[24]` and has no
> position fields at all. The vendor header comments that out and substitutes exactly 24 bytes of
> nav ([HytemDataFormatDef.h:173-178](../3rd/Hydro_3101d/include/HytemDataFormatDef.h#L173)):
> `longitude[2]` + `latitude[2]` + `heading` + `heigh` = 8+8+4+4.
>
> SatCenter takes two inputs — sonar on 5002, INS on 6001 — parses the INS (`INSDataType`: GGA+ZDA
> or Hydro proprietary), writes those 24 bytes, and emits one merged stream on 16129. That merge is
> the only real job it does on our data path. Everything else it does for us is choreography.
>
> Practical consequence: **do not describe SatCenter as carrying "no live data."** It carries no
> *separate* data stream, which is a different claim. Remove it with nothing in its place and the
> waterfall still renders while every position reads as 24 zero bytes.
>
> The byte layout is documented fact. "SatCenter is the component that writes them" is strong
> inference — the spec says the sonar leaves the field reserved, the consumer header expects it
> filled, and SatCenter is the only component holding both streams — but no document states it
> outright. One question to the implementing team settles it.

## Dead / unwired code (do not assume these are load-bearing)

- `satCenterConnection` (`QTcpSocket*` member) — only ever set to `nullptr` in
  `onSatCenterDisconnected`/`onSatCenterError` ([mainwindow.cpp:829-845](../Widget/mainwindow.cpp#L829)).
  No call site actually creates/connects it.
- `connectToSatCenterSerial(portName)` / `persistentSerialPort`
  ([mainwindow.cpp:728-785](../Widget/mainwindow.cpp#L728)) — opens a serial port and does
  nothing with incoming data (no `readyRead` handler wired up). No call site invokes
  `connectToSatCenterSerial` anywhere in `mainwindow.cpp`.
- `processSatCenterData(data)` ([mainwindow.cpp:847-857](../Widget/mainwindow.cpp#L847)) — a stub
  that only `qDebug()`-logs a guessed packet type (`SONAR:`/`STATUS:`/`INS:` prefix sniffing). Never
  called from live code.

**Conclusion, restated 2026-08-09 (the earlier wording was wrong):** what looks like a SatCenter data
bridge in *our code* is three half-built or dead paths plus one process/config ceremony that moves no
data. All of that is safe to delete.

But SatCenter itself is not idle. It performs one real, load-bearing job on the live path: it takes
the INS feed from port 6001 and merges it into the sonar's 3101 packets, filling the 24 `reserved2`
bytes with longitude, latitude, heading and height. Sonar data and nav do **not** "flow directly into
the app" — they arrive as one stream precisely because SatCenter combined them.

The distinction that matters: **our SatCenter-facing code is dead; SatCenter's merge is not.** Delete
the former freely. The latter has to keep happening somewhere, and after the migration it happens
inside the towfish.

## What "direct integration" therefore actually means

Not "build a new bridge to replace SatCenter's data bridge" (there isn't a live one to replace).
It means: **replace the external-process liveness dance with an internal state machine.**

Concretely, remove/replace:
- `launchSatCenter`, `killExistingSatCenter`, `launchServerWithChecks`,
  `getRelativeSatCenterBasePath`, `checkAndFixSatCenterConfig` (~150 lines,
  [mainwindow.cpp:1367-1511](../Widget/mainwindow.cpp#L1367))
- The `QProcess`/`taskkill` relaunch cycle in `doActionConnect`
- The `Config.ini` `[SonarDevice]` towfish-vs-local toggle (`ConfigManager::setSonarDeviceToTowfish`
  / `setSonarDeviceToLocal`, `ConfigManager.cpp`) — this becomes an in-app mode flag instead of a
  file write that an external process has to notice
- The `127.0.0.1:16128` probe-as-liveness-check — replace with directly probing the towfish's own
  cmd/data TCP ports (already owned by `NetSonarCmdTcpClient`/`NetSonarDataTcpClient`)

~~Still open / needs real investigation before removal~~ — **all three closed on 2026-07-27**, see
the "A0 open questions" section at the top of this document. Summary: `SatCenterData/` is written by
SatCenter alone and moves into the towfish; `[SerialPort]` is dead on our side *and* dropped by the
vendor; `INSDataNetType` becomes a towfish-internal setting (and mode 3/serial no longer exists).

One genuinely new open item, surfaced by the protocol document:
[NetSonarDataTcpClient.cpp:137-145](../Core/Network/NetSonarDataTcpClient.cpp#L137) dispatches packet
types 2016, 2017, 3000, 3102, 3103, 5000, 5001, 5004, 5005 — but
[the protocol spec](vendor/hytem-data-protocol-v1.0.9.md) documents only **107, 166, 3101, 3102**.
The rest are undocumented. Find out whether they are legacy, internal, or still emitted before
assuming any of that dispatch code can go.

**Sharper version of the same point, 2026-08-09:** the mismatch runs both ways. `166` appears
**nowhere** in `Core/` or `Widget/` — zero grep hits. So we handle nine undocumented types and ignore
the one documented status packet, whose single `sonarState` byte reports whether the sonar's command
and data networks are up. That is exactly the signal the throwaway probe socket at
[mainwindow.cpp:630](../Widget/mainwindow.cpp#L630) exists to fake. Handling 166 is now SPEC
checkpoint A1b, sequenced before A2 for that reason.

## Guardrail in effect

`satcenter/` is edit-blocked by the `.claude/settings.json` PreToolUse hook
(`.claude/hooks/guard-vendor-paths.js`) — reference it, don't edit it. If a real reason to edit it
surfaces (e.g. confirming the SatCenterData file format), that's a signal to re-open the guardrail
decision with the user, not to work around the hook.
