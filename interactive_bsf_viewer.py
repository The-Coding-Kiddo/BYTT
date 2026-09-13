#!/usr/bin/env python3
"""
bsf_viewer.py  –  Live-scrolling side-scan sonar waterfall viewer
==================================================================
Core controls:
SPACE           – play / pause
LEFT/RIGHT      – step one ping (paused)
scroll wheel    – zoom in/out
double-click    – zoom into the waterfall at that point (double-click
                  again to zoom back out)
click scrub bar – jump to position
TAB             – open/close the Controls panel (clickable buttons
                  for every setting: gain, gamma, denoise, contrast,
                  target/shadow enhancement, channels, palette, etc.)
F1              – open/close the Keybindings reference panel
M               – open/close the GPS Track panel
L               – cycle colour palette
Q / Esc         – quit  (Esc goes Home first, or closes a panel)

Everything else — the full OpenCV-based real-time enhancement pipeline
(noise reduction, AGC, CLAHE/histogram equalisation, sharpening, and the
sonar-specific target/shadow/background-suppression stage) — lives behind
the TAB panel so the main view stays uncluttered. Press F1 in the app for
the complete keyboard shortcut list.

Performance optimisations in this build:
  • Reused canvas surface — the draw loop no longer allocates a 2–5 MB
    Pygame surface every single frame; the canvas is reallocated only
    when the window/canvas size actually changes.
  • Fast view zoom — pygame.transform.scale instead of smoothscale
    (≈3× faster, visually identical on sonar imagery), writing into a
    reused destination surface so zoomed redraws allocate nothing.
  • Seek rebuild — _jump_to no longer redraws/enhances every 200 rows;
    it just pumps OS events every 1 000 rows and enhances once at the end.
  • Duplicate-job guard — the main thread won't resubmit the same
    raw-data + params job if the worker is already processing it.
  • Deferred dock resize — dragging the GPS panel divider only updates
    layout coordinates live; the expensive waterfall rebuild happens
    once on mouse-release.
  • Adaptive work budget — when playing/live the pipeline automatically
    works at ≤600 kpx instead of 1.5 Mpx, snapping back to full
    resolution when paused.
  • Skip enhancement during rebuild — the draw loop ignores enhancement
    while the waterfall is being reconstructed.

Requirements:
    pip install pygame numpy opencv-python
"""
import struct, sys, os, time, datetime, threading, multiprocessing, math, socket, queue, json
from collections import namedtuple
import numpy as np

def _fatal_import_error(missing_pkg, pip_cmd):
    msg = (f"BSF Viewer could not start: '{missing_pkg}' is not installed "
           f"in this Python environment.\n\nRun:\n    {pip_cmd}\n\n"
           f"Python executable in use:\n    {sys.executable}\n\n"
           "(If you installed these packages before, double-clicking the "
           ".py file may be launching a different Python than the one you "
           "installed them into — running from a terminal with the same "
           "'python'/'python3' you used for pip install will confirm this.)")
    print(msg, file=sys.stderr)
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk(); root.withdraw()
        messagebox.showerror("BSF Viewer — Missing dependency", msg)
        root.destroy()
    except Exception:
        pass
    sys.exit(1)

try:
    import pygame
except ImportError:
    _fatal_import_error("pygame", "pip install pygame numpy opencv-python")

try:
    import cv2
except ImportError:
    _fatal_import_error("opencv-python", "pip install opencv-python")

# Let OpenCV's internal thread pool use every core for the pixel-level work
try:
    cv2.setUseOptimized(True)
    cv2.setNumThreads(max(1, multiprocessing.cpu_count()))
except Exception:
    pass


# ── EnhanceParams: split into heavy (expensive pipeline) and light (LUT only) ──
# Heavy params change → full pipeline re-run
# Light params change → reuse cached enhanced grayscale, just re-colorize
EnhanceParams = namedtuple('EnhanceParams', [
    'noise_idx', 'contrast_idx', 'agc', 'target_idx', 'target_ksize',
    'shadow_enh', 'overlay', 'sharpen', 'gain', 'gamma', 'lut_idx', 'fast', 'hdr',
])

HEAVY_FIELDS = ('noise_idx', 'contrast_idx', 'agc', 'target_idx',
                'target_ksize', 'shadow_enh', 'overlay', 'sharpen', 'hdr')


def _heavy_key(p):
    """Extract the subset of params that affect the expensive pipeline."""
    return tuple(getattr(p, f) for f in HEAVY_FIELDS)


# ── BSF constants ─────────────────────────────────────────────────────────────
BSF_MAGIC        = b'\x0fHSFV1.1.000'
BSF_FILE_HDR_SZ  = 0x400
BSF_RECORD_TYPE  = 0x00101702
# Was 240. Verified against (record_size - sample_bytes_needed) across every
# ping in a real file: offset 216 matches exactly, 100% of pings; 240 came up
# 24 bytes (6 samples) short on every single one and was silently zero-padded.
# The old value quietly misread every ping, splicing ~6 samples of the
# opposite channel onto the tail of port and zeroing the tail of stbd.
SAMPLE_OFFSET    = 216
CH_SR_OFFSET     = 84
CH_SR_SIZE       = 56

# ── Amplitude decode / normalization ──────────────────────────────────────────
# The 32-bit sample word is a plain linear amplitude — NOT a 24-bit value with
# 8 flag/status bits on top. Masking with 0x00FFFFFF (the previous behaviour)
# truncates any echo whose true amplitude exceeds 2^24 (~16.7M): strong
# returns — the seafloor itself, and small hard targets like rebar/rock/debris
# — wrap around modulo 2^24 instead of clipping to white, which scrambles or
# outright hides exactly the reflectors you most want to see. Real amplitudes
# in this data run up to ~2^28 (16x past the mask), confirmed by inspecting
# raw sample words directly.
#
# The full-range values still span several orders of magnitude (near-field
# returns vs. strong mid-range bottom echoes), so we log-compress rather than
# scale linearly, then stretch the log values between two calibrated
# percentiles into 0..1. AMP_NORM_P_LO/HI below are generic fallbacks (used
# before a file is loaded / during live streaming); _load_file() replaces
# them with values calibrated from the actual file once it's read.
AMP_NORM_P_LO_DEFAULT = 12.4
AMP_NORM_P_HI_DEFAULT = 18.9

# ── GPS/nav fix records ───────────────────────────────────────────────────────
NAV_RECORD_SIZE = 156
NAV_TS_OFFSET   = 64
NAV_LAT_OFFSET  = 80
NAV_LON_OFFSET  = 88
NAV_ALT_OFFSET  = 96
NAV_FIELD_FMT   = '<d'

# ── Live network protocol (Hytem Data Protocol, SS series) ────────────────────
LIVE_DEFAULT_HOST = "192.168.1.31"
LIVE_DEFAULT_PORT = 16129
PACKET_IDENTIFIER       = 0x004D5448
PACKET_IDENTIFIER_BYTES = struct.pack('<I', PACKET_IDENTIFIER)
PACKET_HEADER_SIZE      = 24
PACKET_TYPE_OFFSET      = 20
PACKET_SIZE_OFFSET      = 8
PACKET_TYPE_3101        = 3101
PACKET_TYPE_166         = 166
PACKET_FLAG_OFFSET       = 12
PACKET_FRAME_ID_OFFSET   = 14
PACKET_TOTAL_OFFSET      = 16
PACKET_NUMBER_OFFSET     = 18
PACKET_FLAG_MULTI_BIT    = 0x0001
PACKET_FLAG_CHECKSUM_BIT = 0x0002
PACKET_CHECKSUM_NONE     = 0x77EEEE77

B3101_SYNTIME_OFF   = 16
B3101_PINGNUM_OFF    = 28
B3101_SONARRANGE_OFF = 32
B3101_CENTERFREQ_OFF = 52
B3101_SPREADING_OFF  = 60
B3101_ABSORPTION_OFF = 64
B3101_SAMPLERATE_OFF = 72
B3101_SAMPLELEN_OFF  = 76
B3101_BODY_SIZE      = 104
B3101_DATA_OFF       = PACKET_HEADER_SIZE + B3101_BODY_SIZE

MULTI_PACKET_FRAME_TIMEOUT_S = 2.0

# ── Layout constants ─────────────────────────────────────────────────────────
SCRUB_H   = 36
TOOLBAR_H = 56
GAP_W     = 20

# ── Real-time enhancement pipeline ───────────────────────────────────────────
NOISE_MODES    = ["Off", "Median", "Bilateral", "NL-Means"]
CONTRAST_MODES = ["Off", "Global HistEq", "CLAHE Low", "CLAHE Med", "CLAHE High"]
# Was 3 (CLAHE Med), briefly 4 (CLAHE High) while AMP_NORM_* below was still
# a 24-bit-masked linear decode papering over lost dynamic range. Now that
# extract_raw_channels() does a proper log/percentile stretch on the full
# amplitude, most of the contrast work is already done before this stage —
# CLAHE Low adds a little local pop without smearing small hard-target
# returns the way a higher clip limit does.
CONTRAST_DEFAULT_IDX = 2

# ── Sonar-specific enhancement ────────────────────────────────────────────────
TARGET_MODES     = ["Off", "Top-Hat Bright", "Top-Hat Bright+Dark", "CFAR Contrast"]
TARGET_KSIZE_MIN = 5
TARGET_KSIZE_MAX = 51
TARGET_KSIZE_STEP = 4
TARGET_KSIZE_DEFAULT = 15

# ── Performance: work-resolution cap ──────────────────────────────────────────
WORK_BUDGET_PX      = 1_500_000   # paused, seeking, or ordinary file playback
WORK_BUDGET_PX_NLM  = 40_000      # NL-Means is too slow above this size
WORK_BUDGET_PX_LIVE = 600_000     # genuine live acquisition only — see fast= in _update_enhancement

# ── GPS Track dock ────────────────────────────────────────────────────────────
DOCK_W = 380

# ── Zoom ──────────────────────────────────────────────────────────────────────
ZOOM_STEPS   = [0.25, 0.33, 0.5, 0.67, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0]
ZOOM_DEFAULT = 5
VIEW_ZOOM_FACTOR  = 3.0
DOUBLE_CLICK_MS   = 350
DOUBLE_CLICK_DIST = 6

# ── Selection / marks ────────────────────────────────────────────────────────
RECT_MIN_DRAG_PX = 4          # pixels of movement before a click becomes a drag
MARK_COLOURS = [
    (0,   255, 255),   # cyan
    (255, 255,   0),   # yellow
    (255,   0, 255),   # magenta
    (0,   255,   0),   # green
    (255, 128,   0),   # orange
    (160,  80, 255),   # purple
]

# ── Colours ───────────────────────────────────────────────────────────────────
C_BG        = (10,  12,  18)
C_TOOLBAR   = (18,  22,  32)
C_SCRUB_BG  = (14,  17,  26)
C_ACCENT    = (0,  200, 160)
C_ACCENT2   = (255,  80,  80)
C_WARN      = (255, 180,  40)
C_TEXT      = (200, 210, 220)
C_DIM       = (80,  95, 110)
C_PLAYHEAD  = (255, 210,  50)
C_OFF       = (60,  30,  30)
C_TICK      = (140, 150, 170)
C_END_BADGE = (255, 100,  40)

C_TRACK_BG     = (10,  30,  55)
C_TRACK_GRID   = (30,  60,  95)
C_TRACK_AXIS   = (70, 120, 160)
C_TRACK_LABEL  = (140, 175, 205)
C_TRACK_TRAIL_DIM    = (60, 130, 170)
C_TRACK_TRAIL_BRIGHT = (110, 210, 255)
C_TRACK_SHIP   = (255, 210,  50)

SPEED_STEPS  = [80, 40, 20, 10, 5, 2, 0]
SPEED_LABELS = ["1×", "2×", "4×", "8×", "16×", "32×", "MAX"]
DEFAULT_SPEED_IDX = 2

# ── Settings persistence ─────────────────────────────────────────────────────
CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "bsf_viewer_config.json")


# ── Windows DPI / HiDPI awareness ─────────────────────────────────────────────
def _enable_dpi_awareness():
    if sys.platform != "win32":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


# ── Windows dark title bar ────────────────────────────────────────────────────
def _style_windows_titlebar(hwnd):
    if sys.platform != "win32" or not hwnd:
        return
    try:
        import ctypes
        dwmapi = ctypes.windll.dwmapi
        def _set(attr, value):
            v = ctypes.c_int(value)
            dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), attr,
                                         ctypes.byref(v), ctypes.sizeof(v))
        def _bgr(rgb):
            r, g, b = rgb
            return r | (g << 8) | (b << 16)
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        DWMWA_CAPTION_COLOR           = 35
        DWMWA_TEXT_COLOR              = 36
        DWMWA_BORDER_COLOR            = 34
        _set(DWMWA_USE_IMMERSIVE_DARK_MODE, 1)
        _set(DWMWA_CAPTION_COLOR, _bgr(C_TOOLBAR))
        _set(DWMWA_TEXT_COLOR,    _bgr(C_TEXT))
        _set(DWMWA_BORDER_COLOR,  _bgr(C_ACCENT))
    except Exception:
        pass


def _apply_titlebar_style():
    if sys.platform != "win32":
        return
    try:
        wm_info = pygame.display.get_wm_info()
        hwnd = wm_info.get('window')
        if hwnd:
            _style_windows_titlebar(hwnd)
    except Exception:
        pass


# ── Loader screen ─────────────────────────────────────────────────────────────
class Loader:
    def __init__(self, screen, font_mono, font_big):
        self.screen    = screen
        self.font_mono = font_mono
        self.font_big  = font_big
        self._lines    = []

    def set_stage(self, text):
        self._lines = [(text, (200, 210, 220))]
        self._render()

    def set_progress(self, label, current, total):
        pct     = 100.0 * current / max(1, total)
        bar_w   = 400
        filled  = int(bar_w * current / max(1, total))
        self._progress = (label, pct, bar_w, filled, current, total)
        self._render()

    def _render(self):
        W, H = self.screen.get_size()
        self.screen.fill((10, 12, 18))
        title = self.font_big.render("BSF Sonar Viewer", True, (0, 200, 160))
        self.screen.blit(title, (W//2 - title.get_width()//2, H//2 - 80))
        y = H//2 - 40
        for text, colour in self._lines:
            surf = self.font_mono.render(text, True, colour)
            self.screen.blit(surf, (W//2 - surf.get_width()//2, y))
            y += 22
        if hasattr(self, '_progress'):
            label, pct, bar_w, filled, current, total = self._progress
            bar_x = W//2 - bar_w//2
            bar_y = y + 10
            pygame.draw.rect(self.screen, (30, 38, 55),
                             (bar_x, bar_y, bar_w, 8), border_radius=4)
            if filled > 0:
                pygame.draw.rect(self.screen, (0, 160, 120),
                                 (bar_x, bar_y, filled, 8), border_radius=4)
            detail = self.font_mono.render(
                f"{label}  {current:,} / {total:,}  ({pct:.0f}%)",
                True, (80, 95, 110))
            self.screen.blit(detail, (W//2 - detail.get_width()//2, bar_y + 16))
        pygame.display.flip()
        pygame.event.pump()


# ── Sonar colourmaps ──────────────────────────────────────────────────────────
def _lut_stop(r, g, b):
    return np.array([r, g, b], dtype=np.float32)

def _build_lut(stops):
    lut = np.zeros((256, 3), dtype=np.uint8)
    for i in range(256):
        t = i / 255.0
        for j in range(len(stops) - 1):
            t0, c0 = stops[j]
            t1, c1 = stops[j + 1]
            if t0 <= t <= t1:
                alpha = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
                lut[i] = np.clip(c0 + alpha * (c1 - c0), 0, 255).astype(np.uint8)
                break
    return lut

_LUT_STOPS = {
    "Amber": [
        (0.00, _lut_stop(0,   0,   0)),
        (0.35, _lut_stop(80,  40,   0)),
        (0.60, _lut_stop(210, 140,  10)),
        (0.80, _lut_stop(255, 210,  60)),
        (1.00, _lut_stop(255, 255, 255)),
    ],
    "Grayscale": [
        (0.00, _lut_stop(0,   0,   0)),
        (1.00, _lut_stop(255, 255, 255)),
    ],
    "Ice": [
        (0.00, _lut_stop(0,   0,   10)),
        (0.35, _lut_stop(0,   40,  95)),
        (0.65, _lut_stop(0,  150, 210)),
        (0.85, _lut_stop(140, 225, 255)),
        (1.00, _lut_stop(255, 255, 255)),
    ],
    "Copper": [
        (0.00, _lut_stop(0,   0,   0)),
        (0.40, _lut_stop(90,  45,  25)),
        (0.70, _lut_stop(200, 120,  70)),
        (0.90, _lut_stop(240, 190, 140)),
        (1.00, _lut_stop(255, 255, 255)),
    ],
    "Phosphor": [
        (0.00, _lut_stop(0,   5,   0)),
        (0.35, _lut_stop(0,   60,  15)),
        (0.65, _lut_stop(30, 180,  40)),
        (0.85, _lut_stop(150, 255, 130)),
        (1.00, _lut_stop(255, 255, 255)),
    ],
}
LUT_NAMES    = list(_LUT_STOPS.keys())
LUT_PALETTES = {name: _build_lut(stops) for name, stops in _LUT_STOPS.items()}


def _build_combined_lut(gain, gamma, colour_lut):
    """
    Pre-compute gain + gamma + palette into a single 256×3 uint8 LUT.
    This replaces per-pixel float multiply, np.power, and indexing with
    a single gather operation: rgb = combined_lut[img8].
    Cost: 256 float ops — negligible. Recomputed only when gain/gamma/
    palette actually change.
    """
    x = np.arange(256, dtype=np.float32) / 255.0
    v = np.clip(x * gain, 0.0, 1.0)
    v = np.power(v, gamma)
    idx = (v * 255.0).astype(np.uint8)
    return colour_lut[idx]  # shape (256, 3)


# ── Layout helper ─────────────────────────────────────────────────────────────
def _compute_layout(win_w, win_h, zoom_idx, port_on, stbd_on, dock_w=0):
    canvas_h = win_h - SCRUB_H - TOOLBAR_H
    if canvas_h < 100:
        canvas_h = 100
    zoom = ZOOM_STEPS[zoom_idx]
    dock_w   = max(0, min(dock_w, win_w - 300))
    canvas_w = win_w - dock_w
    n_ch = (1 if port_on else 0) + (1 if stbd_on else 0)
    if n_ch == 0:
        n_ch = 1
    gap       = GAP_W if (port_on and stbd_on) else 0
    avail_w   = canvas_w - gap
    base_ch_w = max(20, avail_w // n_ch)
    channel_w = int(base_ch_w * zoom)
    total_w   = channel_w * n_ch + gap
    return dict(
        WIN_W=win_w, WIN_H=win_h,
        CANVAS_W=canvas_w,
        DOCK_W=dock_w,
        CANVAS_H=canvas_h,
        CHANNEL_W=channel_w,
        TOTAL_W=total_w,
        GAP=gap,
        ZOOM=zoom,
        ZOOM_IDX=zoom_idx,
        PORT_ON=port_on,
        STBD_ON=stbd_on,
    )


# ── BSF parsing ───────────────────────────────────────────────────────────────
def read_file_header(data):
    return {
        'sound_speed':  struct.unpack_from('<f', data, 0x10)[0],
        'device_model': data[0x60:0x70].rstrip(b'\x00').decode('ascii', errors='replace'),
        'sonar_serial': data[0x70:0x80].rstrip(b'\x00').decode('ascii', errors='replace'),
        'longitude':    struct.unpack_from('<d', data, 0x38)[0],
        'latitude':     struct.unpack_from('<d', data, 0x40)[0],
    }

def parse_channel(ping, ch_idx):
    off = CH_SR_OFFSET + ch_idx * CH_SR_SIZE
    return {
        'freq_kHz':     struct.unpack_from('<H', ping, off + 2)[0],
        'half_samples': struct.unpack_from('<I', ping, off + 52)[0],
        'raw_sr':       struct.unpack_from('<I', ping, off + 28)[0],
    }

def get_ping_meta(ping):
    ch = parse_channel(ping, 0)
    y  = struct.unpack_from('<H', ping, 16)[0]
    mo = ping[18]; d = ping[19]
    h  = ping[20]; mi = ping[21]; s = ping[22]
    return {
        'ts':        f"{y}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}:{s:02d}",
        'freq_kHz':  ch['freq_kHz'],
        'n_samples': ch['half_samples'],
        'raw_sr':    ch['raw_sr'],
    }

def extract_nav_fix(nav_bytes):
    if len(nav_bytes) < NAV_LAT_OFFSET + 8 or len(nav_bytes) < NAV_LON_OFFSET + 8:
        return None
    try:
        lat = struct.unpack_from(NAV_FIELD_FMT, nav_bytes, NAV_LAT_OFFSET)[0]
        lon = struct.unpack_from(NAV_FIELD_FMT, nav_bytes, NAV_LON_OFFSET)[0]
        ts  = struct.unpack_from(NAV_FIELD_FMT, nav_bytes, NAV_TS_OFFSET)[0]
    except struct.error:
        return None
    if not np.isfinite(lat) or not np.isfinite(lon):
        return None
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return None
    if abs(lat) < 1e-6 and abs(lon) < 1e-6:
        return None
    return lat, lon, ts

def extract_raw_channels(ping, p_lo=AMP_NORM_P_LO_DEFAULT, p_hi=AMP_NORM_P_HI_DEFAULT):
    ch   = parse_channel(ping, 0)
    n    = ch['half_samples']
    base = SAMPLE_OFFSET
    mb   = len(ping)
    raw_data = np.zeros(2 * n, dtype='<u4')
    available_bytes = mb - base
    if available_bytes > 0:
        bytes_to_read   = min(available_bytes, n * 2 * 4)
        actual_elements = bytes_to_read // 4
        raw_data[:actual_elements] = np.frombuffer(
            ping[base: base + bytes_to_read], dtype='<u4', count=actual_elements)
    raw_data = raw_data.reshape(2, n)
    # Full 32-bit linear amplitude — no masking (see note above extract_raw_channels
    # usage / AMP_NORM_P_LO_DEFAULT). Log-compress, then stretch the calibrated
    # [p_lo, p_hi] percentile window to 0..1. Values outside that window aren't
    # hard-clipped here — they're clipped once, later, in the shared 8-bit
    # quantization step — so a slightly-too-narrow calibration window just
    # loses a little headroom rather than silently corrupting data.
    span = max(p_hi - p_lo, 1e-6)
    port = (np.log1p(raw_data[0].astype(np.float64)) - p_lo) / span
    stbd = (np.log1p(raw_data[1].astype(np.float64)) - p_lo) / span
    return port[::-1].astype(np.float32), stbd.astype(np.float32)


def calibrate_amplitude_range(data, ping_index, n_sample=400):
    """
    One-time per-file calibration: sample pings spread evenly across the
    whole file, log-compress their raw amplitudes, and take the [2, 99.5]
    percentile window. This is what extract_raw_channels() stretches to
    0..1, so it replaces the old fixed-constant normalization with one
    based on what this specific file's sonar/gain setting actually recorded.
    Falls back to the generic AMP_NORM_*_DEFAULT constants on any failure
    (e.g. a truncated or unusual file) so a bad calibration never crashes
    loading — it just falls back to a reasonable generic contrast.
    """
    try:
        n_pings = len(ping_index)
        if n_pings == 0:
            return AMP_NORM_P_LO_DEFAULT, AMP_NORM_P_HI_DEFAULT
        idxs = np.linspace(0, n_pings - 1, min(n_sample, n_pings)).astype(int)
        samples = []
        for i in idxs:
            off, sz = ping_index[i]
            ping = data[off: off + sz]
            n = parse_channel(ping, 0)['half_samples']
            if n <= 0:
                continue
            base = SAMPLE_OFFSET
            avail = len(ping) - base
            if avail <= 0:
                continue
            b2r = min(avail, n * 2 * 4)
            ne  = (b2r // 4)
            raw = np.frombuffer(ping[base: base + ne * 4], dtype='<u4', count=ne)
            if raw.size:
                samples.append(raw.astype(np.float64))
        if not samples:
            return AMP_NORM_P_LO_DEFAULT, AMP_NORM_P_HI_DEFAULT
        logs = np.log1p(np.concatenate(samples))
        p_lo, p_hi = np.percentile(logs, [2.0, 99.5])
        if p_hi <= p_lo:
            return AMP_NORM_P_LO_DEFAULT, AMP_NORM_P_HI_DEFAULT
        return float(p_lo), float(p_hi)
    except Exception:
        return AMP_NORM_P_LO_DEFAULT, AMP_NORM_P_HI_DEFAULT


# ── Live 3101 packet parsing ───────────────────────────────────────────────────
def _checksum_ok(packet, packet_size):
    if packet_size < PACKET_HEADER_SIZE + 4:
        return False
    try:
        flag = struct.unpack_from('<H', packet, PACKET_FLAG_OFFSET)[0]
    except struct.error:
        return False
    if not (flag & PACKET_FLAG_CHECKSUM_BIT):
        return True
    trailer = struct.unpack_from('<I', packet, packet_size - 4)[0]
    computed = sum(packet[:packet_size - 4]) & 0xFFFFFFFF
    return computed == trailer

def parse_3101_body(header_bytes, body_and_data):
    if len(body_and_data) < B3101_BODY_SIZE:
        return None
    try:
        sample_len = struct.unpack_from('<I', body_and_data, B3101_SAMPLELEN_OFF)[0]
        n = int(sample_len)
        if n <= 0 or n > 200_000:
            return None
        need = 2 * n * 2
        if len(body_and_data) < B3101_BODY_SIZE + need:
            return None
        syn = B3101_SYNTIME_OFF
        year   = struct.unpack_from('<H', body_and_data, syn)[0]
        month  = body_and_data[syn + 2]
        day    = body_and_data[syn + 3]
        hour   = body_and_data[syn + 4]
        minute = body_and_data[syn + 5]
        second = body_and_data[syn + 6]
        ping_number    = struct.unpack_from('<I', body_and_data, B3101_PINGNUM_OFF)[0]
        sonar_range_cm = struct.unpack_from('<I', body_and_data, B3101_SONARRANGE_OFF)[0]
        center_freq_hz = struct.unpack_from('<I', body_and_data, B3101_CENTERFREQ_OFF)[0]
        sample_rate    = struct.unpack_from('<f', body_and_data, B3101_SAMPLERATE_OFF)[0]
        arr = np.frombuffer(body_and_data, dtype='<u2',
                            count=2 * n, offset=B3101_BODY_SIZE)
        port = (arr[:n].astype(np.float32)) / 65535.0
        stbd = (arr[n:2 * n].astype(np.float32)) / 65535.0
    except (struct.error, ValueError):
        return None
    meta = {
        'ts':          f"{year}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}",
        'freq_kHz':    center_freq_hz / 1000.0,
        'n_samples':   n,
        'raw_sr':      sample_rate,
        'ping_number': ping_number,
        'max_range_m': sonar_range_cm / 100.0,
    }
    return port[::-1], stbd, meta

def parse_3101_packet(packet):
    if len(packet) < B3101_DATA_OFF:
        return None
    return parse_3101_body(packet[:PACKET_HEADER_SIZE], packet[PACKET_HEADER_SIZE:])


class LiveClient:
    def __init__(self, host, port, on_row, on_status):
        self.host, self.port = host, port
        self.on_row, self.on_status = on_row, on_status
        self._sock   = None
        self._stop   = threading.Event()
        self._thread = None
        self._partial_frames = {}
        self._last_ping_number = None
        self._checksum_failures = 0

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self):
        self.on_status('connecting')
        try:
            sock = socket.create_connection((self.host, self.port), timeout=3.0)
        except Exception as e:
            self.on_status(f'not connected ({e})')
            return
        self._sock = sock
        sock.settimeout(1.0)
        self.on_status('connected')
        buf = bytearray()
        try:
            while not self._stop.is_set():
                try:
                    chunk = sock.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buf.extend(chunk)
                self._drain(buf)
        except Exception:
            pass
        finally:
            try:
                sock.close()
            except Exception:
                pass
            if not self._stop.is_set():
                self.on_status('disconnected (connection lost)')

    def _drain(self, buf):
        while True:
            if len(buf) < PACKET_HEADER_SIZE:
                return
            idx = buf.find(PACKET_IDENTIFIER_BYTES)
            if idx == -1:
                buf.clear()
                return
            if idx > 0:
                del buf[:idx]
            if len(buf) < PACKET_HEADER_SIZE:
                return
            packet_size = struct.unpack_from('<I', buf, PACKET_SIZE_OFFSET)[0]
            if packet_size < PACKET_HEADER_SIZE or packet_size > 8_000_000:
                del buf[:4]
                continue
            if len(buf) < packet_size:
                return
            packet = bytes(buf[:packet_size])
            del buf[:packet_size]
            self._handle_packet(packet)

    def _prune_partial_frames(self, now):
        stale = [k for k, v in self._partial_frames.items()
                 if now - v['first_seen'] > MULTI_PACKET_FRAME_TIMEOUT_S]
        for k in stale:
            del self._partial_frames[k]

    def _handle_packet(self, packet):
        packet_size = len(packet)
        try:
            packet_type = struct.unpack_from('<I', packet, PACKET_TYPE_OFFSET)[0]
            flag        = struct.unpack_from('<H', packet, PACKET_FLAG_OFFSET)[0]
        except struct.error:
            return
        if not _checksum_ok(packet, packet_size):
            self._checksum_failures += 1
            self.on_status(f'checksum FAILED (packet dropped, '
                           f'{self._checksum_failures} total)')
            return
        is_multi = bool(flag & PACKET_FLAG_MULTI_BIT)
        body_and_data = packet[PACKET_HEADER_SIZE: packet_size - 4]
        if is_multi:
            body_and_data = self._reassemble(packet, packet_type, body_and_data)
            if body_and_data is None:
                return
        if packet_type == PACKET_TYPE_3101:
            self._handle_3101(body_and_data)
        elif packet_type == PACKET_TYPE_166:
            self._handle_166(body_and_data)

    def _reassemble(self, packet, packet_type, body_and_data):
        now = time.time()
        self._prune_partial_frames(now)
        try:
            frame_id     = struct.unpack_from('<H', packet, PACKET_FRAME_ID_OFFSET)[0]
            total_packet = struct.unpack_from('<H', packet, PACKET_TOTAL_OFFSET)[0]
            packet_num   = struct.unpack_from('<H', packet, PACKET_NUMBER_OFFSET)[0]
        except struct.error:
            return None
        if total_packet <= 1 or packet_num < 1:
            return body_and_data
        key = (packet_type, frame_id)
        entry = self._partial_frames.get(key)
        if entry is None or entry['total'] != total_packet:
            entry = {'total': total_packet, 'parts': {}, 'first_seen': now}
            self._partial_frames[key] = entry
        entry['parts'][packet_num] = body_and_data
        if len(entry['parts']) < total_packet:
            return None
        del self._partial_frames[key]
        return b''.join(entry['parts'][i] for i in range(1, total_packet + 1))

    def _handle_3101(self, body_and_data):
        parsed = parse_3101_body(None, body_and_data)
        if not parsed:
            return
        port_raw, stbd_raw, meta = parsed
        pn = meta.get('ping_number')
        if pn is not None and self._last_ping_number is not None:
            delta = pn - self._last_ping_number
            if delta > 1:
                meta['ping_gap'] = delta - 1
            elif delta < 0:
                meta['ping_gap'] = 0
        self._last_ping_number = pn if pn is not None else self._last_ping_number
        self.on_row(port_raw, stbd_raw, meta)

    def _handle_166(self, body_and_data):
        if len(body_and_data) < 1:
            return
        state = body_and_data[0]
        data_up = bool(state & 0x01)
        cmd_up  = bool(state & 0x10)
        if data_up and cmd_up:
            desc = 'connected (data+cmd)'
        elif data_up:
            desc = 'connected (data only)'
        elif cmd_up:
            desc = 'connected (cmd only)'
        else:
            desc = 'sonar link down'
        self.on_status(f'connected — hw: {desc}')


def extract_ping_row_from_raw(port_raw, stbd_raw, channel_w, port_on, stbd_on, gap,
                              interp_xs_cache):
    n  = len(port_raw)
    key = (n, channel_w)
    if key not in interp_xs_cache:
        interp_xs_cache[key] = np.linspace(0, n - 1, channel_w)
    xs = interp_xs_cache[key]
    xi = np.arange(n)
    parts = []
    if port_on:
        parts.append(np.interp(xs, xi, port_raw).astype(np.float32))
    if port_on and stbd_on:
        parts.append(np.zeros(gap, dtype=np.float32))
    if stbd_on:
        parts.append(np.interp(xs, xi, stbd_raw).astype(np.float32))
    if not parts:
        return np.zeros(channel_w, dtype=np.float32)
    return np.concatenate(parts)


def load_all_pings(data, loader=None):
    pings       = []
    nav_records = []
    pos   = BSF_FILE_HDR_SZ
    file_sz = len(data)
    report_every = 500
    while pos + 16 <= file_sz:
        rt = struct.unpack_from('<I', data, pos)[0]
        if rt == BSF_RECORD_TYPE:
            rs = struct.unpack_from('<I', data, pos + 12)[0]
            if rs > 16 and pos + rs <= file_sz:
                if rs >= SAMPLE_OFFSET:
                    pings.append((pos, rs))
                    if loader and len(pings) % report_every == 0:
                        loader.set_progress("Scanning pings", pos, file_sz)
                elif rs == NAV_RECORD_SIZE:
                    nav_records.append((pos, len(pings) - 1))
                pos += rs
            else:
                break
        else:
            pos += 1
    return pings, nav_records


# ── GPS track ─────────────────────────────────────────────────────────────────
def _format_degrees(value, pos_letter, neg_letter, decimals):
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


# ── Ring-buffer waterfall ─────────────────────────────────────────────────────
class FastWaterfall:
    def __init__(self, w, h):
        self.w = w
        self.h = h
        self._raw          = np.zeros((h, w),    dtype=np.float32)
        self._rgb_ordered  = np.zeros((h, w, 3), dtype=np.uint8)
        self._head         = 0
        self._count        = 0
        self.surf          = pygame.Surface((w, h))
        self._surf_dirty   = True
        self.raw_changed   = True

    def add_row(self, row_f32):
        n = len(row_f32)
        if n >= self.w:
            self._raw[self._head] = row_f32[:self.w]
        else:
            self._raw[self._head, :n]  = row_f32
            self._raw[self._head, n:]  = 0.0
        self._head       = (self._head - 1) % self.h
        self._count      = min(self._count + 1, self.h)
        self.raw_changed = True

    def get_ordered_raw(self):
        start = (self._head + 1) % self.h
        if start == 0:
            return self._raw
        return np.concatenate((self._raw[start:], self._raw[:start]), axis=0)

    def set_ordered_rgb(self, rgb):
        self._rgb_ordered = rgb
        self._surf_dirty  = True
        self.raw_changed  = False

    def clear(self):
        self._raw.fill(0.0)
        self._rgb_ordered.fill(0)
        self._head        = 0
        self._count       = 0
        self._surf_dirty  = True
        self.raw_changed  = True

    def _flush_surface(self):
        if not self._surf_dirty:
            return
        pygame.surfarray.blit_array(self.surf, self._rgb_ordered.transpose(1, 0, 2))
        self._surf_dirty = False

    def blit_to(self, dest, y, win_w):
        self._flush_surface()
        if self.w <= win_w:
            dest.blit(self.surf, ((win_w - self.w) // 2, y))
        else:
            src_x = (self.w - win_w) // 2
            dest.blit(self.surf, (0, y),
                      area=pygame.Rect(src_x, 0, win_w, self.h))


# ── Main application ──────────────────────────────────────────────────────────
class BSFViewer:
    GAIN_DEFAULT  = 1.0;  GAIN_STEP  = 0.1;  GAIN_MIN  = 0.1;  GAIN_MAX  = 5.0
    # Was 0.7. Gain/gamma are applied AFTER the contrast pipeline (see
    # _build_combined_lut), on top of whatever extract_raw_channels() /
    # CLAHE already produced. A sub-1.0 gamma brightens further on top of
    # that, which no longer serves a purpose now that the amplitude decode
    # itself is calibrated per-file — 1.0 (neutral) is the right starting
    # point; nudge it per-file from the Controls panel if needed.
    GAMMA_DEFAULT = 1.0;  GAMMA_STEP = 0.05; GAMMA_MIN = 0.1;  GAMMA_MAX = 3.0

    def __init__(self, bsf_path=None):
        self.bsf_path = bsf_path
        _enable_dpi_awareness()
        pygame.init()
        caption = f"BSF Viewer — {os.path.basename(bsf_path)}" if bsf_path else "BSF Viewer"
        pygame.display.set_caption(caption)
        info  = pygame.display.Info()
        win_w = min(info.current_w - 80, 1600)
        win_h = min(info.current_h - 80, 950)
        win_h = max(win_h, SCRUB_H + TOOLBAR_H + 200)
        self._fullscreen    = False
        self._windowed_size = (win_w, win_h)
        self.screen = pygame.display.set_mode((win_w, win_h), pygame.RESIZABLE)
        _apply_titlebar_style()
        self.clock  = pygame.time.Clock()
        try:
            self.font_mono = pygame.font.SysFont("consolas", 13)
            self.font_big  = pygame.font.SysFont("consolas", 15, bold=True)
        except Exception:
            self.font_mono = pygame.font.SysFont(None, 14)
            self.font_big  = pygame.font.SysFont(None, 16, bold=True)

        self.data          = b''
        self.fh            = {'sound_speed': 0.0, 'longitude': 0.0, 'latitude': 0.0,
                              'device_model': '', 'sonar_serial': ''}
        self.ping_index    = []
        self.n_pings       = 0
        self.gps_track     = GPSTrack()
        self.meta          = {'ts': '', 'freq_kHz': 0.0, 'n_samples': 0, 'raw_sr': 0.0}
        self._max_range_m  = 0.0
        self._amp_p_lo     = AMP_NORM_P_LO_DEFAULT
        self._amp_p_hi     = AMP_NORM_P_HI_DEFAULT

        self.current_ping  = -1
        self.playing       = False
        self.speed_idx     = DEFAULT_SPEED_IDX
        self.last_advance  = 0.0
        self.zoom_idx      = ZOOM_DEFAULT
        self.port_on       = True
        self.stbd_on       = True
        self.gain          = self.GAIN_DEFAULT
        self.gamma         = self.GAMMA_DEFAULT
        self.lut_idx       = 0

        self._view_zoom       = 1.0
        self._view_cx         = 0.5
        self._view_cy         = 0.5
        self._last_click_time = 0.0
        self._last_click_pos  = (-10_000, -10_000)

        # ── Reused draw surfaces — no per-frame allocation ──
        self._canvas_surf     = None   # waterfall canvas (realloc on resize only)
        self._zoom_src        = None   # view-zoom source scratch
        self._zoom_out        = None   # view-zoom scaled destination

        self.mode         = 'launcher'
        self.live_host    = LIVE_DEFAULT_HOST
        self.live_port    = LIVE_DEFAULT_PORT
        self.live_status  = 'disconnected'
        self.live_client  = None
        self._live_queue  = queue.Queue()
        self._live_n      = 0
        self._live_last_meta = None
        self._pre_live_ping = -1

        self.recording       = False
        self._record_count   = 0
        self._rebuilding   = False
        self._rebuild_msg  = ""
        self._flash_msg    = ""
        self._flash_msg_time = 0.0
        self.show_controls = False
        self.show_help      = False
        self.show_track      = False
        self.track_docked    = False

        self._track_pan_x     = 0.0
        self._track_pan_y     = 0.0
        self._track_dragging  = False
        self._track_drag_last = (0, 0)
        self._track_scale     = None

        self.dock_w = DOCK_W
        self._dock_resizing     = False
        self._dock_resize_hover = False

        self._track_buttons     = []
        self._track_panel_rect  = None
        self._track_dock_rect   = None
        self._track_plot_rect   = None
        self._dock_resize_rect  = None
        self._controls_panel_rect = None
        self._help_panel_rect     = None

        # ── Selection rectangle / ruler / marks ──
        self._mouse_is_down    = False
        self._mouse_down_pos   = None
        self._rect_dragging    = False
        self._rect_start       = None
        self._rect_end         = None
        self._rect_menu        = None
        self._rect_menu_buttons = []
        self._marks             = []
        self._mark_counter      = 0
        self._show_marks        = True
        self._marks_dirty       = False

        # ── Unsaved-marks confirmation dialog ──
        self._show_unsaved_dialog = False
        self._pending_action       = None   # 'quit' | 'home' | 'open_file' | 'go_live'
        self._unsaved_dialog_rect    = None
        self._unsaved_dialog_buttons = []

        # ── Enhancement pipeline state ──
        # noise_idx=0 (Off) and agc=False: with the amplitude decode fixed
        # (see SAMPLE_OFFSET / extract_raw_channels), the data no longer
        # needs blurring or a second brightness pass to look reasonable —
        # and median/bilateral/NL-Means denoise all cost you exactly the
        # small, sparse hard-target returns you're trying to see. AGC would
        # just re-stretch a range extract_raw_channels() already calibrated
        # per-file. Turn either on from the Controls panel per-file if it helps.
        self.noise_idx        = 0
        self.contrast_idx     = CONTRAST_DEFAULT_IDX
        self.sharpen           = False
        self.agc                = False
        self.hdr                = False
        self._clahe_low  = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8))
        self._clahe_med  = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        self._clahe_high = cv2.createCLAHE(clipLimit=6.0, tileGridSize=(8, 8))

        # Background enhancement worker with Event-based notification
        self._enh_lock          = threading.Lock()
        self._enh_job           = None
        self._enh_job_sig       = None   # (data_gen, shape, params) of the pending
                                         # job — duplicate-job guard
        self._enh_job_gen       = 0
        self._enh_result        = None
        self._enh_displayed_gen = 0
        self._enh_event         = threading.Event()  # replaces polling sleep

        # ── Grayscale cache: skip heavy pipeline when only light params change ──
        self._cached_enhanced   = None   # (H, W) uint8 grayscale
        self._cached_tmask      = None
        self._cached_smask      = None
        self._cached_heavy_key  = None
        self._cached_data_gen   = -1
        self._cached_scale      = 1.0    # work-scale used for cached result
        self._enh_data_gen      = 0      # increments when raw data actually changes

        self._enh_thread = threading.Thread(target=self._enhancement_worker, daemon=True)
        self._enh_thread.start()

        # ── Sonar-specific enhancement state ──
        self.target_idx    = 0
        self.target_ksize  = TARGET_KSIZE_DEFAULT
        self.shadow_enh    = False
        self.overlay       = False

        # ── Load persisted settings (before layout is computed) ──
        self._load_settings()

        self._interp_xs_cache = {}
        self._raw_cache = {}
        self._row_cache = {}
        self._layout   = _compute_layout(win_w, win_h, self.zoom_idx,
                                         self.port_on, self.stbd_on)
        self.waterfall = FastWaterfall(self._layout['TOTAL_W'],
                                       self._layout['CANVAS_H'])
        if bsf_path:
            if not self._load_file(bsf_path):
                sys.exit(1)

    # ── Settings persistence ──────────────────────────────────────────────
    def _load_settings(self):
        if not os.path.exists(CONFIG_PATH):
            return
        try:
            with open(CONFIG_PATH, 'r') as f:
                s = json.load(f)
        except Exception:
            return
        self.dock_w        = s.get('dock_w',        DOCK_W)
        self.hdr          = s.get('hdr', False)
        self.show_track    = s.get('show_track',    False)
        self.track_docked  = s.get('track_docked',  False)

    def _save_settings(self):
        s = {
            'dock_w':       self.dock_w,
            'show_track':   self.show_track,
            'track_docked': self.track_docked,
            'hdr':          self.hdr,
        }
        try:
            with open(CONFIG_PATH, 'w') as f:
                json.dump(s, f, indent=2)
        except Exception:
            pass

    def _load_file(self, bsf_path):
        loader = Loader(self.screen, self.font_mono, self.font_big)
        loader.set_stage(f"Reading  {os.path.basename(bsf_path)} ...")
        try:
            with open(bsf_path, 'rb') as f:
                data = f.read()
        except Exception as e:
            print(f"ERROR: could not read {bsf_path}: {e}")
            return False
        if not data[:12].startswith(BSF_MAGIC):
            print("ERROR: Not a valid BSF file")
            return False
        loader.set_stage("Parsing file header ...")
        fh = read_file_header(data)
        loader.set_stage(
            f"Device: {fh['device_model']}  ·  "
            f"Sound speed: {fh['sound_speed']:.1f} m/s  ·  "
            f"Scanning pings ...")
        ping_index, nav_records = load_all_pings(data, loader)
        n_pings = len(ping_index)
        if n_pings == 0:
            print("ERROR: No pings found.")
            return False
        gps_track = GPSTrack()
        if nav_records:
            loader.set_stage(f"Found {n_pings:,} pings, "
                             f"{len(nav_records):,} GPS fixes.  Scanning GPS track ...")
            pygame.event.pump()
            for i, (off, anchor_idx) in enumerate(nav_records):
                fix = extract_nav_fix(data[off: off + NAV_RECORD_SIZE])
                if fix is not None:
                    lat, lon, _ts = fix
                    gps_track.add_fix(max(anchor_idx, 0), lat, lon)
                if i % 200 == 0:
                    loader.set_progress("Scanning GPS", i, len(nav_records))
        loader.set_stage(f"Found {n_pings:,} pings.  Calibrating amplitude range ...")
        pygame.event.pump()
        self._amp_p_lo, self._amp_p_hi = calibrate_amplitude_range(data, ping_index)
        loader.set_stage(f"Found {n_pings:,} pings.  Building display ...")
        pygame.event.pump()
        p0_off, p0_sz = ping_index[0]
        meta = get_ping_meta(data[p0_off: p0_off + p0_sz])
        raw_sr = meta['raw_sr']
        n_samp = meta['n_samples']
        ss     = fh['sound_speed'] if fh['sound_speed'] > 0 else 1500.0
        max_range_m = ss * (n_samp / raw_sr) if raw_sr > 0 else 0.0
        self.bsf_path      = bsf_path
        self.data          = data
        self.fh            = fh
        self.ping_index    = ping_index
        self.n_pings       = n_pings
        self.gps_track     = gps_track
        self.meta          = meta
        self._max_range_m  = max_range_m
        self._raw_cache.clear()
        self._row_cache.clear()
        self.waterfall     = FastWaterfall(self._layout['TOTAL_W'], self._layout['CANVAS_H'])
        self.current_ping  = -1
        self.playing       = False
        self.mode          = 'playback'
        self._marks.clear()
        self._mark_counter = 0
        self._rect_menu    = None
        self._marks_dirty  = False
        self._load_marks_sidecar()
        pygame.display.set_caption(f"BSF Viewer — {os.path.basename(bsf_path)}")
        return True

    def _ensure_raw_cache(self, idx):
        if idx not in self._raw_cache:
            off, sz = self.ping_index[idx]
            self._raw_cache[idx] = extract_raw_channels(
                self.data[off: off + sz], self._amp_p_lo, self._amp_p_hi)
            if len(self._raw_cache) > 5000:
                for k in list(self._raw_cache.keys())[:1000]:
                    del self._raw_cache[k]
        return self._raw_cache[idx]

    def _build_row(self, idx):
        L   = self._layout
        key = (idx, L['CHANNEL_W'], L['PORT_ON'], L['STBD_ON'])
        if key not in self._row_cache:
            port_raw, stbd_raw = self._ensure_raw_cache(idx)
            row = extract_ping_row_from_raw(
                port_raw, stbd_raw,
                L['CHANNEL_W'], L['PORT_ON'], L['STBD_ON'], L['GAP'],
                self._interp_xs_cache)
            self._row_cache[key] = row
            if len(self._row_cache) > 800:
                del self._row_cache[next(iter(self._row_cache))]
        return self._row_cache[key]

    # ── Image enhancement pipeline ────────────────────────────────────────────
    # Returns (enhanced_uint8, target_mask, shadow_mask) — stays in uint8
    # throughout to avoid float32 roundtrips.
    def _enhance_pixels(self, raw2d, p):
        img8 = np.clip(raw2d * 255.0, 0, 255).astype(np.uint8)

        nm = NOISE_MODES[p.noise_idx]
        if nm == "Median":
            img8 = cv2.medianBlur(img8, 3)
        elif nm == "Bilateral":
            # sigmaColor/sigmaSpace were 35/35, which is heavy enough to erase
            # single- and few-pixel hard-target returns almost as badly as a
            # median blur (verified: a 250-value single-pixel spike against a
            # ~140 background dropped to 234 at 35/35 vs 247 at these values,
            # while still cutting background speckle std by ~30%). Bilateral
            # is edge/outlier-preserving by design — these tighter sigmas are
            # what actually make use of that property.
            img8 = cv2.bilateralFilter(img8, d=5, sigmaColor=20, sigmaSpace=10)
        elif nm == "NL-Means":
            img8 = cv2.fastNlMeansDenoising(img8, h=7,
                                            templateWindowSize=7, searchWindowSize=15)

        if p.agc:
            lo, hi = np.percentile(img8, (1.0, 99.0))
            if hi > lo:
                img8 = np.clip((img8.astype(np.float32) - lo) * (255.0 / (hi - lo)),
                               0, 255).astype(np.uint8)

        cm = CONTRAST_MODES[p.contrast_idx]
        if cm == "Global HistEq":
            img8 = cv2.equalizeHist(img8)
        elif cm == "CLAHE Low":
            img8 = self._clahe_low.apply(img8)
        elif cm == "CLAHE Med":
            img8 = self._clahe_med.apply(img8)
        elif cm == "CLAHE High":
            img8 = self._clahe_high.apply(img8)

        tm = TARGET_MODES[p.target_idx]
        k  = p.target_ksize
        target_mask = None
        shadow_mask = None
        if tm != "Off":
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
            tophat_m = blackhat_m = None
            if tm == "Top-Hat Bright":
                tophat_m = cv2.morphologyEx(img8, cv2.MORPH_TOPHAT, kernel)
                img8     = cv2.add(img8, tophat_m)
            elif tm == "Top-Hat Bright+Dark":
                tophat_m   = cv2.morphologyEx(img8, cv2.MORPH_TOPHAT,   kernel)
                blackhat_m = cv2.morphologyEx(img8, cv2.MORPH_BLACKHAT, kernel)
                img8       = cv2.subtract(cv2.add(img8, tophat_m), blackhat_m)
            elif tm == "CFAR Contrast":
                f          = img8.astype(np.float32)
                local_mean = cv2.boxFilter(f, ddepth=-1, ksize=(k, k))
                local_sq   = cv2.boxFilter(f * f, ddepth=-1, ksize=(k, k))
                local_var  = np.clip(local_sq - local_mean ** 2, 0, None)
                local_std  = np.sqrt(local_var) + 3.0
                z          = np.clip((f - local_mean) / local_std, -3.0, 3.0)
                img8       = ((z + 3.0) / 6.0 * 255.0).astype(np.uint8)
            if p.overlay:
                if tophat_m is None or blackhat_m is None:
                    tophat_m   = cv2.morphologyEx(img8, cv2.MORPH_TOPHAT,   kernel)
                    blackhat_m = cv2.morphologyEx(img8, cv2.MORPH_BLACKHAT, kernel)
                t_thresh   = max(float(np.percentile(tophat_m, 97)), 8.0)
                s_thresh   = max(float(np.percentile(blackhat_m, 97)), 8.0)
                target_mask = tophat_m   > t_thresh
                shadow_mask = blackhat_m > s_thresh

        if p.shadow_enh:
            f    = img8.astype(np.float32)
            bg   = cv2.GaussianBlur(img8, (0, 0), sigmaX=max(k, 15)).astype(np.float32)
            diff = f - bg
            diff = np.where(diff < 0, diff * 1.6, diff)
            img8 = np.clip(bg + diff, 0, 255).astype(np.uint8)

        # ── Smart HDR local tone mapping ──────────────────────────────────────
        if p.hdr:
            img8 = self._smart_hdr(img8, strength=1.5, fast=p.fast)

        if p.sharpen:
            blur = cv2.GaussianBlur(img8, (0, 0), sigmaX=1.4)
            img8 = cv2.addWeighted(img8, 1.6, blur, -0.6, 0)

        return img8, target_mask, shadow_mask

    def _smart_hdr(self, img8, strength=1.5, fast=False):
        """
        Smart HDR local tone mapping for side-scan sonar.

        Separates the image into a large-scale illumination layer (base)
        and a high-frequency detail layer. The base gets tone-mapped to
        lift shadows and compress highlights, while the detail layer is
        adaptively boosted based on local brightness and texture — giving
        the waterfall a crisp, photographic HDR look without blowing out
        bright targets or losing detail in dark shadows.

        `strength` scales how much of the tone-mapped base actually gets
        used (mix, below) as well as the detail boost — it used to only
        affect the detail term, so the shadow-lift/highlight-compression
        curves were applied at full force regardless of the strength you
        set, which is why this washed the water column out to nearly the
        same brightness as the seabed at any setting. Now strength=0 is a
        no-op and the curve fades in smoothly as it increases.
        """
        f = img8.astype(np.float32) / 255.0
        h, w = f.shape

        # --- Base layer: large-scale structure ---
        sigma = max(8, min(40, max(h, w) // 16))
        base = cv2.GaussianBlur(f, (0, 0), sigmaX=sigma)

        # --- Detail layer ---
        detail = f - base

        # --- Tone-map the base layer ---
        # Shadow lift: gamma < 1 brings dark areas up
        shadow_curve = np.power(np.clip(base, 0, 1), 0.55)

        # Highlight compression: soft shoulder prevents clipping
        hl_curve = 1.0 - np.power(np.clip(1.0 - base, 0, 1), 1.8)

        # Smooth blend between shadow and highlight curves
        t = np.clip(base / 0.45, 0, 1)
        blend = t * t * (3.0 - 2.0 * t)
        toned_base = shadow_curve * (1.0 - blend) + hl_curve * blend

        # Fade the tone curve in with strength instead of applying it at
        # full force unconditionally — this is what actually controls
        # overall brightness/contrast now.
        mix = np.clip(strength / 4.0, 0.0, 1.0)
        base = base * (1.0 - mix) + toned_base * mix

        # --- Adaptive detail enhancement ---
        # Measure local detail energy to avoid amplifying noise
        detail_sq = detail * detail
        local_detail = cv2.GaussianBlur(detail_sq, (0, 0), sigmaX=max(3, sigma // 4))
        local_detail = np.sqrt(np.clip(local_detail, 0, None))

        # Gain map: more boost in dark, flat regions; less in bright/noisy regions
        dark_boost = 1.0 + 0.6 * (1.0 - base)
        noise_gate = np.clip(1.0 - local_detail * 3.0, 0.3, 1.0)
        detail_gain = (1.0 + strength * 0.8) * dark_boost * noise_gate
        detail_gain = np.clip(detail_gain, 0.5, 2.5)

        enhanced_detail = detail * detail_gain

        # --- Recombine ---
        result = np.clip(base + enhanced_detail, 0, 1)

        # Optional crispness pass when paused
        if not fast and strength > 0.5:
            blur = cv2.GaussianBlur(result, (0, 0), sigmaX=2.0)
            result = cv2.addWeighted(result, 1.15, blur, -0.15, 0)
            result = np.clip(result, 0, 1)

        return (result * 255.0).astype(np.uint8)

    @staticmethod
    def _tint(rgb, mask, colour, alpha=0.55):
        if mask is None or not mask.any():
            return rgb
        c = np.array(colour, dtype=np.float32)
        region = rgb[mask].astype(np.float32)
        rgb[mask] = (region * (1 - alpha) + c * alpha).astype(np.uint8)
        return rgb

    # ── Background enhancement worker ─────────────────────────────────────────
    def _enhancement_worker(self):
        last_done_gen = 0
        while True:
            # Wait for a new job instead of polling — zero wasted CPU,
            # near-zero latency (Event.set() wakes us immediately).
            self._enh_event.wait()
            self._enh_event.clear()

            with self._enh_lock:
                job = self._enh_job
            if job is None or job[2] <= last_done_gen:
                continue
            raw, p, gen, data_gen = job
            last_done_gen = gen

            try:
                fh, fw = raw.shape
                hk = _heavy_key(p)

                # ── Check grayscale cache: if data and heavy params unchanged,
                # skip the entire expensive pipeline and just re-colorize. ──
                use_cache = (
                    self._cached_enhanced is not None
                    and data_gen == self._cached_data_gen
                    and hk == self._cached_heavy_key
                    and self._cached_enhanced.shape[:2] == (fh, fw)
                )

                if use_cache:
                    enhanced = self._cached_enhanced
                    tmask    = self._cached_tmask
                    smask    = self._cached_smask
                else:
                    # Full pipeline with work-resolution cap
                    if NOISE_MODES[p.noise_idx] == "NL-Means":
                        budget = WORK_BUDGET_PX_NLM
                    elif p.fast:
                        # Only true for genuine live acquisition now (see
                        # fast= in _update_enhancement) — keeps processing
                        # from falling behind incoming packets. Ordinary
                        # file playback uses the full budget below.
                        budget = WORK_BUDGET_PX_LIVE
                    else:
                        budget = WORK_BUDGET_PX
                    scale = min(1.0, (budget / float(fh * fw)) ** 0.5)
                    if scale < 1.0:
                        ww, wh = max(1, int(fw * scale)), max(1, int(fh * scale))
                        work = cv2.resize(raw, (ww, wh), interpolation=cv2.INTER_AREA)
                        p_scaled = p._replace(
                            target_ksize=max(3, int(round(p.target_ksize * scale)) | 1))
                    else:
                        work = raw
                        p_scaled = p
                    enhanced, tmask, smask = self._enhance_pixels(work, p_scaled)

                    # Upscale back to full res if we downscaled
                    if scale < 1.0:
                        interp = cv2.INTER_LINEAR if p.fast else cv2.INTER_LANCZOS4
                        enhanced = cv2.resize(enhanced, (fw, fh), interpolation=interp)
                        if tmask is not None:
                            tmask = cv2.resize(tmask.astype(np.uint8), (fw, fh),
                                               interpolation=cv2.INTER_NEAREST).astype(bool)
                        if smask is not None:
                            smask = cv2.resize(smask.astype(np.uint8), (fw, fh),
                                               interpolation=cv2.INTER_NEAREST).astype(bool)

                    # Store in cache for future light-param-only changes
                    self._cached_enhanced  = enhanced
                    self._cached_tmask     = tmask
                    self._cached_smask     = smask
                    self._cached_heavy_key = hk
                    self._cached_data_gen  = data_gen

                # ── Colorize via pre-combined LUT (single gather, no float math) ──
                colour_lut    = LUT_PALETTES[LUT_NAMES[p.lut_idx]]
                combined_lut  = _build_combined_lut(p.gain, p.gamma, colour_lut)
                rgb = combined_lut[enhanced]  # (H, W, 3) uint8 — one gather op

                if p.overlay:
                    rgb = rgb.copy()  # ensure writable for tinting
                    rgb = self._tint(rgb, tmask, (60, 255, 140))
                    rgb = self._tint(rgb, smask, (255, 60, 200))

                with self._enh_lock:
                    self._enh_result = (rgb, gen)
            except Exception:
                import traceback
                traceback.print_exc()
                with self._enh_lock:
                    self._enh_result = (None, gen)

    def _update_enhancement(self, force=False):
        if self._rebuilding:
            # The waterfall is being reconstructed (seek / layout change):
            # the raw buffer is mid-mutation, so any job submitted now
            # would be computed from partial data and thrown away a
            # moment later. The rebuild path triggers one full
            # enhancement pass once it finishes.
            return
        if self.waterfall._count == 0:
            return

        if self.waterfall.raw_changed or force:
            if self.waterfall.raw_changed:
                self._enh_data_gen += 1
            raw = self.waterfall.get_ordered_raw().copy()
            # Was `self.playing or self.mode == 'live'` — that dropped to the
            # 600 kpx budget and a cheaper bilinear upscale for ordinary file
            # playback too, not just genuine live acquisition, which is why
            # seeking looked sharper than playing. Live streaming still needs
            # the lighter budget so processing doesn't fall behind incoming
            # packets; playback of an already-loaded file doesn't have that
            # constraint, so it now stays at full resolution whether playing,
            # paused, or seeking.
            fast = (self.mode == 'live')
            params = EnhanceParams(
                self.noise_idx, self.contrast_idx, self.agc, self.target_idx,
                self.target_ksize, self.shadow_enh, self.overlay, self.sharpen,
                self.gain, self.gamma, self.lut_idx, fast, self.hdr)
            job_sig = (self._enh_data_gen, raw.shape, params)
            if job_sig != self._enh_job_sig:
                # Duplicate-job guard: an identical (data generation,
                # shape, params) signature means the worker already has —
                # or just finished — exactly this job, and its result
                # would be pixel-for-pixel the same. Don't resubmit; just
                # wait for that result on a subsequent frame.
                with self._enh_lock:
                    self._enh_job_gen += 1
                    self._enh_job = (raw, params, self._enh_job_gen,
                                     self._enh_data_gen)
                    self._enh_job_sig = job_sig
                self._enh_event.set()  # wake the worker immediately
            self.waterfall.raw_changed = False

        with self._enh_lock:
            result = self._enh_result
        if result is not None:
            rgb, gen = result
            if gen > self._enh_displayed_gen:
                self._enh_displayed_gen = gen
                if rgb is None:
                    self._flash("ERROR in enhancement pipeline — see terminal")
                elif (rgb.shape[0] == self.waterfall.h
                      and rgb.shape[1] == self.waterfall.w):
                    self.waterfall.set_ordered_rgb(rgb)

    def _flash(self, text):
        print(f"[bsf_viewer] {text}")
        self._flash_msg      = text
        self._flash_msg_time = time.time()

    # ── Actions ───────────────────────────────────────────────────────────────
    def _guarded(self, fn):
        try:
            msg = fn()
            if msg:
                self._flash(msg)
        except Exception:
            import traceback
            traceback.print_exc()
            self._flash("ERROR — see terminal")

    def _act_play_pause(self):
        if self.mode == 'live':
            return "Playback controls are disabled while LIVE — F2 to stop"
        self.playing = not self.playing
        self.last_advance = time.time()
        if not self.playing:
            # Paused — snap the pipeline back to the full work budget
            # (playing/live enhancement runs at a capped 600 kpx).
            self._update_enhancement(force=True)
        return "PLAY" if self.playing else "PAUSE"

    def _act_reset(self):
        if self.mode == 'live':
            return "Playback controls are disabled while LIVE — F2 to stop"
        self.playing = False
        self._clear_waterfall()
        return "RESET"

    def _act_home(self):
        if self.mode == 'live':
            return "Playback controls are disabled while LIVE — F2 to stop"
        self._clear_waterfall()
        self.playing = True
        self.last_advance = time.time()
        return "HOME → ping 0, playing"

    def _act_step_right(self):
        if self.mode == 'live' or self.playing or self.current_ping >= self.n_pings - 1:
            return None
        nxt = self.current_ping + 1
        self.waterfall.add_row(self._build_row(nxt))
        self.current_ping = nxt
        if self.recording:
            self._record_count += 1
        return None

    def _act_step_left(self):
        if self.mode == 'live' or self.playing or self.current_ping <= 0:
            return None
        self._jump_to(self.current_ping - 1)
        return None

    def _act_toggle_live(self):
        if self.mode == 'live':
            had_file = bool(self.ping_index)
            self._stop_live()
            return ("LIVE stopped — back to Playback" if had_file
                    else "LIVE stopped")
        if self._marks_dirty and self._marks:
            self._pending_action      = 'go_live'
            self._show_unsaved_dialog = True
            return None
        self._pre_live_ping = self.current_ping
        self.playing      = False
        self.mode         = 'live'
        self.live_status  = 'connecting'
        self._live_n      = 0
        self.waterfall.clear()
        self.current_ping = -1
        self.n_pings      = 0
        self._marks.clear()
        self._mark_counter = 0
        self._rect_menu     = None
        self._marks_dirty   = False
        self.live_client  = LiveClient(self.live_host, self.live_port,
                                       on_row=self._on_live_row,
                                       on_status=self._on_live_status)
        self.live_client.start()
        return f"LIVE → connecting to {self.live_host}:{self.live_port} ..."

    def _stop_live(self):
        if self.live_client:
            self.live_client.stop()
            self.live_client = None
        self.live_status = 'disconnected'
        if self.ping_index:
            self.mode         = 'playback'
            self.waterfall    = FastWaterfall(self._layout['TOTAL_W'], self._layout['CANVAS_H'])
            self.current_ping = -1
            self.n_pings      = len(self.ping_index)
            target = (self._pre_live_ping if 0 <= self._pre_live_ping < self.n_pings
                      else self.n_pings - 1)
            self._jump_to(target)
        else:
            self.mode         = 'launcher'
            self.waterfall    = FastWaterfall(self._layout['TOTAL_W'], self._layout['CANVAS_H'])
            self.current_ping = -1
            self.n_pings      = 0

    def _on_live_status(self, status):
        self.live_status = status

    def _on_live_row(self, port_raw, stbd_raw, meta):
        self._live_queue.put((port_raw, stbd_raw, meta))

    def _drain_live_queue(self):
        added = False
        while not self._live_queue.empty():
            try:
                port_raw, stbd_raw, meta = self._live_queue.get_nowait()
            except queue.Empty:
                break
            row = extract_ping_row_from_raw(
                port_raw, stbd_raw,
                self._layout['CHANNEL_W'], self._layout['PORT_ON'],
                self._layout['STBD_ON'], self._layout['GAP'],
                self._interp_xs_cache)
            self.waterfall.add_row(row)
            self.current_ping += 1
            self._live_n += 1
            self._live_last_meta = meta
            added = True
        if added and self.recording:
            self._record_count += 1

    # ── Range scale (top ruler, metres) ───────────────────────────────────────
    def _draw_range_scale(self, canvas, L, draw_x):
        """
        Top ruler: slant range in metres, 0 at nadir, increasing outwards
        to the same max at each outer edge.
        """
        if self._max_range_m <= 0:
            return
        cw    = L['CHANNEL_W']
        win_w = L['CANVAS_W']
        max_r = self._max_range_m
        if L['PORT_ON'] and L['STBD_ON']:
            port_left  = draw_x
            nadir_x    = draw_x + cw + L['GAP'] // 2
            stbd_right = draw_x + cw + L['GAP'] + cw
        elif L['PORT_ON']:
            total_w   = L['TOTAL_W']
            port_left = (win_w - total_w) // 2 if total_w <= win_w else draw_x
            nadir_x   = port_left + cw
            stbd_right = None
        elif L['STBD_ON']:
            total_w    = L['TOTAL_W']
            stbd_left  = (win_w - total_w) // 2 if total_w <= win_w else draw_x
            nadir_x    = stbd_left
            port_left  = None
            stbd_right = stbd_left + cw
        else:
            return
        metres_per_pixel = max_r / cw if cw > 0 else 0.0
        if metres_per_pixel <= 0:
            return
        tick_y  = 20
        tick_h  = 5
        label_y = tick_y - 4
        colour   = C_ACCENT
        t_colour = C_ACCENT
        shadow_c = (0, 0, 0)
        target_m  = max_r / 5.0
        magnitude = 10 ** int(np.floor(np.log10(max(target_m, 1e-3))))
        step_m    = magnitude
        for mul in (1, 2, 5, 10):
            step_m = magnitude * mul
            if step_m >= target_m:
                break
        if step_m / metres_per_pixel < 10:
            return
        fmt = "{:.0f}" if step_m >= 1 else "{:.1f}"

        def _blit_label(canvas, text_surf, pos):
            """Blit label with a 1px dark shadow for readability."""
            x, y = pos
            shadow = self.font_mono.render(text_surf[0], True, shadow_c) if isinstance(text_surf, tuple) else None
            canvas.blit(text_surf, (x + 1, y + 1))   # shadow offset
            canvas.blit(text_surf, (x, y))

        def side(direction):
            r = step_m
            while r <= max_r * 1.01:
                px = nadir_x + int(direction * r / metres_per_pixel)
                if direction == -1:
                    if port_left is None or px < port_left - 5:
                        break
                else:
                    if stbd_right is None or px > stbd_right + 5:
                        break
                pygame.draw.line(canvas, colour, (px, tick_y), (px, tick_y + tick_h), 1)
                lbl = self.font_mono.render(fmt.format(r), True, t_colour)
                # shadow pass
                shd = self.font_mono.render(fmt.format(r), True, (0, 0, 0))
                canvas.blit(shd, (px - shd.get_width() // 2 + 1, label_y + 1))
                canvas.blit(lbl, (px - lbl.get_width() // 2, label_y))
                r += step_m

        if L['PORT_ON']:
            side(-1)
        if L['STBD_ON']:
            side(1)

        # Nadir "0"
        pygame.draw.line(canvas, C_ACCENT2, (nadir_x, tick_y), (nadir_x, tick_y + tick_h), 2)
        shd  = self.font_mono.render("0", True, (0, 0, 0))
        zero = self.font_mono.render("0", True, C_ACCENT2)
        canvas.blit(shd,  (nadir_x - zero.get_width() // 2 + 1, label_y + 1))
        canvas.blit(zero, (nadir_x - zero.get_width() // 2, label_y))

        # Unit label
        unit = self.font_mono.render("m", True, t_colour)
        ushd = self.font_mono.render("m", True, (0, 0, 0))
        if stbd_right is not None:
            canvas.blit(ushd, (stbd_right + 5, label_y + 1))
            canvas.blit(unit, (stbd_right + 4, label_y))
        elif port_left is not None:
            canvas.blit(ushd, (port_left - unit.get_width() - 3, label_y + 1))
            canvas.blit(unit, (port_left - unit.get_width() - 4, label_y))

    def _act_toggle_record(self):
        if self.recording:
            self.recording = False
            return f"● REC stopped — {self._record_count} pings " \
                   f"(SatCenter recording command not yet wired up)"
        self.recording      = True
        self._record_count = 0
        return "● REC → (placeholder — will trigger SatCenter recording)"

    def _shutdown(self):
        self._save_settings()
        if self.live_client:
            self.live_client.stop()

    def _act_speed_up(self):
        self.speed_idx = min(self.speed_idx + 1, len(SPEED_STEPS) - 1)
        return f"SPEED → {SPEED_LABELS[self.speed_idx]}"

    def _act_speed_down(self):
        self.speed_idx = max(self.speed_idx - 1, 0)
        return f"SPEED → {SPEED_LABELS[self.speed_idx]}"

    def _act_fullscreen(self):
        self._toggle_fullscreen()
        return "FULLSCREEN toggled"

    def _act_screenshot(self):
        self._save_screenshot()
        return None

    def _act_toggle_port(self):
        self.port_on = not self.port_on
        self._rebuild_layout()
        return f"PORT channel → {'On' if self.port_on else 'Off'}"

    def _act_toggle_stbd(self):
        self.stbd_on = not self.stbd_on
        self._rebuild_layout()
        return f"STARBOARD channel → {'On' if self.stbd_on else 'Off'}"

    def _act_gain_dec(self):
        self.gain = max(self.GAIN_MIN, round(self.gain - self.GAIN_STEP, 3))
        self._update_enhancement(force=True)
        return f"GAIN → {self.gain:.2f}"

    def _act_gain_inc(self):
        self.gain = min(self.GAIN_MAX, round(self.gain + self.GAIN_STEP, 3))
        self._update_enhancement(force=True)
        return f"GAIN → {self.gain:.2f}"

    def _act_gamma_dec(self):
        self.gamma = max(self.GAMMA_MIN, round(self.gamma - self.GAMMA_STEP, 3))
        self._update_enhancement(force=True)
        return f"GAMMA → {self.gamma:.2f}"

    def _act_gamma_inc(self):
        self.gamma = min(self.GAMMA_MAX, round(self.gamma + self.GAMMA_STEP, 3))
        self._update_enhancement(force=True)
        return f"GAMMA → {self.gamma:.2f}"

    def _act_cycle_contrast(self):
        self.contrast_idx = (self.contrast_idx + 1) % len(CONTRAST_MODES)
        self._update_enhancement(force=True)
        return f"CONTRAST → {CONTRAST_MODES[self.contrast_idx]}"

    def _act_cycle_noise(self):
        self.noise_idx = (self.noise_idx + 1) % len(NOISE_MODES)
        self._update_enhancement(force=True)
        return f"NOISE REDUCTION → {NOISE_MODES[self.noise_idx]}"

    def _act_toggle_sharpen(self):
        self.sharpen = not self.sharpen
        self._update_enhancement(force=True)
        return f"SHARPEN → {'On' if self.sharpen else 'Off'}"

    def _act_toggle_agc(self):
        self.agc = not self.agc
        self._update_enhancement(force=True)
        return f"AGC → {'On' if self.agc else 'Off'}"

    def _act_cycle_target(self):
        self.target_idx = (self.target_idx + 1) % len(TARGET_MODES)
        self._update_enhancement(force=True)
        return f"TARGET MODE → {TARGET_MODES[self.target_idx]}"

    def _act_toggle_shadow(self):
        self.shadow_enh = not self.shadow_enh
        self._update_enhancement(force=True)
        return f"SHADOW ENHANCE → {'On' if self.shadow_enh else 'Off'}"

    def _act_toggle_overlay(self):
        self.overlay = not self.overlay
        self._update_enhancement(force=True)
        return f"TARGET/SHADOW OVERLAY → {'On' if self.overlay else 'Off'}"

    def _act_ksize_dec(self):
        self.target_ksize = max(TARGET_KSIZE_MIN, self.target_ksize - TARGET_KSIZE_STEP)
        self._update_enhancement(force=True)
        return f"TARGET SIZE → {self.target_ksize}px"

    def _act_ksize_inc(self):
        self.target_ksize = min(TARGET_KSIZE_MAX, self.target_ksize + TARGET_KSIZE_STEP)
        self._update_enhancement(force=True)
        return f"TARGET SIZE → {self.target_ksize}px"

    def _act_zoom_dec(self):
        self.zoom_idx = max(self.zoom_idx - 1, 0)
        self._rebuild_layout()
        return f"ZOOM → {ZOOM_STEPS[self.zoom_idx]:.2f}×"

    def _act_zoom_inc(self):
        self.zoom_idx = min(self.zoom_idx + 1, len(ZOOM_STEPS) - 1)
        self._rebuild_layout()
        return f"ZOOM → {ZOOM_STEPS[self.zoom_idx]:.2f}×"

    def _act_toggle_hdr(self):
        self.hdr = not self.hdr
        self._update_enhancement(force=True)
        return f"SMART HDR → {'On' if self.hdr else 'Off'}"

    def _act_cycle_lut(self):
        self.lut_idx = (self.lut_idx + 1) % len(LUT_NAMES)
        self._update_enhancement(force=True)
        return f"PALETTE → {LUT_NAMES[self.lut_idx]}"

    def _handle_double_click(self, pos):
        L = self._layout
        if L['CANVAS_W'] <= 0 or L['CANVAS_H'] <= 0:
            return None
        if self._view_zoom > 1.001:
            self._view_zoom = 1.0
            return "ZOOM → 1.0×"
        self._view_cx = max(0.0, min(1.0, pos[0] / L['CANVAS_W']))
        self._view_cy = max(0.0, min(1.0, pos[1] / L['CANVAS_H']))
        self._view_zoom = VIEW_ZOOM_FACTOR
        return f"ZOOM → {self._view_zoom:.1f}×  (double-click to reset)"

    def _zoom_src_surface(self, zw, zh):
        """Cached scratch surface for the view-zoom source window."""
        surf = self._zoom_src
        if surf is None or surf.get_width() != zw or surf.get_height() != zh:
            surf = pygame.Surface((zw, zh))
            self._zoom_src = surf
        return surf

    def _zoom_out_surface(self, src, w, h):
        """Scale into a reused destination surface — zero allocation."""
        surf = self._zoom_out
        if surf is None or surf.get_width() != w or surf.get_height() != h:
            surf = pygame.Surface((w, h))
            self._zoom_out = surf
        pygame.transform.scale(src, (w, h), surf)
        return surf

    def _apply_view_zoom(self, canvas):
        if self._view_zoom <= 1.001:
            return canvas
        w, h = canvas.get_size()
        zw = max(2, int(round(w / self._view_zoom)))
        zh = max(2, int(round(h / self._view_zoom)))
        cx = int(self._view_cx * w)
        cy = int(self._view_cy * h)
        x0 = max(0, min(w - zw, cx - zw // 2))
        y0 = max(0, min(h - zh, cy - zh // 2))
        # Copy the zoom window into a reused scratch surface, then scale()
        # into a reused destination: scale() is ~3× faster than
        # smoothscale() and visually identical on sonar imagery — and
        # zoomed redraws now allocate nothing at all.
        src = self._zoom_src_surface(zw, zh)
        src.blit(canvas, (0, 0), area=pygame.Rect(x0, y0, zw, zh))
        return self._zoom_out_surface(src, w, h)

    def _act_reset_settings(self):
        self.gain          = self.GAIN_DEFAULT
        self.gamma         = self.GAMMA_DEFAULT
        self.noise_idx     = 0
        self.contrast_idx  = CONTRAST_DEFAULT_IDX
        self.agc           = False
        self.sharpen       = False
        self.target_idx    = 0
        self.target_ksize  = TARGET_KSIZE_DEFAULT
        self.shadow_enh    = False
        self.overlay       = False
        self.hdr           = False
        self.lut_idx       = 0
        self._view_zoom    = 1.0
        if self.zoom_idx != ZOOM_DEFAULT:
            self.zoom_idx = ZOOM_DEFAULT
            self._rebuild_layout()
        self._update_enhancement(force=True)
        return "SETTINGS RESET to default"

    def _act_track_toggle(self):
        self.show_track = not self.show_track
        if not self.track_docked:
            self.show_controls = False
            self.show_help     = False
        self._rebuild_layout()
        return f"GPS TRACK → {'On' if self.show_track else 'Off'}"

    def _act_track_dock(self):
        self.track_docked = True
        self.show_track   = True
        self._rebuild_layout()
        return "GPS TRACK docked alongside waterfall"

    def _act_track_undock(self):
        self.track_docked = False
        self._rebuild_layout()
        return "GPS TRACK undocked"

    def _act_track_close(self):
        was_docked = self.track_docked
        self.show_track = False
        if was_docked:
            self._rebuild_layout()
        return None

    def _act_go_home(self):
        if self.mode == 'live':
            self._stop_live()
        self.playing    = False
        self.mode       = 'launcher'
        self._rect_menu = None
        return None

    def _act_track_recentre(self):
        self._track_pan_x = 0.0
        self._track_pan_y = 0.0
        return "GPS TRACK view recentred"

    def _build_key_actions(self):
        return {
            pygame.K_SPACE:         self._act_play_pause,
            pygame.K_r:              self._act_reset,
            pygame.K_HOME:           self._act_home,
            pygame.K_RIGHT:          self._act_step_right,
            pygame.K_LEFT:           self._act_step_left,
            pygame.K_UP:             self._act_speed_up,
            pygame.K_DOWN:           self._act_speed_down,
            pygame.K_f:              self._act_fullscreen,
            pygame.K_t:              self._act_screenshot,
            pygame.K_p:              self._act_toggle_port,
            pygame.K_s:              self._act_toggle_stbd,
            pygame.K_LEFTBRACKET:    self._act_gain_dec,
            pygame.K_RIGHTBRACKET:   self._act_gain_inc,
            pygame.K_MINUS:          self._act_gamma_dec,
            pygame.K_EQUALS:         self._act_gamma_inc,
            pygame.K_PLUS:           self._act_gamma_inc,
            pygame.K_c:              self._act_cycle_contrast,
            pygame.K_n:              self._act_cycle_noise,
            pygame.K_u:              self._act_toggle_sharpen,
            pygame.K_g:              self._act_toggle_agc,
            pygame.K_y:              self._act_cycle_target,
            pygame.K_h:              self._act_toggle_shadow,
            pygame.K_o:              self._act_toggle_overlay,
            pygame.K_l:              self._act_cycle_lut,
            pygame.K_d:              self._act_toggle_hdr,
            pygame.K_COMMA:          self._act_ksize_dec,
            pygame.K_PERIOD:         self._act_ksize_inc,
            pygame.K_F2:             self._act_toggle_live,
            pygame.K_F3:             self._act_toggle_record,
            pygame.K_v:              self._act_toggle_marks,
            pygame.K_DELETE:         self._act_clear_marks,
            pygame.K_BACKSPACE:      self._act_clear_marks,
        }

    def _build_control_rows(self):
        return [
            ('reset',   "Reset all settings to default", lambda: "", self._act_reset_settings),
            ('toggle',  "Live mode",           lambda: f"{self.live_status}" if self.mode == 'live' else "Off", self._act_toggle_live),
            ('toggle',  "Recording",           lambda: f"{self._record_count} pings" if self.recording else "Off", self._act_toggle_record),
            ('toggle',  "Play / Pause",        lambda: "Playing" if self.playing else "Paused", self._act_play_pause),
            ('toggle',  "Reset waterfall",      lambda: "",  self._act_reset),
            ('toggle',  "Jump to start (Home)", lambda: "",  self._act_home),
            ('stepper', "Speed",                lambda: SPEED_LABELS[self.speed_idx], self._act_speed_down, self._act_speed_up),
            ('stepper', "Zoom",                 lambda: f"{ZOOM_STEPS[self.zoom_idx]:.2f}×", self._act_zoom_dec, self._act_zoom_inc),
            ('toggle',  "GPS Track panel",       lambda: ("Docked" if self.track_docked else "On") if self.show_track else "Off", self._act_track_toggle),
            ('toggle',  "Fullscreen",           lambda: "On" if self._fullscreen else "Off", self._act_fullscreen),
            ('toggle',  "Screenshot",           lambda: "",  self._act_screenshot),
            ('toggle',  "Port channel",         lambda: "On" if self.port_on else "Off", self._act_toggle_port),
            ('toggle',  "Starboard channel",    lambda: "On" if self.stbd_on else "Off", self._act_toggle_stbd),
            ('stepper', "Gain",                 lambda: f"{self.gain:.2f}", self._act_gain_dec, self._act_gain_inc),
            ('stepper', "Gamma",                lambda: f"{self.gamma:.2f}", self._act_gamma_dec, self._act_gamma_inc),
            ('toggle',  "Smart HDR tone mapping", lambda: "On" if self.hdr else "Off", self._act_toggle_hdr),
            ('toggle',  "Colour palette",       lambda: LUT_NAMES[self.lut_idx], self._act_cycle_lut),
            ('toggle',  "Noise reduction",      lambda: NOISE_MODES[self.noise_idx], self._act_cycle_noise),
            ('toggle',  "Contrast",             lambda: CONTRAST_MODES[self.contrast_idx], self._act_cycle_contrast),
            ('toggle',  "AGC (dynamic range)",  lambda: "On" if self.agc else "Off", self._act_toggle_agc),
            ('toggle',  "Sharpen",              lambda: "On" if self.sharpen else "Off", self._act_toggle_sharpen),
            ('toggle',  "Target mode",          lambda: TARGET_MODES[self.target_idx], self._act_cycle_target),
            ('toggle',  "Shadow enhance",       lambda: "On" if self.shadow_enh else "Off", self._act_toggle_shadow),
            ('toggle',  "Target/shadow overlay",lambda: "On" if self.overlay else "Off", self._act_toggle_overlay),
            ('stepper', "Target size",          lambda: f"{self.target_ksize}px", self._act_ksize_dec, self._act_ksize_inc),
            ('toggle',  "Show marks",           lambda: "On" if self._show_marks else "Off", self._act_toggle_marks),
            ('toggle',  "Save marks",           lambda: (f"{len(self._marks)} unsaved" if self._marks_dirty
                                                          else f"{len(self._marks)} saved"), self._act_save_marks),
            ('toggle',  "Clear all marks",      lambda: f"{len(self._marks)} marks", self._act_clear_marks),
        ]

    def _refresh_layout_coords(self):
        """Recompute layout coordinates only — no waterfall rebuild.

        Used while dragging the GPS dock divider: the dock must track the
        cursor in real time, but the expensive waterfall rebuild
        (re-reading/re-interpolating every visible ping) is deferred to
        the single _rebuild_layout() call made on mouse-release.
        """
        L = self._layout
        dock_w = self.dock_w if (self.show_track and self.track_docked) else 0
        self._layout = _compute_layout(L['WIN_W'], L['WIN_H'], self.zoom_idx,
                                       self.port_on, self.stbd_on, dock_w)

    def _rebuild_layout(self, win_w=None, win_h=None):
        if win_w is None: win_w = self._layout['WIN_W']
        if win_h is None: win_h = self._layout['WIN_H']
        win_h = max(win_h, SCRUB_H + TOOLBAR_H + 200)
        old_current = self.current_ping
        self.dock_w = max(180, min(self.dock_w, win_w // 2))
        dock_w = self.dock_w if (self.show_track and self.track_docked) else 0
        self._layout = _compute_layout(win_w, win_h, self.zoom_idx,
                                       self.port_on, self.stbd_on, dock_w)
        self._row_cache.clear()
        self._interp_xs_cache.clear()
        self.waterfall = FastWaterfall(self._layout['TOTAL_W'],
                                       self._layout['CANVAS_H'])
        self._view_zoom = 1.0
        # Invalidate the grayscale cache (dimensions changed)
        self._cached_enhanced  = None
        self._cached_heavy_key = None
        self._cached_data_gen  = -1
        self._rect_menu     = None
        self._rect_dragging = False
        if self.mode == 'live':
            self.current_ping = -1
        elif old_current >= 0:
            self._jump_to(old_current)
        else:
            self.current_ping = -1

    def _clear_waterfall(self):
        self.waterfall.clear()
        self.current_ping = -1

    def _jump_to(self, target):
        if self._rebuilding:
            return
        if target < 0:
            self._clear_waterfall()
            return
        target    = min(target, self.n_pings - 1)
        canvas_h  = self._layout['CANVAS_H']
        win_first = max(0, target - canvas_h + 1)
        if (self.current_ping >= 0
                and target > self.current_ping
                and target - self.current_ping <= canvas_h):
            for i in range(self.current_ping + 1, target + 1):
                self.waterfall.add_row(self._build_row(i))
            self.current_ping = target
            return
        self._rebuilding  = True
        self._rebuild_msg = f"Seeking… {win_first} – {target}"
        self.waterfall    = FastWaterfall(self._layout['TOTAL_W'], canvas_h)
        for i in range(win_first, target + 1):
            self.waterfall.add_row(self._build_row(i))
            if i % 1000 == 0:
                # Just pump OS events so the window stays responsive — no
                # redraw, no enhancement. The one expensive enhancement
                # pass happens below, after the whole window has been
                # rebuilt, instead of every 200 rows along the way.
                self._rebuild_msg = f"Seeking… {i} / {target}"
                for ev in pygame.event.get():
                    if ev.type == pygame.QUIT:
                        pygame.quit(); sys.exit(0)
        self.current_ping = target
        self._rebuilding  = False
        self._update_enhancement(force=True)

    def _toggle_fullscreen(self):
        if self._fullscreen:
            w, h = self._windowed_size
            self.screen      = pygame.display.set_mode((w, h), pygame.RESIZABLE)
            self._fullscreen = False
        else:
            self._windowed_size = self.screen.get_size()
            self.screen         = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            self._fullscreen    = True
        _apply_titlebar_style()
        fw, fh = self.screen.get_size()
        self._rebuild_layout(fw, fh)

    def _save_screenshot(self):
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        if self.bsf_path:
            base = os.path.splitext(self.bsf_path)[0]
        else:
            base = os.path.join(os.getcwd(), "bsf_viewer")
        path = f"{base}_screenshot_{ts}.png"
        pygame.image.save(self.screen, path)
        self._screenshot_msg      = f"Saved  {os.path.basename(path)}"
        self._screenshot_msg_time = time.time()

    def _scrub_rect(self):
        L = self._layout
        return pygame.Rect(60, L['WIN_H'] - SCRUB_H + 6,
                           L['WIN_W'] - 120, SCRUB_H - 14)

    def _ping_from_mouse(self, mx):
        r = self._scrub_rect()
        t = max(0.0, min(1.0, (mx - r.left) / r.width))
        return int(t * (self.n_pings - 1))

    # ── Coordinate helpers for selection / marks ──────────────────────────
    def _get_nadir_x(self):
        L = self._layout
        cw = L['CHANNEL_W']
        win_w = L['CANVAS_W']
        total_w = L['TOTAL_W']
        draw_x = ((win_w - total_w) // 2 if total_w <= win_w
                  else -((total_w - win_w) // 2))
        if L['PORT_ON'] and L['STBD_ON']:
            return draw_x + cw + L['GAP'] // 2
        elif L['PORT_ON']:
            port_left = (win_w - total_w) // 2 if total_w <= win_w else draw_x
            return port_left + cw
        elif L['STBD_ON']:
            stbd_left = (win_w - total_w) // 2 if total_w <= win_w else draw_x
            return stbd_left
        return win_w // 2

    def _metres_per_pixel(self):
        cw = self._layout['CHANNEL_W']
        if cw <= 0 or self._max_range_m <= 0:
            return 0.0
        return self._max_range_m / cw

    def _screen_to_canvas(self, sx, sy):
        if self._view_zoom <= 1.001:
            return float(sx), float(sy)
        w, h = self._layout['CANVAS_W'], self._layout['CANVAS_H']
        crop_w = int(round(w / self._view_zoom))
        crop_h = int(round(h / self._view_zoom))
        cx = int(self._view_cx * w)
        cy = int(self._view_cy * h)
        x0 = max(0, min(w - crop_w, cx - crop_w // 2))
        y0 = max(0, min(h - crop_h, cy - crop_h // 2))
        return x0 + sx / self._view_zoom, y0 + sy / self._view_zoom

    def _canvas_to_screen(self, cx, cy):
        if self._view_zoom <= 1.001:
            return float(cx), float(cy)
        w, h = self._layout['CANVAS_W'], self._layout['CANVAS_H']
        crop_w = int(round(w / self._view_zoom))
        crop_h = int(round(h / self._view_zoom))
        cx0 = int(self._view_cx * w)
        cy0 = int(self._view_cy * h)
        x0 = max(0, min(w - crop_w, cx0 - crop_w // 2))
        y0 = max(0, min(h - crop_h, cy0 - crop_h // 2))
        return (cx - x0) * self._view_zoom, (cy - y0) * self._view_zoom

    def _canvas_to_world(self, cx, cy):
        """Canvas pixel → (ping_index, signed_range_m from nadir). Marks are
        stored in ping/range space, not screen space, so they stay correctly
        positioned as playback scrolls, and can be redrawn whenever this
        stretch of the file is back on screen.

        The waterfall scrolls with the newest ping at the TOP (row 0) and
        older pings further down, so screen row cy maps to
        ping = current_ping - cy (not current_ping - canvas_h + 1 + cy)."""
        ping = self.current_ping - int(cy)
        mpp = self._metres_per_pixel()
        nadir_x = self._get_nadir_x()
        signed_range = (cx - nadir_x) * mpp if mpp > 0 else 0.0
        return ping, signed_range

    def _estimate_metres_per_ping(self):
        if not self.gps_track.has_data or len(self.gps_track.ping_idx) < 2:
            return None
        dx = self.gps_track.xs[-1] - self.gps_track.xs[-2]
        dy = self.gps_track.ys[-1] - self.gps_track.ys[-2]
        dist = math.hypot(dx, dy)
        dpings = self.gps_track.ping_idx[-1] - self.gps_track.ping_idx[-2]
        if dpings <= 0 or dist < 0.1:
            return None
        return dist / dpings

    def _compute_rect_measurements(self, rx, ry, rw, rh):
        mpp = self._metres_per_pixel()
        if mpp <= 0:
            return None
        width_m = rw * mpp
        height_pings = int(rh)
        m_per_ping = self._estimate_metres_per_ping()
        if m_per_ping:
            height_m = rh * m_per_ping
            return f"{width_m:.1f} m  ×  {height_m:.1f} m  ({height_pings} pings)"
        return f"{width_m:.1f} m  ×  {height_pings} pings"

    # ── Marks ────────────────────────────────────────────────────────────
    def _create_mark_from_rect(self, rx, ry, rw, rh):
        """Build and store a mark from a canvas-space rectangle. Marks are
        saved the instant the drag finishes — closing the follow-up popup
        (however the person does it) can never lose the mark."""
        mpp = self._metres_per_pixel()
        nadir_x = self._get_nadir_x()
        p_top, _ = self._canvas_to_world(rx, ry)
        p_bot, _ = self._canvas_to_world(rx, ry + rh)
        r_left  = (rx - nadir_x) * mpp
        r_right = (rx + rw - nadir_x) * mpp
        if r_left > r_right:
            r_left, r_right = r_right, r_left
        self._mark_counter += 1
        mark = {
            'id':           self._mark_counter,
            'label':        f"T{self._mark_counter}",
            'ping_top':     min(p_top, p_bot),
            'ping_bottom':  max(p_top, p_bot),
            'range_left':   r_left,
            'range_right':  r_right,
            'width_m':      rw * mpp,
            'height_pings': int(rh),
            'timestamp':    datetime.datetime.now().strftime("%H:%M:%S"),
        }
        m_per_ping = self._estimate_metres_per_ping()
        if m_per_ping:
            mark['height_m'] = rh * m_per_ping
        self._marks.append(mark)
        self._marks_dirty = True
        return mark

    def _act_zoom_to_selection(self):
        if self._rect_menu is None:
            return None
        rx, ry, rw, rh = self._rect_menu['rect']
        L = self._layout
        zoom_x = L['CANVAS_W'] / max(rw * 1.2, 1)
        zoom_y = L['CANVAS_H'] / max(rh * 1.2, 1)
        zoom = min(zoom_x, zoom_y)
        zoom = max(1.0, min(zoom, 12.0))
        self._view_zoom = zoom
        self._view_cx = (rx + rw / 2) / max(L['CANVAS_W'], 1)
        self._view_cy = (ry + rh / 2) / max(L['CANVAS_H'], 1)
        self._rect_menu = None
        return f"ZOOM → {zoom:.1f}×  (double-click to reset)"

    def _act_remove_last_selection(self):
        if self._rect_menu is None:
            return None
        mark_id = self._rect_menu.get('mark_id')
        self._rect_menu = None
        if mark_id is None:
            return None
        self._marks = [m for m in self._marks if m['id'] != mark_id]
        self._marks_dirty = True
        return "Mark removed"

    def _act_dismiss_selection(self):
        self._rect_menu = None
        return None

    def _act_toggle_marks(self):
        self._show_marks = not self._show_marks
        return f"MARKS → {'Visible' if self._show_marks else 'Hidden'}"

    def _act_clear_marks(self):
        count = len(self._marks)
        self._marks.clear()
        self._mark_counter = 0
        self._rect_menu = None
        if count:
            self._marks_dirty = True
        return f"Cleared {count} mark{'s' if count != 1 else ''}" if count else "No marks"

    # ── Mark persistence (sidecar JSON next to the .bsf file) ─────────────
    def _marks_sidecar_path(self):
        if not self.bsf_path:
            return None
        base = os.path.splitext(self.bsf_path)[0]
        return f"{base}_marks.json"

    def _act_save_marks(self):
        path = self._marks_sidecar_path()
        if path is None:
            return "No file loaded — nothing to save marks against"
        payload = {
            'source_file': os.path.basename(self.bsf_path),
            'saved_at':    datetime.datetime.now().isoformat(timespec='seconds'),
            'marks':       self._marks,
        }
        try:
            with open(path, 'w') as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            return f"ERROR saving marks: {e}"
        self._marks_dirty = False
        return f"Saved {len(self._marks)} mark{'s' if len(self._marks) != 1 else ''} → {os.path.basename(path)}"

    def _load_marks_sidecar(self):
        """Auto-load a previously saved marks file for this .bsf, if any."""
        path = self._marks_sidecar_path()
        if path is None or not os.path.exists(path):
            return
        try:
            with open(path, 'r') as f:
                payload = json.load(f)
            marks = payload.get('marks', [])
        except Exception:
            return
        self._marks = marks
        self._mark_counter = max((m.get('id', 0) for m in marks), default=0)
        self._marks_dirty = False

    # ── Unsaved-marks confirmation ─────────────────────────────────────────
    def _request_action(self, action):
        """Run `action` immediately, unless there are unsaved marks — in
        which case pop up a confirm dialog first and run it afterwards."""
        if self._marks_dirty and self._marks:
            self._pending_action      = action
            self._show_unsaved_dialog = True
        else:
            self._perform_action(action)

    def _perform_action(self, action):
        if action == 'quit':
            self._shutdown(); pygame.quit(); sys.exit(0)
        elif action == 'home':
            self.playing = False
            if self.mode == 'live':
                self._stop_live()
            self.mode = 'launcher'
        elif action == 'go_live':
            self._guarded(self._act_toggle_live)
        elif callable(action):
            self._guarded(action)

    def _act_dialog_save_and_continue(self):
        msg = self._act_save_marks()
        if msg:
            self._flash(msg)
        action, self._pending_action = self._pending_action, None
        self._show_unsaved_dialog = False
        if action:
            self._perform_action(action)
        return None

    def _act_dialog_discard_and_continue(self):
        self._marks_dirty = False
        action, self._pending_action = self._pending_action, None
        self._show_unsaved_dialog = False
        if action:
            self._perform_action(action)
        return None

    def _act_dialog_cancel(self):
        self._pending_action       = None
        self._show_unsaved_dialog  = False
        return None

    def _draw_unsaved_dialog(self):
        WW, WH = self._layout['WIN_W'], self._layout['WIN_H']
        dim = pygame.Surface((WW, WH), pygame.SRCALPHA)
        dim.fill((0, 0, 0, 180))
        self.screen.blit(dim, (0, 0))
        n = len(self._marks)
        panel_w, panel_h = 380, 150
        panel_x = WW // 2 - panel_w // 2
        panel_y = WH // 2 - panel_h // 2
        panel = pygame.Rect(panel_x, panel_y, panel_w, panel_h)
        self._unsaved_dialog_rect = panel
        pygame.draw.rect(self.screen, (24, 28, 40), panel, border_radius=10)
        pygame.draw.rect(self.screen, C_WARN, panel, width=2, border_radius=10)
        title = self.font_big.render("Unsaved marks", True, C_WARN)
        self.screen.blit(title, (panel_x + 18, panel_y + 16))
        msg = self.font_mono.render(
            f"You have {n} unsaved mark{'s' if n != 1 else ''}. "
            f"Save before continuing?", True, C_TEXT)
        self.screen.blit(msg, (panel_x + 18, panel_y + 48))
        sidecar = self._marks_sidecar_path()
        if sidecar:
            path_txt = self.font_mono.render(
                f"→ {os.path.basename(sidecar)}", True, C_DIM)
            self.screen.blit(path_txt, (panel_x + 18, panel_y + 68))
        self._unsaved_dialog_buttons = []
        btn_specs = [
            ("Save",    C_ACCENT,  self._act_dialog_save_and_continue),
            ("Discard", C_ACCENT2, self._act_dialog_discard_and_continue),
            ("Cancel",  C_DIM,     self._act_dialog_cancel),
        ]
        btn_h = 28
        btn_y = panel_y + panel_h - btn_h - 14
        bx = panel_x + 18
        for label, colour, action in btn_specs:
            surf = self.font_mono.render(label, True, C_TEXT)
            bw = surf.get_width() + 26
            rect = pygame.Rect(bx, btn_y, bw, btn_h)
            pygame.draw.rect(self.screen, (38, 46, 62), rect, border_radius=6)
            pygame.draw.rect(self.screen, colour, rect, width=1, border_radius=6)
            self.screen.blit(surf, (rect.centerx - surf.get_width() // 2,
                                    rect.centery - surf.get_height() // 2))
            self._unsaved_dialog_buttons.append((rect, action))
            bx += bw + 10

    def _resolve_mouse_release(self, pos):
        """Finish whatever the left button was doing: a completed drag
        becomes a saved mark plus a small follow-up popup (zoom/undo); a
        plain click (no drag) is checked for a double-click."""
        if self._rect_dragging:
            self._rect_dragging = False
            self._mouse_is_down = False
            if self._rect_start and self._rect_end:
                x1, y1 = self._rect_start
                x2, y2 = self._rect_end
                rx, ry = min(x1, x2), min(y1, y2)
                rw, rh = abs(x2 - x1), abs(y2 - y1)
                if rw >= 4 and rh >= 4:
                    mark = self._create_mark_from_rect(rx, ry, rw, rh)
                    measurements = self._compute_rect_measurements(rx, ry, rw, rh)
                    self._rect_menu = {
                        'rect': (rx, ry, rw, rh),
                        'measurements': measurements or "",
                        'mark_id': mark['id'],
                    }
                    self._flash(f"Marked {mark['label']}")
            self._rect_start = None
            self._rect_end   = None
        elif self._mouse_is_down:
            self._mouse_is_down = False
            if self._mouse_down_pos:
                now_click = time.time()
                lx, ly = self._last_click_pos
                dx = pos[0] - lx
                dy = pos[1] - ly
                is_double = (
                    (now_click - self._last_click_time) * 1000.0 < DOUBLE_CLICK_MS
                    and (dx * dx + dy * dy) < DOUBLE_CLICK_DIST * DOUBLE_CLICK_DIST)
                self._last_click_time = now_click
                self._last_click_pos  = pos
                if is_double:
                    self._last_click_time = 0.0
                    self._guarded(lambda p=pos: self._handle_double_click(p))
            self._mouse_down_pos = None

    # ── Selection rectangle + marks drawing ───────────────────────────────
    def _draw_selection_rect(self, canvas):
        if not self._rect_dragging or self._rect_start is None or self._rect_end is None:
            return
        x1, y1 = self._rect_start
        x2, y2 = self._rect_end
        rx, ry = min(x1, x2), min(y1, y2)
        rw, rh = abs(x2 - x1), abs(y2 - y1)
        if rw < 2 or rh < 2:
            return
        rect = pygame.Rect(int(rx), int(ry), int(rw), int(rh))
        fill_surf = pygame.Surface((max(1, int(rw)), max(1, int(rh))), pygame.SRCALPHA)
        fill_surf.fill((255, 210, 50, 28))
        canvas.blit(fill_surf, (int(rx), int(ry)))
        pygame.draw.rect(canvas, C_PLAYHEAD, rect, 2)
        measurements = self._compute_rect_measurements(rx, ry, rw, rh)
        if measurements:
            txt = self.font_mono.render(measurements, True, C_TEXT)
            bg = pygame.Surface((txt.get_width() + 10, txt.get_height() + 6), pygame.SRCALPHA)
            bg.fill((10, 12, 18, 210))
            ty = ry + rh + 6
            if ty + bg.get_height() > self._layout['CANVAS_H']:
                ty = ry - bg.get_height() - 4
            canvas.blit(bg, (rx + 2, ty))
            canvas.blit(txt, (rx + 7, ty + 3))

    def _draw_marks(self, canvas):
        if not self._show_marks or not self._marks:
            return
        L = self._layout
        canvas_h = L['CANVAS_H']
        mpp = self._metres_per_pixel()
        if mpp <= 0:
            return
        nadir_x = self._get_nadir_x()
        for mark in self._marks:
            # Newest ping is row 0 (top); older pings are further down, so
            # a bigger ping index means a SMALLER screen row.
            y_a = self.current_ping - mark['ping_top']
            y_b = self.current_ping - mark['ping_bottom']
            y1, y2 = min(y_a, y_b), max(y_a, y_b)
            if y2 < 0 or y1 > canvas_h:
                continue
            y1c = max(0, y1)
            y2c = min(canvas_h - 1, y2)
            x1 = nadir_x + mark['range_left'] / mpp
            x2 = nadir_x + mark['range_right'] / mpp
            if x1 > x2:
                x1, x2 = x2, x1
            colour = MARK_COLOURS[mark['id'] % len(MARK_COLOURS)]
            rect = pygame.Rect(int(x1), int(y1c), max(1, int(x2 - x1)), max(1, int(y2c - y1c)))
            fill_surf = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
            fill_surf.fill((*colour, 35))
            canvas.blit(fill_surf, (rect.x, rect.y))
            pygame.draw.rect(canvas, colour, rect, 2)
            lbl = self.font_mono.render(mark['label'], True, colour)
            shd = self.font_mono.render(mark['label'], True, (0, 0, 0))
            canvas.blit(shd, (rect.x + 3, rect.y + 3))
            canvas.blit(lbl, (rect.x + 2, rect.y + 2))

    def _draw_rect_menu(self):
        if self._rect_menu is None:
            return
        rx, ry, rw, rh = self._rect_menu['rect']
        sx, sy = self._canvas_to_screen(rx + rw, ry + rh)
        menu_w, menu_h = 230, 90
        mx = int(sx) + 12
        my = int(sy) + 12
        WW, WH = self._layout['WIN_W'], self._layout['CANVAS_H']
        if mx + menu_w > WW - 8:
            mx = int(sx) - menu_w - 12
        if my + menu_h > WH - 8:
            my = int(sy) - menu_h - 12
        mx = max(4, mx)
        my = max(4, my)
        panel = pygame.Rect(mx, my, menu_w, menu_h)
        pygame.draw.rect(self.screen, (22, 28, 40), panel, border_radius=8)
        pygame.draw.rect(self.screen, C_ACCENT, panel, width=1, border_radius=8)
        measurements = self._rect_menu.get('measurements', '')
        if measurements:
            txt = self.font_mono.render(measurements, True, C_TEXT)
            self.screen.blit(txt, (mx + 10, my + 8))
        self._rect_menu_buttons = []
        btn_y = my + 34
        btn_h = 24
        btn_gap = 8
        btns = [
            ("Zoom",   C_ACCENT2, self._act_zoom_to_selection),
            ("Undo",   C_ACCENT2, self._act_remove_last_selection),
            ("×",      C_DIM,     self._act_dismiss_selection),
        ]
        bx = mx + 10
        for label, colour, action in btns:
            surf = self.font_mono.render(label, True, C_TEXT)
            bw = surf.get_width() + 18
            rect = pygame.Rect(bx, btn_y, bw, btn_h)
            pygame.draw.rect(self.screen, (36, 44, 60), rect, border_radius=5)
            pygame.draw.rect(self.screen, colour, rect, width=1, border_radius=5)
            self.screen.blit(surf, (rect.centerx - surf.get_width() // 2,
                                    rect.centery - surf.get_height() // 2))
            self._rect_menu_buttons.append((rect, action))
            bx += bw + btn_gap
        hint = self.font_mono.render("already saved — click outside to close", True, C_DIM)
        self.screen.blit(hint, (mx + 10, btn_y + btn_h + 8))

    # ── Waterfall area ────────────────────────────────────────────────────────
    def _canvas_surface(self):
        """Reuse one canvas surface across frames instead of allocating a
        fresh 2–5 MB Pygame surface every single draw. Reallocated only
        when the canvas size actually changes (resize, dock, zoom)."""
        w, h = self._layout['CANVAS_W'], self._layout['CANVAS_H']
        surf = self._canvas_surf
        if surf is None or surf.get_width() != w or surf.get_height() != h:
            surf = pygame.Surface((w, h))
            self._canvas_surf = surf
        return surf

    def _draw_waterfall_area(self):
        L      = self._layout
        canvas = self._canvas_surface()
        canvas.fill(C_BG)
        if not L['PORT_ON'] and not L['STBD_ON']:
            msg = self.font_big.render(
                "Both channels disabled — press P or S", True, C_WARN)
            canvas.blit(msg, (L['CANVAS_W']//2 - msg.get_width()//2,
                              L['CANVAS_H']//2 - msg.get_height()//2))
            self.screen.blit(canvas, (0, 0))
            return
        self.waterfall.blit_to(canvas, 0, L['CANVAS_W'])
        total_w = L['TOTAL_W']
        win_w   = L['CANVAS_W']
        draw_x  = ((win_w - total_w) // 2 if total_w <= win_w
                   else -((total_w - win_w) // 2))

        # ── Top gradient banner for the range scale ──────────────────────────
        # A vertical fade from near-opaque dark at the very top to fully
        # transparent ~34px down. The scale text/ticks sit inside this band,
        # so they're always legible regardless of how bright the waterfall
        # is underneath. The waterfall itself loses zero height — the banner
        # just darkens the top few pixels it overlaps.
        banner_h = 34
        if not hasattr(self, '_range_banner') or \
                self._range_banner.get_width() != win_w:
            grad = pygame.Surface((win_w, banner_h), pygame.SRCALPHA)
            for y in range(banner_h):
                alpha = int(200 * (1.0 - y / banner_h) ** 1.4)
                pygame.draw.line(grad, (6, 8, 14, alpha), (0, y), (win_w, y))
            self._range_banner = grad
        canvas.blit(self._range_banner, (0, 0))

        self._draw_range_scale(canvas, L, draw_x)
        self._draw_marks(canvas)
        self._draw_selection_rect(canvas)

        zoom = L['ZOOM']
        cw   = L['CHANNEL_W']
        if L['PORT_ON'] and L['STBD_ON']:
            port_cx = draw_x + cw // 2
            stbd_cx = draw_x + cw + L['GAP'] + cw // 2
            nadir_x = draw_x + cw + L['GAP'] // 2
            lbl_p   = self.font_mono.render(f"◄ PORT  [{zoom:.2f}×]", True, C_ACCENT)
            lbl_s   = self.font_mono.render("STARBOARD ►", True, C_ACCENT)
            canvas.blit(lbl_p, (port_cx - lbl_p.get_width()//2, 36))
            canvas.blit(lbl_s, (stbd_cx - lbl_s.get_width()//2, 36))
            pygame.draw.line(canvas, C_ACCENT2,
                             (nadir_x, 50), (nadir_x, L['CANVAS_H'] - 2), 1)
        elif L['PORT_ON']:
            lbl = self.font_mono.render(f"◄ PORT  [{zoom:.2f}×]", True, C_ACCENT)
            canvas.blit(lbl, (win_w//2 - lbl.get_width()//2, 36))
        elif L['STBD_ON']:
            lbl = self.font_mono.render(f"STARBOARD ►  [{zoom:.2f}×]", True, C_ACCENT)
            canvas.blit(lbl, (win_w//2 - lbl.get_width()//2, 36))

        # Rebuilding overlay
        if self._rebuilding:
            overlay = pygame.Surface((win_w, 26), pygame.SRCALPHA)
            overlay.fill((10, 12, 18, 200))
            canvas.blit(overlay, (0, L['CANVAS_H']//2 - 13))
            msg = self.font_mono.render(self._rebuild_msg, True, C_WARN)
            canvas.blit(msg, (win_w//2 - msg.get_width()//2, L['CANVAS_H']//2 - 8))

        # Big centered confirmation banner
        FLASH_DURATION = 1.3
        if getattr(self, '_flash_msg', "") and \
                time.time() - self._flash_msg_time < FLASH_DURATION:
            age   = time.time() - self._flash_msg_time
            alpha = 235 if age < FLASH_DURATION - 0.3 else \
                int(235 * (FLASH_DURATION - age) / 0.3)
            alpha = max(0, min(235, alpha))
            txt   = self.font_big.render(self._flash_msg, True, (10, 12, 18))
            pad_x, pad_y = 22, 12
            box_w = txt.get_width()  + pad_x * 2
            box_h = txt.get_height() + pad_y * 2
            box_x = win_w // 2 - box_w // 2
            box_y = 56
            box = pygame.Surface((box_w, box_h), pygame.SRCALPHA)
            box_colour = (255, 90, 90, alpha) if self._flash_msg.startswith("ERROR") \
                else (0, 220, 175, alpha)
            pygame.draw.rect(box, box_colour, (0, 0, box_w, box_h), border_radius=8)
            canvas.blit(box, (box_x, box_y))
            canvas.blit(txt, (box_x + pad_x, box_y + pad_y))

        canvas = self._apply_view_zoom(canvas)
        self.screen.blit(canvas, (0, 0))
        
    # ── Toolbar ───────────────────────────────────────────────────────────────
    def _draw_toolbar(self):
        L = self._layout
        WW, WH = L['WIN_W'], L['WIN_H']
        tb_y   = WH - SCRUB_H - TOOLBAR_H
        pygame.draw.rect(self.screen, C_TOOLBAR, (0, tb_y, WW, TOOLBAR_H))
        row1  = tb_y + 8
        if self.mode == 'live':
            live_colours = {'connected': C_ACCENT, 'connecting': C_WARN}
            colour = live_colours.get(self.live_status, C_ACCENT2)
            state  = f"● LIVE — {self.live_status}"
            s0 = self.font_big.render(state, True, colour)
        else:
            state = "▶ PLAY" if self.playing else "⏸ PAUSE"
            s0 = self.font_big.render(state, True, C_ACCENT if self.playing else C_DIM)
        self.screen.blit(s0, (12, row1))
        spd = self.font_mono.render(SPEED_LABELS[self.speed_idx], True, C_TEXT)
        self.screen.blit(spd, (12 + s0.get_width() + 16, row1 + 2))
        zm = self.font_mono.render(f"{ZOOM_STEPS[self.zoom_idx]:.2f}×", True, C_DIM)
        self.screen.blit(zm, (12 + s0.get_width() + 70, row1 + 2))
        self._toolbar_buttons = []
        btn_y = row1 - 4
        bx    = 12 + s0.get_width() + 130
        for label, on, colour, action in (
            ("F2 PLAYBACK" if self.mode == 'live' else "F2 LIVE",
             self.mode == 'live', C_ACCENT, self._act_toggle_live),
            ("F3 STOP" if self.recording else "F3 ● REC",
             self.recording, C_ACCENT2, self._act_toggle_record),
        ):
            surf = self.font_mono.render(label, True, C_TEXT)
            rect = pygame.Rect(bx, btn_y, surf.get_width() + 16, 22)
            bg   = (10, 40, 34) if on else (28, 34, 46)
            pygame.draw.rect(self.screen, bg, rect, border_radius=5)
            pygame.draw.rect(self.screen, colour, rect, width=1, border_radius=5)
            self.screen.blit(surf, (rect.x + 8, rect.y + (rect.h - surf.get_height()) // 2))
            self._toolbar_buttons.append((rect, action))
            bx += rect.w + 10
        if self.mode == 'live':
            if self._live_last_meta:
                ping_txt = (f"Ping {self.current_ping+1}  {self._live_last_meta['ts']}  "
                            f"{self._live_last_meta['freq_kHz']:.0f}kHz")
            else:
                ping_txt = f"Waiting for data from {self.live_host}:{self.live_port} ..."
            ping = self.font_mono.render(ping_txt, True, C_TEXT)
        elif self.current_ping >= 0:
            pct  = 100.0 * self.current_ping / max(1, self.n_pings - 1)
            off, sz  = self.ping_index[self.current_ping]
            cur_meta = get_ping_meta(self.data[off: off + sz])
            ping_txt = (f"Ping {self.current_ping+1}/{self.n_pings} "
                        f"({pct:.1f}%)  {cur_meta['ts']}")
            ping = self.font_mono.render(ping_txt, True, C_TEXT)
        else:
            ping = self.font_mono.render("Ping –/– (0%)", True, C_DIM)
        self.screen.blit(ping, (WW//2 - ping.get_width()//2, row1 + 2))
        if self.recording:
            rec_txt = self.font_mono.render(f"● REC {self._record_count}", True, C_ACCENT2)
            self.screen.blit(rec_txt, (WW - rec_txt.get_width() - 12, row1 + 20))
        if self._marks:
            unsaved = "●" if self._marks_dirty else ""
            marks_colour = C_WARN if self._marks_dirty else C_PLAYHEAD
            marks_txt = self.font_mono.render(
                f"Marks: {len(self._marks)}{unsaved}", True, marks_colour)
            self.screen.blit(marks_txt, (WW - marks_txt.get_width() - 12,
                                         row1 + 20 if self.recording else row1 + 38))
        hint = self.font_mono.render("TAB: Controls   F1: Help   M: Track   F2: Live   F3: Rec   Ctrl+S: Save marks", True, C_DIM)
        self.screen.blit(hint, (WW - hint.get_width() - 12, row1 + 2))
        active = []
        if self.noise_idx:    active.append(NOISE_MODES[self.noise_idx])
        if self.contrast_idx: active.append(CONTRAST_MODES[self.contrast_idx])
        if self.agc:           active.append("AGC")
        if self.sharpen:       active.append("Sharpen")
        if self.target_idx:   active.append(TARGET_MODES[self.target_idx])
        if self.shadow_enh:    active.append("ShadowEnh")
        if self.overlay:       active.append("Overlay")
        if self.hdr:           active.append("HDR")
        if self.lut_idx:       active.append(LUT_NAMES[self.lut_idx])
        if self._view_zoom > 1.001: active.append(f"View {self._view_zoom:.1f}×")
        if abs(self.gain  - self.GAIN_DEFAULT)  > 1e-6: active.append(f"Gain {self.gain:.2f}")
        if abs(self.gamma - self.GAMMA_DEFAULT) > 1e-6: active.append(f"Gamma {self.gamma:.2f}")
        if active:
            row2 = tb_y + 30
            line = "Active: " + "  ·  ".join(active)
            surf = self.font_mono.render(line, True, (255, 190, 120))
            self.screen.blit(surf, (WW//2 - surf.get_width()//2, row2))
        if (hasattr(self, '_screenshot_msg') and
                time.time() - self._screenshot_msg_time < 3.0):
            toast = self.font_mono.render(self._screenshot_msg, True, C_ACCENT)
            self.screen.blit(toast, (WW - toast.get_width() - 10, row1 + 2))

    # ── Controls popup ────────────────────────────────────────────────────────
    def _draw_controls_panel(self):
        WW, WH = self._layout['WIN_W'], self._layout['WIN_H']
        dim = pygame.Surface((WW, WH), pygame.SRCALPHA)
        dim.fill((0, 0, 0, 165))
        self.screen.blit(dim, (0, 0))
        cols        = 2 if WW >= 560 else 1
        row_h       = 34
        pad         = 16
        col_w       = min(320, (WW - pad * (cols + 1)) // cols)
        n           = len(self._control_rows)
        rows_per_col = -(-n // cols)
        panel_w = col_w * cols + pad * (cols + 1)
        panel_h = rows_per_col * row_h + 60
        panel_w = min(panel_w, WW - 24)
        panel_h = min(panel_h, WH - 24)
        panel_x = WW // 2 - panel_w // 2
        panel_y = max(12, WH // 2 - panel_h // 2)
        panel = pygame.Rect(panel_x, panel_y, panel_w, panel_h)
        self._controls_panel_rect = panel
        pygame.draw.rect(self.screen, (20, 24, 34), panel, border_radius=10)
        pygame.draw.rect(self.screen, C_ACCENT, panel, width=2, border_radius=10)
        title = self.font_big.render("Controls   (click outside, or TAB, to close)", True, C_ACCENT)
        self.screen.blit(title, (panel_x + pad, panel_y + 12))
        self._control_buttons = []
        start_y = panel_y + 44
        btn_w   = 26
        btn_gap = 4
        for i, row in enumerate(self._control_rows):
            kind, label = row[0], row[1]
            col = i // rows_per_col
            row_i = i % rows_per_col
            bx  = panel_x + pad + col * (col_w + pad)
            by  = start_y + row_i * row_h
            if by + row_h - 6 > panel_y + panel_h - 8:
                continue
            rect = pygame.Rect(bx, by, col_w, row_h - 6)
            if kind == 'reset':
                valuefn, action = row[2], row[3]
                pygame.draw.rect(self.screen, (52, 26, 26), rect, border_radius=6)
                pygame.draw.rect(self.screen, C_ACCENT2, rect, width=1, border_radius=6)
                lbl = self.font_mono.render(label, True, (255, 170, 170))
                self.screen.blit(lbl, (rect.centerx - lbl.get_width()//2,
                                       rect.y + (rect.h - lbl.get_height()) // 2))
                self._control_buttons.append((rect, action))
            elif kind == 'stepper':
                valuefn, dec_action, inc_action = row[2], row[3], row[4]
                pygame.draw.rect(self.screen, (32, 40, 55), rect, border_radius=6)
                lbl = self.font_mono.render(label, True, C_TEXT)
                self.screen.blit(lbl, (rect.x + 8, rect.y + (rect.h - lbl.get_height()) // 2))
                inc_rect = pygame.Rect(rect.right - btn_w - 3, rect.y + 3, btn_w, rect.h - 6)
                dec_rect = pygame.Rect(inc_rect.left - btn_gap - btn_w, rect.y + 3, btn_w, rect.h - 6)
                val = valuefn()
                if val:
                    vtxt = self.font_mono.render(str(val), True, C_ACCENT)
                    self.screen.blit(vtxt, (dec_rect.left - vtxt.get_width() - 10,
                                            rect.y + (rect.h - vtxt.get_height()) // 2))
                pygame.draw.rect(self.screen, (44, 54, 72), dec_rect, border_radius=4)
                pygame.draw.rect(self.screen, (44, 54, 72), inc_rect, border_radius=4)
                dsurf = self.font_mono.render("−", True, C_TEXT)
                isurf = self.font_mono.render("+", True, C_TEXT)
                self.screen.blit(dsurf, (dec_rect.centerx - dsurf.get_width()//2,
                                         dec_rect.centery - dsurf.get_height()//2))
                self.screen.blit(isurf, (inc_rect.centerx - isurf.get_width()//2,
                                         inc_rect.centery - isurf.get_height()//2))
                self._control_buttons.append((dec_rect, dec_action))
                self._control_buttons.append((inc_rect, inc_action))
            else:
                valuefn, action = row[2], row[3]
                pygame.draw.rect(self.screen, (32, 40, 55), rect, border_radius=6)
                lbl = self.font_mono.render(label, True, C_TEXT)
                self.screen.blit(lbl, (rect.x + 8, rect.y + (rect.h - lbl.get_height()) // 2))
                val = valuefn()
                if val:
                    vtxt = self.font_mono.render(str(val), True, C_ACCENT)
                    self.screen.blit(vtxt, (rect.right - vtxt.get_width() - 8,
                                            rect.y + (rect.h - vtxt.get_height()) // 2))
                self._control_buttons.append((rect, action))

    # ── Keybindings popup ─────────────────────────────────────────────────────
    def _draw_help_panel(self):
        WW, WH = self._layout['WIN_W'], self._layout['WIN_H']
        dim = pygame.Surface((WW, WH), pygame.SRCALPHA)
        dim.fill((0, 0, 0, 165))
        self.screen.blit(dim, (0, 0))
        lines = [
            ("Keybindings   (F1 or click outside to close)", C_ACCENT),
            ("", None),
            ("SPACE          play / pause",                 C_TEXT),
            ("LEFT / RIGHT   step one ping (when paused)",   C_TEXT),
            ("UP / DOWN      speed up / slow down",          C_TEXT),
            ("scroll wheel   zoom in/out (channel width)",   C_TEXT),
            ("double-click   zoom into the waterfall (double-click again to reset)", C_TEXT),
            ("HOME           jump to ping 0 and play",       C_TEXT),
            ("R              reset (clear waterfall)",       C_TEXT),
            ("F              fullscreen toggle",             C_TEXT),
            ("T              screenshot",                    C_TEXT),
            ("P / S          toggle port / starboard channel", C_TEXT),
            ("[ / ]          decrease / increase gain",      C_TEXT),
            ("- / =          decrease / increase gamma",     C_TEXT),
            ("L              cycle colour palette",          C_TEXT),
            ("drag           draw a selection → saves a mark", C_TEXT),
            ("V              toggle mark visibility",        C_TEXT),
            ("Ctrl+S         save marks to disk",             C_TEXT),
            ("Delete         clear all marks",                C_TEXT),
            ("", None),
            ("C              cycle contrast mode",           C_TEXT),
            ("N              cycle noise reduction",         C_TEXT),
            ("U              toggle sharpening",              C_TEXT),
            ("G              toggle AGC",                     C_TEXT),
            ("", None),
            ("Y              cycle target mode",              C_TEXT),
            ("H              toggle shadow enhancement",      C_TEXT),
            ("O              toggle target/shadow overlay",   C_TEXT),
            (", / .          decrease / increase target size", C_TEXT),
            ("", None),
            ("TAB            open/close the Controls panel",  C_TEXT),
            ("F1             open/close this panel",          C_TEXT),
            ("M              open/close the GPS Track panel",  C_TEXT),
            ("F2             start/stop LIVE mode",            C_TEXT),
            ("F3             start/stop recording",            C_TEXT),
            ("Q / Esc        quit  (Esc closes a panel / goes Home first)", C_TEXT),
        ]
        panel_w = 480
        panel_h = len(lines) * 20 + 24
        panel_x = WW // 2 - panel_w // 2
        panel_y = max(12, WH // 2 - panel_h // 2)
        panel = pygame.Rect(panel_x, panel_y, panel_w, min(panel_h, WH - 24))
        self._help_panel_rect = panel
        pygame.draw.rect(self.screen, (20, 24, 34), panel, border_radius=10)
        pygame.draw.rect(self.screen, C_ACCENT, panel, width=2, border_radius=10)
        y = panel_y + 12
        for text, colour in lines:
            if y > panel_y + panel.height - 20:
                break
            if text:
                surf = self.font_mono.render(text, True, colour)
                self.screen.blit(surf, (panel_x + 16, y))
            y += 20

    # ── GPS Track shared helpers ──────────────────────────────────────────────
    def _layout_track_buttons(self, header_rect, specs):
        btn_h = 20
        y = header_rect.y + (header_rect.h - btn_h) // 2
        x = header_rect.right - 8
        for label, action in specs:
            w = self.font_mono.size(label)[0] + 16
            x -= w
            rect = pygame.Rect(x, y, w, btn_h)
            pygame.draw.rect(self.screen, (40, 54, 72), rect, border_radius=4)
            surf = self.font_mono.render(label, True, C_TEXT)
            self.screen.blit(surf, (rect.centerx - surf.get_width()//2,
                                    rect.centery - surf.get_height()//2))
            self._track_buttons.append((rect, action))
            x -= 6

    def _draw_track_plot(self, plot):
        self._track_plot_rect = plot
        pygame.draw.rect(self.screen, (8, 22, 42), plot)
        raw_min_x, raw_max_x, raw_min_y, raw_max_y = self.gps_track.bounds()
        cx = (raw_min_x + raw_max_x) / 2.0 + self._track_pan_x
        cy = (raw_min_y + raw_max_y) / 2.0 + self._track_pan_y
        pad_frac = 0.15
        span_x = max(raw_max_x - raw_min_x, 1.0) * (1.0 + 2 * pad_frac)
        span_y = max(raw_max_y - raw_min_y, 1.0) * (1.0 + 2 * pad_frac)
        plot_aspect = plot.width / plot.height
        span_aspect = span_x / span_y
        if span_aspect < plot_aspect:
            span_x = span_y * plot_aspect
        else:
            span_y = span_x / plot_aspect
        min_x, max_x = cx - span_x / 2.0, cx + span_x / 2.0
        min_y, max_y = cy - span_y / 2.0, cy + span_y / 2.0
        scale = plot.width / span_x
        self._track_scale = scale
        def world_to_px(x, y):
            px = plot.centerx + (x - cx) * scale
            py = plot.centery - (y - cy) * scale
            return int(round(px)), int(round(py))
        target_px = 80.0
        raw_step  = target_px / scale
        magnitude = 10 ** math.floor(math.log10(max(raw_step, 0.1)))
        step_m = magnitude
        for mul in (1, 2, 5, 10):
            step_m = magnitude * mul
            if step_m >= raw_step:
                break
        ref_lat = self.gps_track.ref_lat
        coslat  = max(0.01, math.cos(math.radians(ref_lat)))
        deg_step_lat = math.degrees(step_m / GPSTrack.EARTH_R_M)
        deg_step_lon = math.degrees(step_m / (GPSTrack.EARTH_R_M * coslat))
        dec_lat = min(6, max(0, int(math.ceil(-math.log10(max(deg_step_lat, 1e-9)))) + 1))
        dec_lon = min(6, max(0, int(math.ceil(-math.log10(max(deg_step_lon, 1e-9)))) + 1))
        prev_clip = self.screen.get_clip()
        self.screen.set_clip(plot)
        i_min = math.floor(min_x / step_m)
        i_max = math.ceil(max_x / step_m)
        for i in range(i_min, i_max + 1):
            wx = i * step_m
            if wx < min_x - step_m * 1e-6 or wx > max_x + step_m * 1e-6:
                continue
            px, _ = world_to_px(wx, cy)
            pygame.draw.line(self.screen, C_TRACK_GRID, (px, plot.top), (px, plot.bottom), 1)
            _, lon = self.gps_track.unproject(wx, cy)
            lbl = self.font_mono.render(
                _format_degrees(lon, 'E', 'W', dec_lon), True, C_TRACK_LABEL)
            lx = px - lbl.get_width() // 2
            lx = max(plot.left + 1, min(lx, plot.right - lbl.get_width() - 1))
            self.screen.blit(lbl, (lx, plot.bottom - 14))
        j_min = math.floor(min_y / step_m)
        j_max = math.ceil(max_y / step_m)
        for j in range(j_min, j_max + 1):
            wy = j * step_m
            if wy < min_y - step_m * 1e-6 or wy > max_y + step_m * 1e-6:
                continue
            _, py = world_to_px(cx, wy)
            pygame.draw.line(self.screen, C_TRACK_GRID, (plot.left, py), (plot.right, py), 1)
            lat, _ = self.gps_track.unproject(cx, wy)
            lbl = self.font_mono.render(
                _format_degrees(lat, 'N', 'S', dec_lat), True, C_TRACK_LABEL)
            ly = max(plot.top + 1, min(py - 7, plot.bottom - 14))
            self.screen.blit(lbl, (plot.left + 2, ly))
        unit = self.font_mono.render("lat / lon (approx.)", True, C_TRACK_LABEL)
        self.screen.blit(unit, (plot.right - unit.get_width(), plot.top + 2))
        xs_all, ys_all = self.gps_track.xs, self.gps_track.ys
        upto = self.gps_track.history_upto(max(self.current_ping, 0))
        future_start = max(upto - 1, 0)
        if len(xs_all) - future_start >= 2:
            pts = [world_to_px(x, y) for x, y in zip(xs_all[future_start:], ys_all[future_start:])]
            local_pts = [(x - plot.left, y - plot.top) for x, y in pts]
            future_layer = pygame.Surface((plot.width, plot.height), pygame.SRCALPHA)
            pygame.draw.lines(future_layer, (*C_TRACK_TRAIL_DIM, 100), False, local_pts, 1)
            self.screen.blit(future_layer, (plot.left, plot.top))
        if upto >= 2:
            pts = [world_to_px(x, y) for x, y in zip(xs_all[:upto], ys_all[:upto])]
            pygame.draw.lines(self.screen, C_TRACK_TRAIL_BRIGHT, False, pts, 2)
        pos = self.gps_track.position_at_or_before(max(self.current_ping, 0))
        if pos is not None:
            x, y, fix_ping, fix_i = pos
            px, py = world_to_px(x, y)
            HEADING_WINDOW = 4
            end_i = fix_i if fix_i > 0 else min(1, len(xs_all) - 1)
            start_i = max(1, end_i - HEADING_WINDOW + 1)
            vx = vy = 0.0
            for i in range(start_i, end_i + 1):
                sdx = xs_all[i] - xs_all[i - 1]
                sdy = ys_all[i] - ys_all[i - 1]
                seg_len = math.hypot(sdx, sdy)
                if seg_len > 1e-6:
                    vx += sdx / seg_len
                    vy += sdy / seg_len
            if abs(vx) > 1e-6 or abs(vy) > 1e-6:
                angle = math.atan2(-vy, vx)
                size  = 14
                tip   = (px + size * math.cos(angle), py + size * math.sin(angle))
                bl    = (px + size * 0.55 * math.cos(angle + 2.6),
                         py + size * 0.55 * math.sin(angle + 2.6))
                br    = (px + size * 0.55 * math.cos(angle - 2.6),
                         py + size * 0.55 * math.sin(angle - 2.6))
                pygame.draw.polygon(self.screen, C_TRACK_SHIP, [tip, bl, br])
            pygame.draw.circle(self.screen, C_TRACK_SHIP, (px, py), 5)
            pygame.draw.circle(self.screen, C_TRACK_BG, (px, py), 5, width=2)
            ship_lat, ship_lon = self.gps_track.unproject(x, y)
            lbl1 = self.font_mono.render(f"ping {fix_ping}", True, C_TRACK_SHIP)
            lbl2 = self.font_mono.render(
                f"{_format_degrees(ship_lat, 'N', 'S', 5)}  {_format_degrees(ship_lon, 'E', 'W', 5)}",
                True, C_TRACK_SHIP)
            self.screen.blit(lbl1, (px + 10, py - 18))
            self.screen.blit(lbl2, (px + 10, py - 4))
        self.screen.set_clip(prev_clip)

    # ── GPS Track popup ───────────────────────────────────────────────────────
    def _draw_track_panel(self):
        WW, WH = self._layout['WIN_W'], self._layout['WIN_H']
        dim = pygame.Surface((WW, WH), pygame.SRCALPHA)
        dim.fill((0, 0, 0, 165))
        self.screen.blit(dim, (0, 0))
        panel_w = min(760, WW - 24)
        panel_h = min(560, WH - 24)
        panel_x = WW // 2 - panel_w // 2
        panel_y = max(12, WH // 2 - panel_h // 2)
        panel   = pygame.Rect(panel_x, panel_y, panel_w, panel_h)
        self._track_panel_rect = panel
        title_h = 34
        plot    = pygame.Rect(panel_x + 12, panel_y + title_h,
                              panel_w - 24, panel_h - title_h - 12)
        pygame.draw.rect(self.screen, C_TRACK_BG, panel, border_radius=10)
        pygame.draw.rect(self.screen, C_ACCENT, panel, width=2, border_radius=10)
        title = self.font_big.render("GPS Track", True, C_ACCENT)
        self.screen.blit(title, (panel_x + 12, panel_y + 9))
        header = pygame.Rect(panel_x, panel_y, panel_w, title_h)
        self._track_buttons = []
        self._layout_track_buttons(header, [
            ("×",        self._act_track_close),
            ("Dock ▶",   self._act_track_dock),
            ("Recentre", self._act_track_recentre),
        ])
        if not self.gps_track.has_data:
            msg = self.font_mono.render(
                "No valid GPS fixes found in this file.", True, C_WARN)
            self.screen.blit(msg, (plot.centerx - msg.get_width()//2,
                                   plot.centery - 8))
            self._track_plot_rect = None
            return
        self._draw_track_plot(plot)

    # ── GPS Track sidebar ─────────────────────────────────────────────────────
    def _draw_track_dock(self):
        L = self._layout
        dock_w = L['DOCK_W']
        if dock_w <= 0:
            self._track_dock_rect  = None
            self._dock_resize_rect = None
            return
        x0   = L['CANVAS_W']
        rect = pygame.Rect(x0, 0, dock_w, L['CANVAS_H'])
        self._track_dock_rect = rect
        pygame.draw.rect(self.screen, C_TRACK_BG, rect)
        handle_active = self._dock_resizing or self._dock_resize_hover
        divider_colour = C_PLAYHEAD if handle_active else C_ACCENT
        divider_w = 4 if handle_active else 2
        pygame.draw.line(self.screen, divider_colour,
                         (x0, 0), (x0, L['CANVAS_H']), divider_w)
        # Wider invisible hit zone around the divider, easier to grab than
        # the 2px line itself.
        self._dock_resize_rect = pygame.Rect(x0 - 4, 0, 8, L['CANVAS_H'])
        header_h = 30
        header   = pygame.Rect(x0, 0, dock_w, header_h)
        pygame.draw.rect(self.screen, (16, 40, 68), header)
        title = self.font_mono.render("GPS TRACK", True, C_ACCENT)
        self.screen.blit(title, (x0 + 10, 7))
        self._track_buttons = []
        self._layout_track_buttons(header, [
            ("×",        self._act_track_close),
            ("Float",    self._act_track_undock),
            ("Recentre", self._act_track_recentre),
        ])
        plot = pygame.Rect(rect.left + 8, header_h + 6,
                           dock_w - 16, rect.height - header_h - 14)
        if not self.gps_track.has_data:
            msg = self.font_mono.render("No GPS fixes in file.", True, C_WARN)
            self.screen.blit(msg, (plot.centerx - msg.get_width()//2,
                                   plot.centery - 8))
            self._track_plot_rect = None
            return
        self._draw_track_plot(plot)

    # ── Scrub bar ─────────────────────────────────────────────────────────────
    def _draw_scrub(self):
        L = self._layout
        pygame.draw.rect(self.screen, C_SCRUB_BG,
                         (0, L['WIN_H'] - SCRUB_H, L['WIN_W'], SCRUB_H))
        r = self._scrub_rect()
        pygame.draw.rect(self.screen, (30, 38, 55), r, border_radius=4)
        at_end = (self.current_ping >= self.n_pings - 1 and self.current_ping >= 0)
        is_live = self.mode == 'live'
        if self.current_ping >= 0:
            t      = self.current_ping / max(1, self.n_pings - 1)
            fill_w = int(r.width * t)
            if fill_w > 0:
                fill_colour = (0, 90, 60) if is_live else ((80, 40, 20) if at_end else (0, 100, 80))
                pygame.draw.rect(self.screen, fill_colour,
                                 (r.left, r.top, fill_w, r.height), border_radius=4)
            px = r.left + fill_w
            pygame.draw.rect(self.screen, C_PLAYHEAD,
                             (px - 2, r.top - 2, 4, r.height + 4), border_radius=2)
        else:
            pygame.draw.rect(self.screen, (60, 60, 70),
                             (r.left - 2, r.top - 2, 4, r.height + 4), border_radius=2)
        ll = self.font_mono.render("0", True, C_DIM)
        lr = self.font_mono.render(str(self.n_pings), True, C_DIM)
        self.screen.blit(ll, (r.left - ll.get_width() - 4, r.top + 1))
        self.screen.blit(lr, (r.right + 4, r.top + 1))
        if is_live:
            badge = self.font_mono.render(" ● LIVE ", True, C_BG)
            bw    = badge.get_width() + 6
            bh    = badge.get_height() + 2
            bx    = r.right - bw - 2
            by    = r.top + (r.height - bh) // 2
            pygame.draw.rect(self.screen, C_ACCENT, (bx, by, bw, bh), border_radius=3)
            self.screen.blit(badge, (bx + 3, by + 1))
        elif at_end:
            badge = self.font_mono.render(" END ", True, C_BG)
            bw    = badge.get_width() + 6
            bh    = badge.get_height() + 2
            bx    = r.right - bw - 2
            by    = r.top + (r.height - bh) // 2
            pygame.draw.rect(self.screen, C_END_BADGE, (bx, by, bw, bh), border_radius=3)
            self.screen.blit(badge, (bx + 3, by + 1))

    # ── Launcher ──────────────────────────────────────────────────────────────
    def _act_open_file(self):
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            path = filedialog.askopenfilename(
                title="Open BSF file",
                filetypes=[("BSF sonar files", "*.bsf"), ("All files", "*.*")])
            root.destroy()
        except Exception as e:
            return f"Couldn't open a file dialog ({e})"
        if not path:
            return None
        if self._load_file(path):
            return f"Loaded {os.path.basename(path)} — {self.n_pings:,} pings"
        return f"Couldn't open {os.path.basename(path)} — see console for details"

    def _draw_launcher(self):
        self.screen.fill(C_BG)
        WW, WH = self._layout['WIN_W'], self._layout['WIN_H']
        if not hasattr(self, '_launcher_title_font'):
            try:
                self._launcher_title_font = pygame.font.SysFont("consolas", 36, bold=True)
            except Exception:
                self._launcher_title_font = pygame.font.SysFont(None, 40, bold=True)
        cx, cy0 = WW // 2, int(WH * 0.30)
        for i, r in enumerate((260, 200, 140, 80)):
            shade = tuple(min(255, int(c * (0.10 + 0.05 * i))) for c in C_ACCENT)
            pygame.draw.circle(self.screen, shade, (cx, cy0), r, width=1)
        title = self._launcher_title_font.render("BSF Viewer", True, C_TEXT)
        self.screen.blit(title, (cx - title.get_width() // 2, cy0 - title.get_height() // 2 - 6))
        subtitle = self.font_mono.render(
            "Side-scan sonar — playback & live viewer", True, C_DIM)
        self.screen.blit(subtitle, (cx - subtitle.get_width() // 2,
                                    cy0 + title.get_height() // 2 + 8))
        card_w, card_h, gap = 300, 150, 40
        total_w = card_w * 2 + gap
        left_x  = cx - total_w // 2
        top_y   = int(WH * 0.52)
        self._launcher_buttons = []
        cards = [
            (left_x, "📂", "Open BSF File", "Browse for a recorded .bsf file",
             C_ACCENT, self._act_open_file),
            (left_x + card_w + gap, "📡", "Go Live", f"Connect to {self.live_host}:{self.live_port}",
             C_ACCENT2, self._act_toggle_live),
        ]
        mouse_pos = pygame.mouse.get_pos()
        any_hover = False
        for x, icon, label, desc, colour, action in cards:
            rect = pygame.Rect(x, top_y, card_w, card_h)
            hover = rect.collidepoint(mouse_pos)
            any_hover = any_hover or hover
            draw_rect = rect.move(0, -6) if hover else rect
            bg = tuple(min(255, c + (34 if hover else 0)) for c in (18, 22, 30))
            border_w = 3 if hover else 2
            if hover:
                glow = pygame.Surface((draw_rect.w + 16, draw_rect.h + 16), pygame.SRCALPHA)
                pygame.draw.rect(glow, (*colour, 55),
                                 glow.get_rect().inflate(-4, -4), border_radius=16)
                self.screen.blit(glow, (draw_rect.x - 8, draw_rect.y - 8))
            pygame.draw.rect(self.screen, bg, draw_rect, border_radius=14)
            pygame.draw.rect(self.screen, colour, draw_rect, width=border_w, border_radius=14)
            icon_s = self._launcher_title_font.render(icon, True, colour)
            self.screen.blit(icon_s, (draw_rect.centerx - icon_s.get_width() // 2, draw_rect.y + 18))
            label_s = self.font_big.render(label, True, C_TEXT)
            self.screen.blit(label_s, (draw_rect.centerx - label_s.get_width() // 2, draw_rect.y + 76))
            desc_s = self.font_mono.render(desc, True, C_DIM)
            self.screen.blit(desc_s, (draw_rect.centerx - desc_s.get_width() // 2, draw_rect.y + 102))
            self._launcher_buttons.append((rect, action))
        self._set_cursor('hand' if any_hover else 'arrow')
        hint = self.font_mono.render(
            "F2 also starts Live · F3 arms Recording · Q to quit", True, C_DIM)
        self.screen.blit(hint, (cx - hint.get_width() // 2, top_y + card_h + 36))
        if self._flash_msg and (time.time() - self._flash_msg_time) < 3.0:
            msg = self.font_mono.render(self._flash_msg, True, C_ACCENT)
            self.screen.blit(msg, (cx - msg.get_width() // 2, top_y + card_h + 66))

    # ── Cursor management ───────────────────────────────────────────────────
    def _set_cursor(self, kind):
        if getattr(self, '_current_cursor', None) == kind:
            return
        try:
            cursor_map = {
                'arrow':   pygame.SYSTEM_CURSOR_ARROW,
                'hand':    pygame.SYSTEM_CURSOR_HAND,
                'sizewe':  pygame.SYSTEM_CURSOR_SIZEWE,
            }
            pygame.mouse.set_cursor(cursor_map[kind])
            self._current_cursor = kind
        except Exception:
            pass

    # ── Master draw ───────────────────────────────────────────────────────────
    def _draw(self):
        if self.mode == 'launcher':
            self._draw_launcher()
            if self._show_unsaved_dialog:
                self._draw_unsaved_dialog()
            pygame.display.flip()
            return
        self._update_enhancement()
        self.screen.fill(C_BG)
        self._draw_waterfall_area()
        if self.show_track and self.track_docked:
            self._draw_track_dock()
            want_resize_cursor = self._dock_resizing or self._dock_resize_hover
            self._set_cursor('sizewe' if want_resize_cursor else 'arrow')
        else:
            self._set_cursor('arrow')
        self._draw_toolbar()
        self._draw_scrub()
        self._draw_rect_menu()
        if self.show_controls:
            self._draw_controls_panel()
        elif self.show_help:
            self._draw_help_panel()
        elif self.show_track and not self.track_docked:
            self._draw_track_panel()
        if self._show_unsaved_dialog:
            self._draw_unsaved_dialog()
        pygame.display.flip()

    # ── Main loop ─────────────────────────────────────────────────────────────
    def run(self):
        dragging = False
        self._screenshot_msg      = ""
        self._screenshot_msg_time = 0.0
        self._flash_msg           = ""
        self._flash_msg_time      = 0.0
        self._key_actions         = self._build_key_actions()
        self._control_rows        = self._build_control_rows()
        self._control_buttons     = []
        self._toolbar_buttons     = []
        self._launcher_buttons    = []
        while True:
            now = time.time()
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self._request_action('quit')
                elif event.type == pygame.KEYDOWN:
                    k = event.key
                    if self._show_unsaved_dialog:
                        if k == pygame.K_ESCAPE:
                            self._act_dialog_cancel()
                        elif k == pygame.K_RETURN:
                            self._guarded(self._act_dialog_save_and_continue)
                        continue
                    if k == pygame.K_q:
                        self._request_action('quit')
                    elif k == pygame.K_ESCAPE:
                        if self._rect_menu is not None:
                            self._rect_menu = None
                        elif self.show_controls or self.show_help or self.show_track:
                            track_was_docked = self.show_track and self.track_docked
                            self.show_controls = False
                            self.show_help     = False
                            self.show_track    = False
                            if track_was_docked:
                                self._rebuild_layout()
                        elif self.mode != 'launcher':
                            self._request_action('home')
                        else:
                            self._request_action('quit')
                    elif k == pygame.K_TAB:
                        self.show_controls = not self.show_controls
                        self.show_help     = False
                        self._rect_menu    = None
                        if not self.track_docked:
                            self.show_track = False
                    elif k == pygame.K_F1:
                        self.show_help      = not self.show_help
                        self.show_controls  = False
                        self._rect_menu     = None
                        if not self.track_docked:
                            self.show_track = False
                    elif k == pygame.K_m:
                        self._guarded(self._act_track_toggle)
                    elif k == pygame.K_s and (event.mod & pygame.KMOD_CTRL):
                        self._guarded(self._act_save_marks)
                    elif k in self._key_actions:
                        self._guarded(self._key_actions[k])
                elif event.type == pygame.MOUSEWHEEL:
                    if self._mouse_is_down or self._rect_dragging:
                        # A trackpad drag can spuriously report tiny wheel
                        # deltas (two-finger nudge, inertial residue); don't
                        # let that cancel an in-progress selection rectangle.
                        pass
                    else:
                        self.zoom_idx = (min(self.zoom_idx + 1, len(ZOOM_STEPS) - 1)
                                         if event.y > 0
                                         else max(self.zoom_idx - 1, 0))
                        self._rebuild_layout()
                elif event.type == pygame.VIDEORESIZE and not self._fullscreen:
                    self._windowed_size = (event.w, event.h)
                    self._rebuild_layout(event.w, event.h)
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    if event.button != 1:
                        pass
                    elif self._show_unsaved_dialog:
                        for rect, action in self._unsaved_dialog_buttons:
                            if rect.collidepoint(event.pos):
                                self._guarded(action)
                                break
                    else:
                        consumed = False
                        if self.mode == 'launcher':
                            for rect, action in self._launcher_buttons:
                                if rect.collidepoint(event.pos):
                                    self._guarded(action)
                                    break
                            continue
                        # Selection popup takes priority
                        if self._rect_menu is not None:
                            consumed = True
                            hit = False
                            for rect, action in self._rect_menu_buttons:
                                if rect.collidepoint(event.pos):
                                    self._guarded(action)
                                    hit = True
                                    break
                            if not hit:
                                self._rect_menu = None
                        elif (self.show_track and self.track_docked
                                and self._dock_resize_rect
                                and self._dock_resize_rect.collidepoint(event.pos)):
                            consumed = True
                            self._dock_resizing = True
                        elif (self.show_track and self.track_docked
                                and self._track_dock_rect
                                and self._track_dock_rect.collidepoint(event.pos)):
                            consumed = True
                            hit = False
                            for rect, action in self._track_buttons:
                                if rect.collidepoint(event.pos):
                                    self._guarded(action)
                                    hit = True
                                    break
                            if (not hit and self._track_plot_rect
                                    and self._track_plot_rect.collidepoint(event.pos)):
                                self._track_dragging  = True
                                self._track_drag_last = event.pos
                        elif self.show_controls:
                            consumed = True
                            hit = False
                            for rect, action in self._control_buttons:
                                if rect.collidepoint(event.pos):
                                    self._guarded(action)
                                    hit = True
                                    break
                            if not hit and not (self._controls_panel_rect
                                                and self._controls_panel_rect.collidepoint(event.pos)):
                                self.show_controls = False
                        elif self.show_help:
                            consumed = True
                            if not (self._help_panel_rect
                                    and self._help_panel_rect.collidepoint(event.pos)):
                                self.show_help = False
                        elif self.show_track and not self.track_docked:
                            consumed = True
                            hit = False
                            for rect, action in self._track_buttons:
                                if rect.collidepoint(event.pos):
                                    self._guarded(action)
                                    hit = True
                                    break
                            if not hit:
                                if (self._track_panel_rect
                                        and self._track_panel_rect.collidepoint(event.pos)):
                                    if (self._track_plot_rect
                                            and self._track_plot_rect.collidepoint(event.pos)):
                                        self._track_dragging  = True
                                        self._track_drag_last = event.pos
                                else:
                                    self.show_track = False
                        if not consumed:
                            for rect, action in self._toolbar_buttons:
                                if rect.collidepoint(event.pos):
                                    self._guarded(action)
                                    consumed = True
                                    break
                        if (not consumed and self.mode in ('playback', 'live')
                                and event.pos[0] < self._layout['CANVAS_W']
                                and event.pos[1] < self._layout['CANVAS_H']):
                            self._mouse_is_down  = True
                            self._mouse_down_pos = event.pos
                        if (not consumed and self.mode == 'playback'
                                and self._scrub_rect().collidepoint(event.pos)):
                            dragging      = True
                            self.playing  = False
                            self._jump_to(self._ping_from_mouse(event.pos[0]))
                elif event.type == pygame.MOUSEBUTTONUP:
                    if event.button == 1:
                        dragging             = False
                        self._track_dragging = False
                        if self._dock_resizing:
                            self._dock_resizing = False
                            self._rebuild_layout()
                            self._save_settings()
                        else:
                            self._resolve_mouse_release(event.pos)
                elif event.type == pygame.MOUSEMOTION:
                    if self._dock_resizing:
                        # Dock's right edge is pinned to the window edge,
                        # so width = distance from the cursor to that edge.
                        new_w = self._layout['WIN_W'] - event.pos[0]
                        new_w = max(180, min(new_w, self._layout['WIN_W'] // 2))
                        if new_w != self.dock_w:
                            self.dock_w = new_w
                            # Deferred dock resize: only the cheap layout
                            # coordinates are refreshed so the dock tracks
                            # the cursor live; the expensive waterfall
                            # rebuild happens once, on mouse-release.
                            self._refresh_layout_coords()
                    elif self._track_dragging:
                        mx, my = event.pos
                        lx, ly = self._track_drag_last
                        if self._track_scale:
                            self._track_pan_x -= (mx - lx) / self._track_scale
                            self._track_pan_y += (my - ly) / self._track_scale
                        self._track_drag_last = event.pos
                    elif self._mouse_is_down and self._mouse_down_pos:
                        dx = event.pos[0] - self._mouse_down_pos[0]
                        dy = event.pos[1] - self._mouse_down_pos[1]
                        if (dx * dx + dy * dy) > RECT_MIN_DRAG_PX * RECT_MIN_DRAG_PX:
                            if not self._rect_dragging:
                                self._rect_dragging = True
                                cx, cy = self._screen_to_canvas(*self._mouse_down_pos)
                                self._rect_start = (cx, cy)
                            cx, cy = self._screen_to_canvas(*event.pos)
                            self._rect_end = (cx, cy)
                    elif dragging:
                        self._jump_to(self._ping_from_mouse(event.pos[0]))
                    self._dock_resize_hover = bool(
                        self._dock_resize_rect
                        and self._dock_resize_rect.collidepoint(event.pos))

            # ── Advance: live stream, or file playback ──
            if self.mode == 'live':
                self._drain_live_queue()
            elif self.playing and self.current_ping < self.n_pings - 1:
                speed_val = SPEED_STEPS[self.speed_idx]
                if speed_val == 0:
                    nxt = self.current_ping + 1
                    self.waterfall.add_row(self._build_row(nxt))
                    self.current_ping = nxt
                    if self.recording:
                        self._record_count += 1
                else:
                    delay = speed_val / 1000.0
                    if now - self.last_advance >= delay:
                        nxt = self.current_ping + 1
                        self.waterfall.add_row(self._build_row(nxt))
                        self.current_ping = nxt
                        self.last_advance = now
                        if self.recording:
                            self._record_count += 1
            elif self.playing and self.current_ping >= self.n_pings - 1:
                self.playing = False
                # End of file → paused: snap the pipeline back to the full
                # work budget (playing/live runs at a capped 600 kpx).
                self._update_enhancement(force=True)

            if self.mode == 'live':
                pygame.display.set_caption(
                    f"BSF Viewer — LIVE {self.live_host}:{self.live_port} "
                    f"({self.live_status})")
            elif self.mode == 'launcher':
                pygame.display.set_caption("BSF Viewer")
            elif self.current_ping >= 0:
                off, sz  = self.ping_index[self.current_ping]
                cur_meta = get_ping_meta(self.data[off: off + sz])
                pygame.display.set_caption(
                    f"BSF Viewer — {os.path.basename(self.bsf_path)} "
                    f"— {cur_meta['ts']}")
            else:
                pygame.display.set_caption(
                    f"BSF Viewer — {os.path.basename(self.bsf_path)}")
            self._draw()
            self.clock.tick(60)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    BSFViewer(path).run()


def _show_fatal_error(message):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("BSF Viewer — Error", message)
        root.destroy()
    except Exception:
        pass


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        import traceback
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        log_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "bsf_viewer_error.log")
        try:
            with open(log_path, "w") as f:
                f.write(tb)
        except Exception:
            log_path = None
        msg = tb
        if log_path:
            msg += f"\n\nAlso saved to:\n{log_path}"
        _show_fatal_error(msg)
        sys.exit(1)