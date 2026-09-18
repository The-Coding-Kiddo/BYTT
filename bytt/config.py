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
    }
    with open(path, "w") as f:
        parser.write(f)
