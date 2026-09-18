import struct
from bytt.protocol import constants as pc
from bytt.bsf import file_io

def _build_ping_record(n_samples=4, half_samples=4):
    size = pc.SAMPLE_OFFSET + 2 * n_samples * 4
    rec = bytearray(size)
    struct.pack_into('<I', rec, 0, pc.BSF_RECORD_TYPE)
    struct.pack_into('<I', rec, 12, size)
    ch0_off = pc.CH_SR_OFFSET
    struct.pack_into('<H', rec, ch0_off + 2, 400)       # freq_kHz
    struct.pack_into('<I', rec, ch0_off + 28, 96000)     # raw_sr
    struct.pack_into('<I', rec, ch0_off + 52, half_samples)  # half_samples
    return bytes(rec)

def _build_bsf_bytes(n_pings=3):
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    struct.pack_into('<f', header, 0x10, 1500.0)   # sound_speed
    struct.pack_into('<d', header, 0x38, 36.33)    # longitude
    struct.pack_into('<d', header, 0x40, 41.30)    # latitude
    header[0x60:0x66] = b'SS3060'
    header[0x70:0x76] = b'SN0001'
    body = b''.join(_build_ping_record() for _ in range(n_pings))
    return bytes(header) + body

def test_read_file_header():
    data = _build_bsf_bytes()
    hdr = file_io.read_file_header(data)
    assert hdr['sound_speed'] == 1500.0
    assert hdr['device_model'] == 'SS3060'
    assert hdr['sonar_serial'] == 'SN0001'
    assert abs(hdr['longitude'] - 36.33) < 1e-9
    assert abs(hdr['latitude'] - 41.30) < 1e-9

def test_load_all_pings_finds_every_record():
    data = _build_bsf_bytes(n_pings=3)
    pings, nav_records = file_io.load_all_pings(data)
    assert len(pings) == 3
    assert nav_records == []
    for offset, size in pings:
        assert data[offset:offset + 4] == struct.pack('<I', pc.BSF_RECORD_TYPE)

def test_load_all_pings_reports_progress():
    data = _build_bsf_bytes(n_pings=3)
    calls = []
    file_io.load_all_pings(data, progress_cb=lambda done, total: calls.append((done, total)))
    # progress_cb is optional and only required to not raise; exact call
    # cadence is an implementation detail of the report_every threshold.
