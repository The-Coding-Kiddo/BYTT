import struct
from bytt.protocol import constants as pc
from bytt.protocol import commands
from bytt.protocol import packets

def test_build_command_107_is_256_bytes_with_valid_header():
    cmd = commands.SonarWorkCommand()
    packet = commands.build_command_107(cmd, identification=42)
    assert len(packet) == 256
    magic, header_size, version, size, flag, ident, total, num, ptype = \
        struct.unpack_from('<IHHIHHHHI', packet, 0)
    assert magic == pc.PACKET_IDENTIFIER
    assert header_size == pc.PACKET_HEADER_SIZE
    assert size == 256
    assert flag & pc.PACKET_FLAG_CHECKSUM_BIT
    assert ident == 42
    assert total == 1
    assert num == 1
    assert ptype == pc.PACKET_TYPE_107

def test_build_command_107_checksum_is_valid():
    cmd = commands.SonarWorkCommand()
    packet = commands.build_command_107(cmd)
    assert packets.checksum_ok(packet, len(packet)) is True

def test_build_command_107_encodes_sonar_run_flag():
    stopped = commands.build_command_107(commands.SonarWorkCommand(sonar_run=0))
    running = commands.build_command_107(commands.SonarWorkCommand(sonar_run=1))
    # sonar_run is the u32 right before the 104-byte reserved3 tail (content
    # ends at header(24) + 228 = 252, sonar_run is the 4 bytes before that)
    off = pc.PACKET_HEADER_SIZE + 228 - 104 - 4
    assert struct.unpack_from('<I', stopped, off)[0] == 0
    assert struct.unpack_from('<I', running, off)[0] == 1

def test_build_command_107_round_trips_gain_fields():
    cmd = commands.SonarWorkCommand(gain_hf=15, gain_lf=22)
    packet = commands.build_command_107(cmd)
    parsed = commands.parse_command_107(packet)
    assert parsed.gain_hf == 15
    assert parsed.gain_lf == 22
