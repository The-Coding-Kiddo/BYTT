"""Pure parsing/validation for Hytem wire packets and .bsf channel data.
No sockets, no Qt — safe to unit test directly."""
import struct
import numpy as np
from bytt.protocol import constants as pc


def parse_channel(ping, ch_idx):
    off = pc.CH_SR_OFFSET + ch_idx * pc.CH_SR_SIZE
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
    if len(nav_bytes) < pc.NAV_LAT_OFFSET + 8 or len(nav_bytes) < pc.NAV_LON_OFFSET + 8:
        return None
    try:
        lat = struct.unpack_from(pc.NAV_FIELD_FMT, nav_bytes, pc.NAV_LAT_OFFSET)[0]
        lon = struct.unpack_from(pc.NAV_FIELD_FMT, nav_bytes, pc.NAV_LON_OFFSET)[0]
        ts  = struct.unpack_from(pc.NAV_FIELD_FMT, nav_bytes, pc.NAV_TS_OFFSET)[0]
    except struct.error:
        return None
    if not np.isfinite(lat) or not np.isfinite(lon):
        return None
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return None
    if abs(lat) < 1e-6 and abs(lon) < 1e-6:
        return None
    return lat, lon, ts

def extract_raw_channels(ping, p_lo=pc.AMP_NORM_P_LO_DEFAULT, p_hi=pc.AMP_NORM_P_HI_DEFAULT):
    ch   = parse_channel(ping, 0)
    n    = ch['half_samples']
    base = pc.SAMPLE_OFFSET
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
            return pc.AMP_NORM_P_LO_DEFAULT, pc.AMP_NORM_P_HI_DEFAULT
        idxs = np.linspace(0, n_pings - 1, min(n_sample, n_pings)).astype(int)
        samples = []
        for i in idxs:
            off, sz = ping_index[i]
            ping = data[off: off + sz]
            n = parse_channel(ping, 0)['half_samples']
            if n <= 0:
                continue
            base = pc.SAMPLE_OFFSET
            avail = len(ping) - base
            if avail <= 0:
                continue
            b2r = min(avail, n * 2 * 4)
            ne  = (b2r // 4)
            raw = np.frombuffer(ping[base: base + ne * 4], dtype='<u4', count=ne)
            if raw.size:
                samples.append(raw.astype(np.float64))
        if not samples:
            return pc.AMP_NORM_P_LO_DEFAULT, pc.AMP_NORM_P_HI_DEFAULT
        logs = np.log1p(np.concatenate(samples))
        p_lo, p_hi = np.percentile(logs, [2.0, 99.5])
        if p_hi <= p_lo:
            return pc.AMP_NORM_P_LO_DEFAULT, pc.AMP_NORM_P_HI_DEFAULT
        return float(p_lo), float(p_hi)
    except Exception:
        return pc.AMP_NORM_P_LO_DEFAULT, pc.AMP_NORM_P_HI_DEFAULT


def compute_checksum(data: bytes) -> int:
    """Byte-sum checksum per hytem-data-protocol-v1.0.9.md section 3.2: the
    sum of every byte from packetIdentifier through the end of content,
    truncated to 32 bits."""
    return sum(data) & 0xFFFFFFFF


def checksum_ok(packet: bytes, packet_size: int) -> bool:
    if packet_size < pc.PACKET_HEADER_SIZE + 4:
        return False
    try:
        flag = struct.unpack_from('<H', packet, pc.PACKET_FLAG_OFFSET)[0]
    except struct.error:
        return False
    if not (flag & pc.PACKET_FLAG_CHECKSUM_BIT):
        return True
    trailer = struct.unpack_from('<I', packet, packet_size - 4)[0]
    return compute_checksum(packet[:packet_size - 4]) == trailer


def parse_3101_body(header_bytes, body_and_data):
    if len(body_and_data) < pc.B3101_BODY_SIZE:
        return None
    try:
        sample_len = struct.unpack_from('<I', body_and_data, pc.B3101_SAMPLELEN_OFF)[0]
        n = int(sample_len)
        if n <= 0 or n > 200_000:
            return None
        need = 2 * n * 2
        if len(body_and_data) < pc.B3101_BODY_SIZE + need:
            return None
        syn = pc.B3101_SYNTIME_OFF
        year   = struct.unpack_from('<H', body_and_data, syn)[0]
        month  = body_and_data[syn + 2]
        day    = body_and_data[syn + 3]
        hour   = body_and_data[syn + 4]
        minute = body_and_data[syn + 5]
        second = body_and_data[syn + 6]
        ping_number    = struct.unpack_from('<I', body_and_data, pc.B3101_PINGNUM_OFF)[0]
        sonar_range_cm = struct.unpack_from('<I', body_and_data, pc.B3101_SONARRANGE_OFF)[0]
        center_freq_hz = struct.unpack_from('<I', body_and_data, pc.B3101_CENTERFREQ_OFF)[0]
        sample_rate    = struct.unpack_from('<f', body_and_data, pc.B3101_SAMPLERATE_OFF)[0]
        arr = np.frombuffer(body_and_data, dtype='<u2',
                            count=2 * n, offset=pc.B3101_BODY_SIZE)
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
    if len(packet) < pc.B3101_DATA_OFF:
        return None
    return parse_3101_body(packet[:pc.PACKET_HEADER_SIZE], packet[pc.PACKET_HEADER_SIZE:])
