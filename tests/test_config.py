import configparser
from pathlib import Path
from bytt.config import AppConfig, load_config, save_config, same_segment

def test_same_segment_matching_24():
    assert same_segment("192.168.1.16", "192.168.1.99") is True

def test_same_segment_different_24():
    assert same_segment("192.168.1.16", "192.168.56.1") is False

def test_same_segment_rejects_malformed_input():
    assert same_segment("not-an-ip", "192.168.1.1") is False
    assert same_segment("192.168.1.1", "") is False
    assert same_segment("999.1.1.1", "192.168.1.1") is False

def test_pc_ip_round_trips_through_config(tmp_path):
    path = tmp_path / "bytt.ini"
    original = AppConfig(pc_ip="192.168.1.50")
    save_config(original, path)
    assert load_config(path).pc_ip == "192.168.1.50"

def test_load_config_missing_file_returns_defaults(tmp_path):
    cfg = load_config(tmp_path / "nope.ini")
    assert cfg == AppConfig(towfish_ip="192.168.1.16", cmd_port=16128,
                             data_port=16129, source="playback")

def test_save_then_load_round_trip(tmp_path):
    path = tmp_path / "bytt.ini"
    original = AppConfig(towfish_ip="192.168.1.99", cmd_port=1, data_port=2, source="towfish")
    save_config(original, path)
    loaded = load_config(path)
    assert loaded == original

def test_load_config_missing_sonar_section_returns_defaults(tmp_path):
    path = tmp_path / "no_sonar.ini"
    parser = configparser.ConfigParser()
    parser["Other"] = {"Foo": "bar"}
    with open(path, "w") as f:
        parser.write(f)
    cfg = load_config(path)
    assert cfg == AppConfig()


def test_save_config_writes_expected_ini_shape(tmp_path):
    path = tmp_path / "bytt.ini"
    save_config(AppConfig(towfish_ip="10.0.0.1", cmd_port=16128, data_port=16129,
                           source="towfish"), path)
    parser = configparser.ConfigParser()
    parser.read(path)
    assert parser["Sonar"]["TowfishIP"] == "10.0.0.1"
    assert parser["Sonar"]["Source"] == "towfish"
    assert set(parser["Sonar"].keys()) == {"towfiship", "cmdport", "dataport", "source", "pcip"}
