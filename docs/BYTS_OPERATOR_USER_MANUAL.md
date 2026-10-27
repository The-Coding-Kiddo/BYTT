# BYTT Sonar Viewer — Operator Software User Manual

**Software:** BYTT Sonar Viewer  
**Package:** `bytt`  
**Execution:** `python -m bytt.app`  

---

## 1. Introduction

The **BYTT Sonar Viewer** is a desktop application for sidescan sonar visualization, real-time survey acquisition, hardware command control, and post-survey data playback. 

The software has two operational modes:
* **Live Towfish Mode:** Connects over TCP to a sonar towfish (or subsea multiplexer) to stream real-time acoustic imagery, issue transceiver operating commands, display GPS tracks with live swath coverage, and record raw sonar data to disk.
* **Playback Mode:** Opens and replays recorded `.bsf` (Binary Sonar File) containers with play/pause, ping stepping, scrubbing, speed controls, and full post-processing enhancement.

---

## 2. Installation & Launching the Software

### 2.1 Dependencies
The software requires Python 3.11+ and the following packages:
* `PySide6` (Qt 6 GUI framework)
* `pyqtgraph` (Waterfall and navigation plotting)
* `numpy` (Array processing and signal normalization)
* `opencv-python` (Image filtering, CLAHE, and tone mapping)

### 2.2 Launch Command
From the root of the repository:
```bash
python -m bytt.app
```
The application opens a main window sized at 1200×800 pixels with three dock panels and a central waterfall display.

---

## 3. Configuration & Startup Settings

### 3.1 The Configuration File (`~/.bytt/bytt.ini`)
The application reads its default connection parameters from `~/.bytt/bytt.ini`. If the file does not exist, default values are used and saved automatically.

```ini
[Sonar]
towfiship = 192.168.1.16
cmdport = 16128
dataport = 16129
source = playback
pcip = 192.168.1.10
```

#### Configuration Fields:
| Setting | Default Value | Description |
| :--- | :---: | :--- |
| `TowfishIP` | `192.168.1.16` | The IP address of the towfish or embedded multiplexer. |
| `CmdPort` | `16128` | TCP port for the outbound command channel (Packet 107). |
| `DataPort` | `16129` | TCP port for the inbound data channel (Pings 3101 & Status 166). |
| `Source` | `playback` | Initial startup mode (`towfish` or `playback`). |
| `PcIP` | `""` | The operator PC's own IPv4 address. If provided, the application checks whether `TowfishIP` and `PcIP` share the same `/24` subnet and warns in the status bar if they do not. |

### 3.2 UI Layout State (`bsf_viewer_config.json`)
The application stores GUI dock dimensions, track panel docking state, and HDR toggle preferences in `bsf_viewer_config.json` in the working directory:
```json
{
  "dock_w": 380,
  "show_track": false,
  "track_docked": false,
  "hdr": false
}
```

---

## 4. Main Window & Interface Layout

```
+--------------------------------------------------------------------------------------------------------+
| Connect   View                                                      [● REC]  [● Data  ● Cmd  ● Sonar]  |
+--------------------------------------------------------------------------------------------------------+
| [Play] [Step Back] [Step Forward]  Ping: 120/4500  [====O========]  [−] 4× [+]  [Home]                 |
+---------------------------------------+----------------------------------------------------------------+
| DOCK: GPS (Left Area)                 | CENTRAL WATERFALL DISPLAY                                      |
| +-----------------------------------+ |                                                                |
| | Track Plot (PyQtGraph)            | | • Width: 2048 px                                               |
| |                                   | | • Height: 1000 rows buffer                                     |
| | Waypoint List:                    | | • Orientation: Top-to-bottom scroll                           |
| | • WP1 (23m @ 042°)                | | • Row 0 = newest ping                                         |
| | [Add Waypoint] [Remove Waypoint]  | | • Left half: Port channel (Near -> Far)                       |
| +-----------------------------------+ | • Center: Nadir divider                                        |
| Lat: 41.20458°N   Lon: 29.03452°E   | • Right half: Starboard channel (Near -> Far)                  |
| Spd: 3.4 m/s                        |                                                                |
+---------------------------------------+----------------------------------------------------------------+
|                                       | DOCK: ENHANCEMENT CONTROLS (Right Area)                        |
|                                       | • Palette:       [Amber                 ▾]                     |
|                                       | • Gain:          [ 1.00 ]                                      |
|                                       | • Gamma:         [ 1.00 ]                                      |
|                                       | • Denoise:       [Off                   ▾]                     |
|                                       | • Contrast:      [CLAHE Low             ▾]                     |
|                                       | • AGC:           [ ]                                           |
|                                       | • Target Enh:    [Off                   ▾]                     |
|                                       | • Target KSize:  [ 15 ]                                        |
|                                       | • Shadow Enh:    [ ]                                           |
|                                       | • Overlay:       [ ]                                           |
|                                       | • Sharpen:       [ ]                                           |
|                                       | • HDR:           [ ]                                           |
|                                       | • [ Reset to Defaults ]                                        |
|                                       | • Port Channel:  [X]      Starboard Channel: [X]               |
|                                       |----------------------------------------------------------------|
|                                       | DOCK: SONAR CONTROL (Right Area)                               |
|                                       | • HF Channel:    [X]  Range: [50 m]  Gain: [10 dB]             |
|                                       | • LF Channel:    [X]  Range: [50 m]  Gain: [10 dB]             |
|                                       | • [ Start / Stop ]          [ Apply ]                          |
+--------------------------------------------------------------------------------------------------------+
| Status: Connected | Ping rate: 16 Hz                                                                  |
+--------------------------------------------------------------------------------------------------------+
```

---

## 5. Operating Modes

### 5.1 Live Towfish Operation

#### Connecting to the Towfish
1. Click **Connect -> Connect to Towfish...** (or press the menu shortcut).
2. A dialog opens prompting for the **Towfish IP** (pre-populated from `bytt.ini`).
3. Click **OK**.
4. The application opens two background connections:
   * **Data Stream (`LiveClient` on Port 16129):** Receives ping datagrams (Type 3101) and link status reports (Type 166).
   * **Command Stream (`CommandClient` on Port 16128):** Transmits parameter updates to the sonar.

#### Status Bar Connection Indicators
The three permanent colored dots in the bottom-right status bar display the connection state:
* **Data:**
  * 🟢 **Green (`ok`):** Socket is connected and ping data is arriving.
  * 🟠 **Orange (`warning`):** Socket connected, but no pings received for $>5$ seconds.
  * 🔴 **Red (`error`):** Socket disconnected or failed to connect.
* **Cmd:**
  * 🟢 **Green (`ok`):** Command socket to port 16128 is connected.
  * 🔴 **Red (`error`):** Command socket disconnected or connection refused.
* **Sonar:**
  * 🟢 **Green (`ok`):** Towfish internal self-report indicates both internal data and command buses are healthy.
  * 🔴 **Red (`error`):** Multiplexer is reachable, but the sonar transceiver behind it is offline.

#### Controlling the Sonar Hardware (Sonar Control Panel)
The controls in this panel send binary **Packet 107** commands across port 16128:
* **HF Channel:** Checkbox to enable or disable the High-Frequency transceiver.
* **HF Range:** Slant range in meters (1 to 500 m, default: 50 m).
* **HF Gain:** Hardware receiver pre-amplifier gain (0 to 100 dB, default: 10 dB).
* **LF Channel:** Checkbox to enable or disable the Low-Frequency transceiver.
* **LF Range:** Slant range in meters (1 to 500 m, default: 50 m).
* **LF Gain:** Hardware receiver pre-amplifier gain (0 to 100 dB, default: 10 dB).
* **Start / Stop Button:** Starts or stops acoustic pinging. The button label updates to reflect the active state.
* **Apply Button:** Dispatches the current range, gain, and channel enable settings to the hardware without toggling the run state.

#### Recording Raw Data
* To record incoming telemetry, click **Connect -> Record**.
* When active, a bold red **`● REC`** indicator appears in the status bar.
* Raw network packets are written directly to disk under the `recordings/` folder (formatted as `recordings/YYYYMMDD_HHMMSS.bsf`).
* Unchecking **Record** stops recording and closes the file cleanly.

---

### 5.2 Playback Operation

#### Loading a Recording
1. Click **Connect -> Open Playback File...**.
2. Select any valid `.bsf` survey file.
3. The application parses all pings and navigation fixes, runs automatic amplitude dynamic-range calibration across the file, and displays the **Playback Toolbar**.

#### Playback Toolbar Controls
* **Play / Pause (`Space`):** Starts or stops automatic playback. Default playback rate is 4× survey speed.
* **Step Back (`Left Arrow`):** Moves backward by one ping.
* **Step Forward (`Right Arrow`):** Moves forward by one ping.
* **Scrub Slider:** Drag and release to seek to any position in the file. The ping counter displays `Current / Total`.
* **Speed Decrement (`−` or `Down Arrow`):** Decreases speed step-by-step (`1×`, `2×`, `4×`, `8×`, `16×`, `32×`, `max`).
* **Speed Increment (`+` or `Up Arrow`):** Increases speed step-by-step.
* **Home (`Home`):** Seeks back to the very first ping (Ping index 0).

---

## 6. Enhancement Controls Panel

The **Enhancement Controls** panel modifies the software visualization in real time. Adjustments are debounced by 150 ms to ensure responsiveness without stalling the display.

```
+------------------------------------------+
| Palette:          [Amber              ▾] |
| Gain:             [ 1.00 ]               |
| Gamma:            [ 1.00 ]               |
| Denoise:          [Off                ▾] |
| Contrast:         [CLAHE Low          ▾] |
| AGC:              [ ]                    |
| Target enhance:   [Off                ▾] |
| Target ksize:     [ 15 ]                 |
| Shadow enhance:   [ ]                    |
| Overlay:          [ ]                    |
| Sharpen:          [ ]                    |
| HDR:              [ ]                    |
| [ Reset to Defaults ]                    |
| Port channel:     [X]                    |
| Starboard channel:[X]                    |
+------------------------------------------+
```

### Control Details:

#### 1. Palette
Selects the false-color colormap for the waterfall display:
* `Amber`: Traditional warm amber-to-yellow sonar palette.
* `Copper`: High-contrast reddish-gold palette.
* `Bronze`: Earthy brown-gold gradient.
* `Green`: Classic phosphor green radar/sonar display.
* `Bone`: Grayscale palette (useful for measuring fine edge details).

#### 2. Gain & Gamma
* **Gain (0.10 to 5.00, default: 1.00):** Linear brightness multiplier applied to acoustic samples before color mapping.
* **Gamma (0.10 to 3.00, default: 1.00):** Non-linear power-law curve. Values $< 1.0$ lift darker midtones and shadows; values $> 1.0$ compress shadows and emphasize bright target highlights.

#### 3. Denoise Modes
* `Off`: Raw unconditioned pixels.
* `Median`: $3 \times 3$ median filter. Removes sharp single-pixel electrical spikes.
* `Bilateral`: Edge-preserving spatial/radiometric filter ($d=5, \sigma_{\text{color}}=20, \sigma_{\text{space}}=10$). Attenuates acoustic speckle without softening point targets.
* `NL-Means`: Non-local means denoising ($h=7, \text{template}=7, \text{search}=15$). Performs texture smoothing across granular seabeds.

#### 4. Contrast Modes
* `Off`: Linear normalized contrast.
* `Global HistEq`: Standard histogram equalization across the full buffer.
* `CLAHE Low` *(Default)*: Adaptive histogram equalization with clip limit `1.5` over an $8 \times 8$ grid.
* `CLAHE Med`: Clip limit `3.0`.
* `CLAHE High`: Clip limit `6.0` (maximum local edge separation).

#### 5. AGC (Automatic Gain Control)
* Stretches the 1st and 99th percentiles of the current image frame across 0–255 to maximize visible dynamic range.

#### 6. Target Enhancement & Structuring Element
* `Off`: Standard imaging.
* `Top-Hat Bright`: Applies morphological white top-hat filter ($I - (I \circ K)$) to extract bright target returns smaller than kernel size $K$.
* `Top-Hat Bright+Dark`: Applies both white and black top-hat filters to enhance both bright targets and their corresponding dark acoustic shadows.
* `CFAR Contrast`: Computes local mean and standard deviation via box filters and scales pixels by local Z-score. Normalizes detection in uneven or rocky terrain.
* **Target Kernel Size (5 to 51, step 4, default: 15):** The size in pixels ($K \times K$) of the morphological structuring element. Adjust to match the expected pixel footprint of targets.

#### 7. Shadow Enhance
* Blurs the image to estimate background illumination, then boosts negative contrast differences by $1.6\times$ to deepen acoustic shadows behind standing objects.

#### 8. Overlay
* Detects the top 3% brightest morphological targets and deepest shadows, rendering them with color highlights directly on the waterfall:
  * **Targets:** Fluorescent green tint (`RGB: 60, 255, 140`)
  * **Shadows:** Magenta tint (`RGB: 255, 60, 200`)

#### 9. Sharpen
* Applies a Gaussian unsharp mask ($1.6 \times I - 0.6 \times G_{\sigma=1.4}$) to sharpen acoustic boundaries and small seabed ripples.

#### 10. HDR (Smart HDR Tone Mapping)
* Decomposes the frame into a large-scale illumination base layer and a detail layer. 
* Lifts dark shadows ($b^{0.55}$) and compresses specular highlights ($1.0 - (1.0 - b)^{1.8}$) with an adaptive detail boost. Prevents bright bottom returns near nadir from blowing out while keeping deep shadows legible.

#### 11. Channel Toggles (Port & Starboard)
* **Port channel [X] / Starboard channel [X]:**
  * Both checked: Standard dual-channel display with a center divider.
  * Single channel checked: The disabled channel is removed, and the remaining channel automatically expands to fill the entire window width.

#### 12. Reset to Defaults
* Resets all sliders, combos, and checkboxes to default values (`Amber`, `Gain=1.0`, `Gamma=1.0`, `CLAHE Low`, `Kernel=15`, filters off).

---

## 7. GPS & Navigation Panel

The **GPS** panel shows georeferenced position data, vessel track history, and real-time swath coverage on a local metric Cartesian grid.

```
+------------------------------------------+
|  [ + Add Waypoint ] [ - Remove ]         |
|                                          |
|            PyQtGraph Map Area            |
|  • Black line: Vessel trackline          |
|  • Cyan arrow: Current position/heading  |
|  • Shaded band: Real-time acoustic swath |
|  • Red diamonds: Waypoints               |
|                                          |
| Waypoints:                               |
| • WP1: 41.20458°N, 29.03452°E (45m @ 030°)|
+------------------------------------------+
| Latitude:  41.204582°N                   |
| Longitude: 029.034521°E                  |
| Heading:   042.5°                        |
| Speed:     3.4 m/s                       |
+------------------------------------------+
```

### Features:
1. **Local Tangent Projection:** Converts incoming WGS-84 fixes into local metric coordinates $(X, Y)$ relative to the first received fix.
2. **Physical Swath Polygon:** Displays the actual surveyed seabed area:
   * **Outer Edge:** Determined from the measured acoustic noise floor (where return signal drops below background noise).
   * **Inner Edge:** Determined by the nadir gap / water column return.
3. **Waypoints:**
   * Click **Add Waypoint** to drop a marker at the current vessel position.
   * The list shows the waypoint name, coordinates, distance in meters, and true bearing from the current vessel position.
   * Select a waypoint and click **Remove Waypoint** to delete it.
4. **Vessel Heading & Speed:**
   * Heading is taken from the INS navigation block in Packet 3101.
   * Speed is derived from the distance and arrival time of consecutive GPS fixes.

---

## 8. Keyboard Shortcut Summary

| Shortcut | Context | Function |
| :---: | :---: | :--- |
| `Space` | Playback | Toggle Play / Pause |
| `Left Arrow` | Playback | Step backward 1 ping |
| `Right Arrow` | Playback | Step forward 1 ping |
| `Up Arrow` | Playback | Increase replay speed (`1×` to `max`) |
| `Down Arrow` | Playback | Decrease replay speed (`max` to `1×`) |
| `Home` | Playback | Return to start of recording (Ping 0) |
| `Q` or `Esc` | Global | Close and exit application |

---

## 9. Troubleshooting Common Software States

| Symptom / Status | Cause | Solution |
| :--- | :--- | :--- |
| **"warning: towfish and PC are not on same segment"** | `TowfishIP` and `PcIP` in `bytt.ini` have mismatched `/24` prefixes (e.g. `192.168.1.16` vs `192.168.2.10`). | Edit `~/.bytt/bytt.ini` or reconfigure your PC network adapter so both IP addresses share the same subnet prefix (e.g., `192.168.1.X`). |
| **Data indicator turns Orange (`warning`)** | Socket is connected, but no pings have arrived for $>5.0$ seconds. | Click **Start** in the Sonar Control Panel to verify transmission is enabled, or check if the towfish acquisition stopped. |
| **Cmd indicator is Red** | Port 16128 connection failed. | Check that no other process has claimed port 16128 and verify the towfish IP address. |
| **Sonar indicator is Red** | Subsea multiplexer answered, but internal telemetry to the transceiver board failed. | The subsea transceiver is offline or unpowered. Power cycle the towfish. |
| **Waterfall appears all black** | Hardware gain is at 0 dB or software gain is too low. | Increase **HF/LF Gain** in Sonar Control Panel, click **Apply**, or click **Reset to Defaults** in Enhancement Controls. |
| **Single channel fills the whole window** | One channel checkbox was disabled in the controls panel. | Check both **Port channel** and **Starboard channel** in Enhancement Controls if dual-channel view is desired. |
