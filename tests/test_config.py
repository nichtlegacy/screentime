from pathlib import Path

from datetime import date
import stat
import tomllib

from screentime.config import dump_toml, load_settings, write_config


def test_file_values_and_env_overrides(tmp_path: Path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(
        """
timezone = "America/New_York"
db_path = "~/st.db"
[sources]
knowledgec = false
[devices]
"device-1" = "Work iPhone"
"device-2" = false
[apps."com.example.app"]
category = "Productivity"
[influx]
url = "http://influx.test:8086/"
token = "file-token"
org = "home"
[lookup]
enabled = false
country = "DE"
[home_assistant]
url = "http://ha.test"
token = "ha-token"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("SCREENTIME_CONFIG", str(path))
    monkeypatch.setenv("INFLUX_TOKEN", "env-token")
    monkeypatch.setenv("SCREENTIME_TIMEZONE", "Europe/Berlin")

    settings = load_settings()

    assert settings.config_path == path
    assert settings.timezone.key == "Europe/Berlin"
    assert settings.db_path == Path.home() / "st.db"
    assert settings.sources == {"biome": True, "knowledgec": False}
    assert settings.devices == {"device-1": "Work iPhone"}
    assert settings.ignored_devices == {"device-2"}
    assert settings.apps == {"com.example.app": {"category": "Productivity"}}
    assert settings.influx_url == "http://influx.test:8086"
    assert settings.influx_token == "env-token"
    assert settings.influx_bucket == "screentime"
    assert (settings.ha_url, settings.ha_token) == ("http://ha.test", "ha-token")
    assert (settings.lookup_enabled, settings.lookup_country) == (False, "de")


def test_defaults_without_config_file(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("sys.platform", "linux")

    settings = load_settings()

    assert settings.config_path is None
    assert settings.db_path == tmp_path / "screentime" / "screentime.db"
    assert settings.sources == {"biome": True, "knowledgec": True}
    assert settings.influx_url is None and settings.ha_url is None
    assert settings.timezone.key  # resolved system zone or UTC


def test_toml_round_trip_and_permissions(tmp_path: Path):
    raw = {
        "timezone": "Europe/Berlin",
        "custom": {"since": date(2026, 1, 2), "tags": ["a", 'q"uote'], "n": 1.5},
        "devices": {"00000000-0000": "Alex’s iPhone\n", "mac:1": False},
        "apps": {"com.example.app": {"name": "Example", "category": "Social"}},
        "empty": {},
    }
    assert tomllib.loads(dump_toml(raw)) == raw
    path = tmp_path / "sub" / "config.toml"
    path.parent.mkdir()
    path.write_text("old")
    path.chmod(0o644)
    write_config(raw, path)
    assert tomllib.loads(path.read_text()) == raw
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
