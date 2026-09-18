"""Real-time waterfall pixel enhancement pipeline: denoise, AGC, contrast
(histogram-eq/CLAHE), target/shadow enhancement, sharpening, HDR."""
from collections import namedtuple
import cv2
import numpy as np

EnhanceParams = namedtuple('EnhanceParams', [
    'noise_idx', 'contrast_idx', 'agc', 'target_idx', 'target_ksize',
    'shadow_enh', 'overlay', 'sharpen', 'gain', 'gamma', 'lut_idx', 'fast', 'hdr',
])

HEAVY_FIELDS = ('noise_idx', 'contrast_idx', 'agc', 'target_idx',
                'target_ksize', 'shadow_enh', 'overlay', 'sharpen', 'hdr')


def heavy_key(p):
    """Extract the subset of params that affect the expensive pipeline."""
    return tuple(getattr(p, f) for f in HEAVY_FIELDS)


NOISE_MODES = ["Off", "Median", "Bilateral", "NL-Means"]
CONTRAST_MODES = ["Off", "Global HistEq", "CLAHE Low", "CLAHE Med", "CLAHE High"]
# Was 3 (CLAHE Med), briefly 4 (CLAHE High) while AMP_NORM_* below was still
# a 24-bit-masked linear decode papering over lost dynamic range. Now that
# extract_raw_channels() does a proper log/percentile stretch on the full
# amplitude, most of the contrast work is already done before this stage —
# CLAHE Low adds a little local pop without smearing small hard-target
# returns the way a higher clip limit does.
CONTRAST_DEFAULT_IDX = 2

TARGET_MODES = ["Off", "Top-Hat Bright", "Top-Hat Bright+Dark", "CFAR Contrast"]

_clahe_low  = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8))
_clahe_med  = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
_clahe_high = cv2.createCLAHE(clipLimit=6.0, tileGridSize=(8, 8))


# ── Image enhancement pipeline ────────────────────────────────────────────
# Returns (enhanced_uint8, target_mask, shadow_mask) — stays in uint8
# throughout to avoid float32 roundtrips.
def enhance_pixels(raw2d, p):
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
        img8 = _clahe_low.apply(img8)
    elif cm == "CLAHE Med":
        img8 = _clahe_med.apply(img8)
    elif cm == "CLAHE High":
        img8 = _clahe_high.apply(img8)

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
        img8 = smart_hdr(img8, strength=1.5, fast=p.fast)

    if p.sharpen:
        blur = cv2.GaussianBlur(img8, (0, 0), sigmaX=1.4)
        img8 = cv2.addWeighted(img8, 1.6, blur, -0.6, 0)

    return img8, target_mask, shadow_mask


def smart_hdr(img8, strength=1.5, fast=False):
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


def tint(rgb, mask, colour, alpha=0.55):
    if mask is None or not mask.any():
        return rgb
    c = np.array(colour, dtype=np.float32)
    region = rgb[mask].astype(np.float32)
    rgb[mask] = (region * (1 - alpha) + c * alpha).astype(np.uint8)
    return rgb
