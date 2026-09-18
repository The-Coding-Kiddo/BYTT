"""Reads .bsf recording files: file header fields and the byte-offset index
of every ping/nav record in the file."""
import struct
from bytt.protocol import constants as pc


def read_file_header(data: bytes) -> dict:
    return {
        'sound_speed':  struct.unpack_from('<f', data, 0x10)[0],
        'device_model': data[0x60:0x70].rstrip(b'\x00').decode('ascii', errors='replace'),
        'sonar_serial': data[0x70:0x80].rstrip(b'\x00').decode('ascii', errors='replace'),
        'longitude':    struct.unpack_from('<d', data, 0x38)[0],
        'latitude':     struct.unpack_from('<d', data, 0x40)[0],
    }


def load_all_pings(data: bytes, progress_cb=None):
    """Scans the file once, returning:
      pings:       [(byte_offset, record_size), ...]
      nav_records: [(byte_offset, ping_index_before_it), ...]
    progress_cb, if given, is called as progress_cb(bytes_scanned, total_bytes)
    every 500 pings found."""
    pings       = []
    nav_records = []
    pos     = pc.BSF_FILE_HDR_SZ
    file_sz = len(data)
    report_every = 500
    while pos + 16 <= file_sz:
        rt = struct.unpack_from('<I', data, pos)[0]
        if rt == pc.BSF_RECORD_TYPE:
            rs = struct.unpack_from('<I', data, pos + 12)[0]
            if rs > 16 and pos + rs <= file_sz:
                if rs >= pc.SAMPLE_OFFSET:
                    pings.append((pos, rs))
                    if progress_cb and len(pings) % report_every == 0:
                        progress_cb(pos, file_sz)
                elif rs == pc.NAV_RECORD_SIZE:
                    nav_records.append((pos, len(pings) - 1))
                pos += rs
            else:
                break
        else:
            pos += 1
    return pings, nav_records
