"""Hytem wire-protocol and .bsf file-format constants."""
import struct

# ── Wire packet header (lines 150-163 of interactive_bsf_viewer.py) ───────────
PACKET_IDENTIFIER        = 0x004D5448
PACKET_IDENTIFIER_BYTES  = struct.pack('<I', PACKET_IDENTIFIER)
PACKET_HEADER_SIZE       = 24
PACKET_TYPE_OFFSET       = 20
PACKET_SIZE_OFFSET       = 8
PACKET_TYPE_3101         = 3101
PACKET_TYPE_166          = 166
PACKET_TYPE_107          = 107  # NEW — not in the original file, needed for CommandClient (Task 10)
PACKET_FLAG_OFFSET       = 12
PACKET_FRAME_ID_OFFSET   = 14
PACKET_TOTAL_OFFSET      = 16
PACKET_NUMBER_OFFSET     = 18
PACKET_FLAG_MULTI_BIT    = 0x0001
PACKET_FLAG_CHECKSUM_BIT = 0x0002
PACKET_CHECKSUM_NONE     = 0x77EEEE77
PACKET_VERSION           = 0x0109  # V1.0.9, per BYTT docs/vendor/hytem-data-protocol-v1.0.9.md

MULTI_PACKET_FRAME_TIMEOUT_S = 2.0

# ── 3101 side-scan body offsets, relative to body_and_data (lines 165-174) ────
B3101_SYNTIME_OFF    = 16
B3101_PINGNUM_OFF    = 28
B3101_SONARRANGE_OFF = 32
B3101_CENTERFREQ_OFF = 52
B3101_SPREADING_OFF  = 60
B3101_ABSORPTION_OFF = 64
B3101_SAMPLERATE_OFF = 72
B3101_SAMPLELEN_OFF  = 76
B3101_BODY_SIZE      = 104
B3101_DATA_OFF       = PACKET_HEADER_SIZE + B3101_BODY_SIZE

# ── .bsf file format (lines 108-118, 140-145) ──────────────────────────────────
BSF_MAGIC        = b'\x0fHSFV1.1.000'
BSF_FILE_HDR_SZ  = 0x400
BSF_RECORD_TYPE  = 0x00101702
SAMPLE_OFFSET    = 216
CH_SR_OFFSET     = 84
CH_SR_SIZE       = 56

NAV_RECORD_SIZE = 156
NAV_TS_OFFSET   = 64
NAV_LAT_OFFSET  = 80
NAV_LON_OFFSET  = 88
NAV_ALT_OFFSET  = 96
NAV_FIELD_FMT   = '<d'

# ── Amplitude normalization defaults (lines 136-137) ───────────────────────────
AMP_NORM_P_LO_DEFAULT = 12.4
AMP_NORM_P_HI_DEFAULT = 18.9
