"""Settings from ``config.toml`` plus environment overrides.

See ``config.example.toml`` for the file format. Environment variables win
over the file; the v1 names ``INFLUX_*``, ``HA_URL`` and ``HA_TOKEN`` keep
working.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class Settings:
    db_path: Path
    timezone: ZoneInfo
    config_path: Path | None = None  # None: no config file found
    sources: dict[str, bool] = field(default_factory=dict)
    devices: dict[str, str] = field(default_factory=dict)  # device id → name
    ignored_devices: frozenset[str] = frozenset()  # `"<id>" = false` in [devices]
    apps: dict[str, dict[str, Any]] = field(default_factory=dict)
    influx_url: str | None = None
    influx_token: str | None = None
    influx_org: str | None = None
    influx_bucket: str = "screentime"
    ha_url: str | None = None
    ha_token: str | None = None
    ha_entity_prefix: str = "screentime"  # sensor.<prefix>_total, …
    request_timeout: float = 30.0
    lookup_enabled: bool = True  # iTunes lookup for unknown bundle ids
    lookup_country: str | None = None  # App Store storefront; None: system region


def config_path() -> Path:
    return Path(
        os.getenv("SCREENTIME_CONFIG", "~/.config/screentime/config.toml")
    ).expanduser()


def default_db_path() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "screentime" / "screentime.db"


def system_timezone() -> str:
    """Return the system's IANA zone name, or ``UTC`` if it can't be resolved."""
    candidates = [os.getenv("TZ", "").lstrip(":")]
    try:
        # macOS and systemd both link /etc/localtime into a zoneinfo tree.
        candidates.append(os.readlink("/etc/localtime").partition("zoneinfo/")[2])
    except OSError:
        pass
    for name in candidates:
        try:
            if name:
                return ZoneInfo(name).key
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return "UTC"


def region_of(locale: str) -> str:
    """Two-letter region of a locale like ``de_DE``, ``en_US.UTF-8`` or ``en_US@rg=dezzzz``."""
    match = re.search(r"@rg=([a-z]{2})", locale, re.IGNORECASE) or re.search(
        r"_([A-Za-z]{2})(?![A-Za-z])", locale
    )
    return match.group(1).lower() if match else "us"


def system_country() -> str:
    """App Store storefront for the system region, ``us`` if unknown.

    macOS keeps the region in the AppleLocale default; LANG is only the
    shell's language and often stays ``en_US`` on a German Mac.
    """
    locale = ""
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["defaults", "read", "-g", "AppleLocale"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            locale = (getattr(result, "stdout", None) or "").strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return region_of(locale or os.getenv("LC_ALL") or os.getenv("LANG") or "")


def load_settings() -> Settings:
    path = config_path()
    raw: dict[str, Any] = {}
    if path.is_file():
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    influx = raw.get("influx", {})
    ha = raw.get("home_assistant", {})
    lookup = raw.get("lookup", {})

    def pick(env: str, section: dict[str, Any], key: str) -> str | None:
        value = os.getenv(env) or section.get(key)
        return str(value) if value else None

    return Settings(
        config_path=path if path.is_file() else None,
        db_path=Path(
            os.getenv("SCREENTIME_DB_PATH") or raw.get("db_path") or default_db_path()
        ).expanduser(),
        timezone=ZoneInfo(
            os.getenv("SCREENTIME_TIMEZONE") or raw.get("timezone") or system_timezone()
        ),
        sources={"biome": True, "knowledgec": True, **raw.get("sources", {})},
        devices={
            str(k): str(v) for k, v in raw.get("devices", {}).items() if v is not False
        },
        ignored_devices=frozenset(
            str(k) for k, v in raw.get("devices", {}).items() if v is False
        ),
        apps=raw.get("apps", {}),
        influx_url=(pick("INFLUX_URL", influx, "url") or "").rstrip("/") or None,
        influx_token=pick("INFLUX_TOKEN", influx, "token"),
        influx_org=pick("INFLUX_ORG", influx, "org"),
        influx_bucket=pick("INFLUX_BUCKET", influx, "bucket") or "screentime",
        ha_url=(pick("HA_URL", ha, "url") or "").rstrip("/") or None,
        ha_token=pick("HA_TOKEN", ha, "token"),
        lookup_enabled=bool(lookup.get("enabled", True)),
        lookup_country=str(lookup.get("country") or "").lower() or None,
        ha_entity_prefix=str(ha.get("entity_prefix") or "screentime"),
    )


def integration_complete(settings: Settings, target: str) -> bool:
    if target == "influx":
        return bool(
            settings.influx_url
            and settings.influx_token
            and settings.influx_org
            and settings.influx_bucket
        )
    if target == "ha":
        return bool(settings.ha_url and settings.ha_token)
    raise ValueError(target)


def _toml_key(key: str) -> str:
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, str):
        # JSON string escapes are a subset of TOML basic-string escapes.
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, date | time):
        return value.isoformat()
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError(f"cannot write {type(value).__name__} to TOML")


def dump_toml(data: dict[str, Any], _prefix: tuple[str, ...] = ()) -> str:
    """Minimal TOML writer for what ``tomllib`` returns (no arrays of tables).

    Comments of an existing file are not preserved.
    """
    lines = [
        f"{_toml_key(k)} = {_toml_value(v)}"
        for k, v in data.items()
        if not isinstance(v, dict)
    ]
    for key, value in data.items():
        if isinstance(value, dict):
            name = (*_prefix, key)
            header = ".".join(_toml_key(part) for part in name)
            lines += ["", f"[{header}]", dump_toml(value, name)]
    return "\n".join(lines).strip("\n")


def write_config(raw: dict[str, Any], path: Path) -> None:
    """Write ``raw`` as TOML to ``path``, readable by the owner only (0600)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)  # an existing file keeps its mode otherwise
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(
            "# Written by `screentime setup`. All keys: config.example.toml\n\n"
            + dump_toml(raw)
            + "\n"
        )
