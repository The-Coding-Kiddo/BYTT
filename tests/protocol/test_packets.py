import struct
import numpy as np
from bytt.protocol import constants as pc
from bytt.protocol import packets

def _make_3101_body(n_samples=4, ping_number=7, sonar_range_cm=5000,
                     center_freq_hz=400_000, sample_rate=96000.0):
    body = bytearray(pc.B3101_BODY_SIZE + 2 * n_samples * 2)
    struct.pack_into('<H', body, pc.B3101_SYNTIME_OFF, 2026)       # year
    body[pc.B3101_SYNTIME_OFF + 2] = 9                              # month
    body[pc.B3101_SYNTIME_OFF + 3] = 18                             # day
    body[pc.B3101_SYNTIME_OFF + 4] = 12                             # hour
    body[pc.B3101_SYNTIME_OFF + 5] = 30                             # minute
    body[pc.B3101_SYNTIME_OFF + 6] = 0                              # second
    struct.pack_into('<I', body, pc.B3101_PINGNUM_OFF, ping_number)
    struct.pack_into('<I', body, pc.B3101_SONARRANGE_OFF, sonar_range_cm)
    struct.pack_into('<I', body, pc.B3101_CENTERFREQ_OFF, center_freq_hz)
    struct.pack_into('<f', body, pc.B3101_SAMPLERATE_OFF, sample_rate)
    struct.pack_into('<I', body, pc.B3101_SAMPLELEN_OFF, n_samples)
    port_vals = np.arange(n_samples, dtype='<u2') * 100
    stbd_vals = np.arange(n_samples, dtype='<u2') * 200
    struct.pack_into(f'<{n_samples}H', body, pc.B3101_BODY_SIZE, *port_vals.tolist())
    struct.pack_into(f'<{n_samples}H', body, pc.B3101_BODY_SIZE + n_samples * 2, *stbd_vals.tolist())
    return bytes(body)

def test_parse_3101_body_round_trip():
    body = _make_3101_body(n_samples=4)
    result = packets.parse_3101_body(None, body)
    assert result is not None
    port, stbd, meta = result
    assert meta['ping_number'] == 7
    assert meta['max_range_m'] == 50.0
    assert meta['freq_kHz'] == 400.0
    assert meta['n_samples'] == 4
    assert port.shape == (4,)
    assert stbd.shape == (4,)

def test_parse_3101_body_rejects_short_input():
    assert packets.parse_3101_body(None, b'\x00' * 10) is None

def test_compute_checksum_and_checksum_ok_round_trip():
    header = struct.pack('<IHHIHHHHI', pc.PACKET_IDENTIFIER, pc.PACKET_HEADER_SIZE,
                          pc.PACKET_VERSION, 0, pc.PACKET_FLAG_CHECKSUM_BIT, 0, 1, 1,
                          pc.PACKET_TYPE_166)
    content = b'\x01'  # sonarState = data+cmd connected
    packet_size = len(header) + len(content) + 4
    header = header[:pc.PACKET_SIZE_OFFSET] + struct.pack('<I', packet_size) + \
             header[pc.PACKET_SIZE_OFFSET + 4:]
    body_no_trailer = header + content
    trailer = packets.compute_checksum(body_no_trailer)
    packet = body_no_trailer + struct.pack('<I', trailer)
    assert packets.checksum_ok(packet, packet_size) is True
    corrupted = packet[:-1] + bytes([packet[-1] ^ 0xFF])
    assert packets.checksum_ok(corrupted, packet_size) is False

def test_extract_nav_fix_rejects_out_of_range():
    nav = bytearray(pc.NAV_LON_OFFSET + 8)
    struct.pack_into(pc.NAV_FIELD_FMT, nav, pc.NAV_LAT_OFFSET, 999.0)
    struct.pack_into(pc.NAV_FIELD_FMT, nav, pc.NAV_LON_OFFSET, 10.0)
    assert packets.extract_nav_fix(bytes(nav)) is None

def test_extract_raw_channels_rejects_absurd_half_samples():
    import time
    # Build a minimal ping buffer with an absurd half_samples value in
    # channel 0's field, and no actual sample data behind it. A naive
    # implementation would try to np.zeros(2 * n) allocate ~34GB here.
    ping = bytearray(pc.SAMPLE_OFFSET)
    off = pc.CH_SR_OFFSET + 0 * pc.CH_SR_SIZE
    struct.pack_into('<I', ping, off + 52, 0xFFFFFFFF)  # half_samples
    start = time.monotonic()
    port, stbd = packets.extract_raw_channels(bytes(ping))
    elapsed = time.monotonic() - start
    assert elapsed < 1.0
    assert port.size == 0
    assert stbd.size == 0


def test_extract_nav_fix_accepts_valid_fix():
    nav = bytearray(pc.NAV_LON_OFFSET + 8)
    struct.pack_into(pc.NAV_FIELD_FMT, nav, pc.NAV_LAT_OFFSET, 41.3)
    struct.pack_into(pc.NAV_FIELD_FMT, nav, pc.NAV_LON_OFFSET, 36.3)
    result = packets.extract_nav_fix(bytes(nav))
    assert result == (41.3, 36.3, 0.0)
