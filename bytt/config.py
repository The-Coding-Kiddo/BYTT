"""Loads/saves the app's connection config: [Sonar] TowfishIP, CmdPort,
DataPort, Source (playback|towfish). See BYTT docs/HydroWaterDemo.ini.example
for the original's shape -- this is the trimmed, SatCenter-free version."""
import configparser
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".bytt" / "bytt.ini"


@dataclass
class AppConfig:
    towfish_ip: str = "192.168.1.16"
    cmd_port: int = 16128
    data_port: int = 16129
    source: str = "playback"  # "playback" | "towfish"
    pc_ip: str = ""  # this PC's own address; "" means "don't check the segment"


def same_segment(a: str, b: str) -> bool:
    """True when two IPv4 addresses share a /24 prefix. The towfish manual
    requires both ends on the same segment; violating it produces a bare
    connection timeout with no clue why, so we check and say so instead."""
    a_parts = a.split(".")
    b_parts = b.split(".")
    if len(a_parts) != 4 or len(b_parts) != 4:
        return False
    try:
        a_octets = [int(p) for p in a_parts]
        b_octets = [int(p) for p in b_parts]
    except ValueError:
        return False
    if any(not (0 <= o <= 255) for o in a_octets + b_octets):
        return False
    return a_octets[:3] == b_octets[:3]


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    path = Path(path)
    if not path.exists():
        return AppConfig()
    parser = configparser.ConfigParser()
    parser.read(path)
    if "Sonar" not in parser:
        return AppConfig()
    section = parser["Sonar"]
    return AppConfig(
        towfish_ip=section.get("TowfishIP", AppConfig.towfish_ip),
        cmd_port=section.getint("CmdPort", AppConfig.cmd_port),
        data_port=section.getint("DataPort", AppConfig.data_port),
        source=section.get("Source", AppConfig.source),
        pc_ip=section.get("PcIP", AppConfig.pc_ip),
    )


def save_config(config: AppConfig, path: Path = DEFAULT_CONFIG_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    parser = configparser.ConfigParser()
    parser["Sonar"] = {
        "TowfishIP": config.towfish_ip,
        "CmdPort": str(config.cmd_port),
        "DataPort": str(config.data_port),
        "Source": config.source,
        "PcIP": config.pc_ip,
    }
    with open(path, "w") as f:
        parser.write(f)
