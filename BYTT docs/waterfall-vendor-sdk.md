---
title: Waterfall display — vendor SDK vs in-house replacement
status: living document
---

## Current state (verified)

`HydroMainWindow::initMainViews()` instantiates **`SonarPlotWindow`**
([mainwindow.cpp:1042](../Widget/mainwindow.cpp#L1042)), a closed-source vendor widget from
`3rd/Hydro_3101d/` (headers only + prebuilt `Hydro_3101.dll`/`Hydro_3101d.dll`, no `.cpp`).

`HydroWaterfallPlotWidget` ([Widget/MainView/HydroWaterfallPlotWidget.cpp](../Widget/MainView/HydroWaterfallPlotWidget.cpp))
is **dead code** — 14 lines, only calls `ui.setupUi(this)`, never instantiated anywhere in
`mainwindow.cpp`. It reads as an abandoned or paused start on an in-house replacement.

Decision made (2026-07-25): **finish `HydroWaterfallPlotWidget` into a real replacement for
`SonarPlotWindow`**, using the vendor's own packet format headers as a spec rather than
reverse-engineering the binary.

## What the vendor package gives us (from `3rd/Hydro_3101d/include/`)

| File | Contents |
|---|---|
| `SonarPlotWindow.h` | Public widget API to match/exceed: `setSonarData`, `setSonarImage(QImage, range)`, `setWaveformData(QVector<float>)`, `setColorMap`, `setColorMapInversion`, `setBrightnessPercentage`, `setStaticText*`, `setPeakLabelFontColor`, visibility toggles per element, signals `plotResized`, `sonarImageUpdated`, `hytem3101Received` |
| `3101DataProcessor.h` | Declares `SonarData` (impl. compiled into the DLL, not available) — shows the *shape* of the processing: `onRecvedHytem3101(QByteArray)` slot in, buffers raw packets in `std::deque<QByteArray>` up to `maxBufferHeight`, `generateSonarImage(data, type)`, color mapping via `QVector<QColor>` lookup table + `mapValueToColor`, brightness factor multiply, emits `sonarImageGenerated(QImage, range)` + `waveformDataGenerated(QVector<float>)` |
| `ColorMap.h` | `enum class ColorMapType { Brown, Prismy, Gray, Jet }` — matches `Core/Tool.h`'s own `colorsEnum` (`brown, prismy, gray, jet`), so the app already has a compatible concept on our side |
| `HytemDataFormatDef.h` | **Raw Hytem wire-protocol struct definitions** — this is the actual parsing spec, no reverse-engineering needed. As of 2026-07-27 it is corroborated by an open vendor document, see below |
| `net_packet_def.h` | Lower-level packet framing definitions |
| `qcustomplot.h` | Vendor uses `QCustomPlot` (a well-known open-source Qt plotting widget) for the waveform pane — we can depend on the same library directly, it's not vendor-proprietary |

## Rendering technique inferred from the header shape (not guessed — this is what the member
variables and method signatures imply)

- Maintain a scrolling `QImage waterfallImage` capped at `maxLines` rows; each new ping appends a
  row and the image scrolls
- Displayed via `QLabel` inside a `QScrollArea` (not a `QGraphicsView`/OpenGL — deliberately simple)
- Intensity → color via a precomputed `QVector<QColor>` lookup table sized to the color map, not a
  per-pixel formula — `setColorMap`/`setColorMapInversion`/`setBrightnessPercentage` all just
  rebuild or reindex this table
- Separate `QCustomPlot`-based waveform strip alongside the image, fed by
  `setWaveformData(QVector<float>)`
- A `QQueue<ImageData>` + `QMutex` buffer decouples the network/processing thread from the paint
  thread — matches the existing project pattern of dedicated `QThread`s per component (see
  `Core/Tool.h`)
- Resize handling is debounced via a `QTimer` (`resizeTimer`, 150ms) before recomputing the scale
  image — worth copying, avoids redrawing on every intermediate resize event during a drag

## 2026-07-27 — the wire protocol is now an open document

[vendor/hytem-data-protocol-v1.0.9.md](vendor/hytem-data-protocol-v1.0.9.md) (Hytem Data Protocol,
SS Series, V1.0.9) is the published spec for everything `HytemDataFormatDef.h` previously gave us
only as C headers. **The two agree** — spot-checked against
`3rd/Hydro_3101d/include/HytemDataFormatDef.h`:

| Fact | Protocol doc | Vendor header |
|---|---|---|
| Magic identifier | `0x004D5448` (`"HTM\0"`) | line 38, `UD_HYTEM_PACKET_HEADER` |
| Packet type 3101 | side-scan intensity | line 43, `UD_HYTEM_PACKET_TYPE_SIDE_SCAN` |
| `DefPacketHeader` | 24 bytes | line 53 |
| 3101 fixed part | 128 bytes | line 151, `_DefDataSideScan` |
| `sampleLength` | `u32` (changed from `f32` in V1.0.4) | line 172, `u32 sampleLength` |

Conventions that matter for parsing: **little-endian**, fields **4-byte aligned**, every packet is
`24-byte header + content + u32 checkSum` trailer. The checksum is the byte-sum from header start
through end of content, or the fixed sentinel `0x77EEEE77` when unused — which of the two applies is
given by **bit 1 of `packetFlag`**. Bit 0 of the same field flags multi-packet frames.

**3101 payload layout** (this is what B1 renders): 128-byte fixed part carrying `synTime`,
`pingNumber`, `sonarRange` (in **cm**), `signalTypes`, `bandWidth`, `pulseWidth`, `centerFreq`,
`gain`, `spreading`, `absorption`, `soundSpeed`, `sampleRate`, `sampleLength` — followed by
`u16 dataValue[2 * sampleLength]`, ordered **all left-side samples first, then all right-side
samples**. Not interleaved. That ordering is the single most important detail for getting the
waterfall image correct.

Note the app already relies on `sonarRange` being centimetres —
[NetSonarDataTcpClient.cpp:126](../Core/Network/NetSonarDataTcpClient.cpp#L126) divides by 100.0f.

Also documented: **3102** (pre-rendered grayscale image plus per-line lat/lon/heading/speed/height —
note lat/lon are `double`s split into pairs of `f32` for alignment), **107** (the work-control
command we *send*), and **166** (connection status). B1 needs only 3101.

**Caveat for later cleanup:**
[NetSonarDataTcpClient.cpp:137-145](../Core/Network/NetSonarDataTcpClient.cpp#L137) also dispatches
types 2016, 2017, 3000, 3103, 5000, 5001, 5004, 5005, none of which appear in this protocol
document. Do not assume they are dead — find out.

## What this means for scope

This is a real, scoped build — not exploratory reverse engineering. The packet format is fully
specified (`HytemDataFormatDef.h`), the target API is fully specified (`SonarPlotWindow.h`, which
`HydroWaterfallPlotWidget` should aim to match so it's a drop-in replacement at
[mainwindow.cpp:1042](../Widget/mainwindow.cpp#L1042) and the `m_pLibControlPanel` signal
connections around it, e.g. [mainwindow.cpp:1069-1073](../Widget/mainwindow.cpp#L1069)), and the
rendering approach is a standard, well-understood Qt technique.

## Also vendor/closed-source, not in scope to modify

`LibControlPanel` (`3rd/controlPanel/`) drives the sonar-parameter and display-setting side panels
([mainwindow.cpp:1064-1067](../Widget/mainwindow.cpp#L1064)) — same closed-SDK situation as
`SonarPlotWindow`. Not part of the current waterfall-replacement decision; flag separately if it
becomes relevant.

## Guardrail in effect

`3rd/` is edit-blocked by the `.claude/settings.json` PreToolUse hook — read the headers for
reference, never edit them (editing wouldn't change the compiled `.dll`'s behavior anyway).
