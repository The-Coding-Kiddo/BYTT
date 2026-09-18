from bytt.protocol import constants as pc

def test_packet_header_layout():
    assert pc.PACKET_IDENTIFIER == 0x004D5448
    assert pc.PACKET_HEADER_SIZE == 24
    assert pc.PACKET_TYPE_OFFSET == 20
    assert pc.PACKET_SIZE_OFFSET == 8
    assert pc.PACKET_TYPE_3101 == 3101
    assert pc.PACKET_TYPE_166 == 166
    assert pc.PACKET_TYPE_107 == 107

def test_3101_body_offsets():
    assert pc.B3101_BODY_SIZE == 104
    assert pc.B3101_DATA_OFF == pc.PACKET_HEADER_SIZE + pc.B3101_BODY_SIZE

def test_bsf_file_constants():
    assert pc.BSF_MAGIC == b'\x0fHSFV1.1.000'
    assert pc.BSF_FILE_HDR_SZ == 0x400
    assert pc.SAMPLE_OFFSET == 216
