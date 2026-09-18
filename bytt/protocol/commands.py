"""Builds Data Type Code 107 (SS-series side-scan sonar work control
command) packets, per BYTT docs/vendor/hytem-data-protocol-v1.0.9.md
section 4.1. 256 bytes total: 24-byte header + 228-byte content + 4-byte
checksum trailer."""
import struct
from dataclasses import dataclass
from bytt.protocol import constants as pc
from bytt.protocol.packets import compute_checksum

_HEADER_FMT = '<IHHIHHHHI'
_CONTENT_FMT = (
    '<'
    'IIIII' 'f' 'I' 'f' 'I' 'f' 'I' 'f' 'I' 'f'   # deviceType..towfshHead
    'III' 'HH' 'I' 'H' '2s' 'ff'                    # HF block
    'III' 'HH' 'I' 'H' '2s' 'ff'                    # LF block
    'I' '104s'                                       # sonarRun, reserved3
)
_CONTENT_SIZE = struct.calcsize(_CONTENT_FMT)
assert _CONTENT_SIZE == 228, _CONTENT_SIZE
_PACKET_SIZE = pc.PACKET_HEADER_SIZE + _CONTENT_SIZE + 4
assert _PACKET_SIZE == 256, _PACKET_SIZE


@dataclass
class SonarWorkCommand:
    """Defaults are the vendor-documented defaults from section 4.1."""
    device_type: int = 20            # SS3060
    work_mode: int = 0                # side-scan mode
    sync_mode: int = 0                # internal sync
    sync_polarity: int = 0            # positive pulse
    savs: int = 0                     # fixed value set by main control software
    sound_speed: float = 1500.0       # m/s
    ship_speed_source: int = 0        # manual
    ship_speed: float = 5.0           # knots
    towfish_depth_source: int = 1     # sonar
    towfish_depth: float = 0.0        # m
    towfish_height_source: int = 1    # sonar
    towfish_height: float = 0.0       # m
    towfish_head_source: int = 1      # sonar
    towfish_head: float = 0.0         # degrees
    ch_en_hf: int = 1
    sonar_range_hf: int = 50          # m
    signal_types_hf: int = 0          # CW
    pulse_width_hf: int = 100         # us
    pulse_source_level_hf: int = 220  # dB
    center_freq_hf: int = 600         # kHz
    gain_hf: int = 10                 # dB
    spreading_hf: float = 20.0        # dB
    absorption_hf: float = 60.0       # dB/km
    ch_en_lf: int = 1
    sonar_range_lf: int = 50          # m
    signal_types_lf: int = 0          # CW
    pulse_width_lf: int = 100         # us
    pulse_source_level_lf: int = 220  # dB
    center_freq_lf: int = 300         # kHz
    gain_lf: int = 10                 # dB
    spreading_lf: float = 20.0        # dB
    absorption_lf: float = 30.0       # dB/km
    sonar_run: int = 0                # 0: stopped, 1: running


def build_command_107(cmd: SonarWorkCommand, identification: int = 0) -> bytes:
    header = struct.pack(
        _HEADER_FMT,
        pc.PACKET_IDENTIFIER, pc.PACKET_HEADER_SIZE, pc.PACKET_VERSION,
        _PACKET_SIZE, pc.PACKET_FLAG_CHECKSUM_BIT, identification, 1, 1,
        pc.PACKET_TYPE_107,
    )
    content = struct.pack(
        _CONTENT_FMT,
        cmd.device_type, cmd.work_mode, cmd.sync_mode, cmd.sync_polarity, cmd.savs,
        cmd.sound_speed, cmd.ship_speed_source, cmd.ship_speed,
        cmd.towfish_depth_source, cmd.towfish_depth,
        cmd.towfish_height_source, cmd.towfish_height,
        cmd.towfish_head_source, cmd.towfish_head,
        cmd.ch_en_hf, cmd.sonar_range_hf, cmd.signal_types_hf,
        cmd.pulse_width_hf, cmd.pulse_source_level_hf, cmd.center_freq_hf,
        cmd.gain_hf, b'\x00\x00', cmd.spreading_hf, cmd.absorption_hf,
        cmd.ch_en_lf, cmd.sonar_range_lf, cmd.signal_types_lf,
        cmd.pulse_width_lf, cmd.pulse_source_level_lf, cmd.center_freq_lf,
        cmd.gain_lf, b'\x00\x00', cmd.spreading_lf, cmd.absorption_lf,
        cmd.sonar_run, b'\x00' * 104,
    )
    body = header + content
    trailer = struct.pack('<I', compute_checksum(body))
    return body + trailer


def parse_command_107(packet: bytes) -> SonarWorkCommand:
    """Inverse of build_command_107 — used by tests and by anything that
    needs to inspect a command packet it received (e.g. a hardware simulator)."""
    content = packet[pc.PACKET_HEADER_SIZE:pc.PACKET_HEADER_SIZE + _CONTENT_SIZE]
    fields = struct.unpack(_CONTENT_FMT, content)
    (device_type, work_mode, sync_mode, sync_polarity, savs, sound_speed,
     ship_speed_source, ship_speed, towfish_depth_source, towfish_depth,
     towfish_height_source, towfish_height, towfish_head_source, towfish_head,
     ch_en_hf, sonar_range_hf, signal_types_hf, pulse_width_hf,
     pulse_source_level_hf, center_freq_hf, gain_hf, _reserved1,
     spreading_hf, absorption_hf, ch_en_lf, sonar_range_lf, signal_types_lf,
     pulse_width_lf, pulse_source_level_lf, center_freq_lf, gain_lf,
     _reserved2, spreading_lf, absorption_lf, sonar_run, _reserved3) = fields
    return SonarWorkCommand(
        device_type=device_type, work_mode=work_mode, sync_mode=sync_mode,
        sync_polarity=sync_polarity, savs=savs, sound_speed=sound_speed,
        ship_speed_source=ship_speed_source, ship_speed=ship_speed,
        towfish_depth_source=towfish_depth_source, towfish_depth=towfish_depth,
        towfish_height_source=towfish_height_source, towfish_height=towfish_height,
        towfish_head_source=towfish_head_source, towfish_head=towfish_head,
        ch_en_hf=ch_en_hf, sonar_range_hf=sonar_range_hf,
        signal_types_hf=signal_types_hf, pulse_width_hf=pulse_width_hf,
        pulse_source_level_hf=pulse_source_level_hf, center_freq_hf=center_freq_hf,
        gain_hf=gain_hf, spreading_hf=spreading_hf, absorption_hf=absorption_hf,
        ch_en_lf=ch_en_lf, sonar_range_lf=sonar_range_lf,
        signal_types_lf=signal_types_lf, pulse_width_lf=pulse_width_lf,
        pulse_source_level_lf=pulse_source_level_lf, center_freq_lf=center_freq_lf,
        gain_lf=gain_lf, spreading_lf=spreading_lf, absorption_lf=absorption_lf,
        sonar_run=sonar_run,
    )
