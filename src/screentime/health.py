"""`status` and `doctor`: read-only views of config, sources and the database."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import json
from pathlib import Path
import plistlib
import re
import sqlite3
import sys
from typing import Any

import requests

from . import launchd
from .config import Settings, integration_complete
from .db import connect, get_state, parse_timestamp
from .importer import BIOME_STREAM, KNOWLEDGEC_DB

FDA_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
STALE_DAYS = 3


def _denied(path: Path) -> bool:
    """True if macOS refuses to open ``path`` (no Full Disk Access).

    A missing store is not denied: Macs without an iPhone have no Biome stream.
    """
    try:
        if path.is_dir():
            next(path.iterdir(), None)
        else:
            path.open("rb").close()
    except PermissionError:
        return True
    except OSError:
        pass
    return False


def denied_sources(settings: Settings) -> list[Path]:
    """Enabled source stores this process may not open (no Full Disk Access)."""
    return [
        path
        for name, path in (("biome", BIOME_STREAM), ("knowledgec", KNOWLEDGEC_DB))
        if settings.sources.get(name, True) and _denied(path)
    ]


def check_influx(settings: Settings) -> str | None:
    """None if the token may write to the bucket, else what is wrong."""
    try:
        # An empty write checks url, org, bucket and token without writing.
        response = requests.post(
            f"{settings.influx_url}/api/v2/write",
            params={"org": settings.influx_org, "bucket": settings.influx_bucket},
            headers={"Authorization": f"Token {settings.influx_token}"},
            data=b"",
            timeout=10,
        )
    except requests.RequestException as exc:
        return f"cannot reach {settings.influx_url}: {exc}"
    if response.status_code == 204:
        return None
    try:
        message = response.json()["message"]
    except (ValueError, KeyError, TypeError):
        message = response.text[:200]
    return f"HTTP {response.status_code}: {message}"


def check_ha(settings: Settings) -> str | None:
    """None if Home Assistant accepts the token, else what is wrong."""
    try:
        response = requests.get(
            f"{settings.ha_url}/api/",
            headers={"Authorization": f"Bearer {settings.ha_token}"},
            timeout=10,
        )
    except requests.RequestException as exc:
        return f"cannot reach {settings.ha_url}: {exc}"
    if response.status_code == 401:
        return "token rejected (HTTP 401)"
    return None if response.status_code == 200 else f"HTTP {response.status_code}"


def _db_facts(conn: sqlite3.Connection) -> dict[str, Any]:
    devices = [
        dict(row)
        for row in conn.execute(
            """SELECT d.device_id, d.name, d.platform, d.source,
              COUNT(e.event_key) AS events,
              MAX(e.started_at_utc) AS last_event_at
            FROM devices d LEFT JOIN screen_events e ON e.device_id = d.device_id
            GROUP BY d.device_id ORDER BY d.name"""
        )
    ]
    run = conn.execute(
        """SELECT run_type, status, started_at, finished_at, details_json, error
        FROM runs ORDER BY id DESC LIMIT 1"""
    ).fetchone()
    last_run = dict(run) if run else None
    if last_run:
        details = last_run.pop("details_json")
        last_run["details"] = json.loads(details) if details else None
    return {"devices": devices, "last_run": last_run}


def status(settings: Settings) -> dict[str, Any]:
    result: dict[str, Any] = {
        "config": str(settings.config_path) if settings.config_path else None,
        "database": str(settings.db_path),
        "timezone": settings.timezone.key,
        "sources": [name for name, on in settings.sources.items() if on],
        "influx": integration_complete(settings, "influx"),
        "home_assistant": integration_complete(settings, "ha"),
    }
    if not settings.db_path.exists():
        return {**result, "devices": [], "last_run": None}
    conn = connect(settings.db_path, read_only=True)
    try:
        return {
            **result,
            **_db_facts(conn),
            "last_influx_success_at": get_state(conn, "last_influx_success_at"),
            "last_ha_success_at": get_state(conn, "last_ha_success_at"),
        }
    finally:
        conn.close()


def _age(timestamp: str) -> float:
    """Seconds since an ISO timestamp."""
    return (datetime.now(UTC) - parse_timestamp(timestamp)).total_seconds()


def doctor(settings: Settings, *, online: bool = False) -> dict[str, Any]:
    checks: list[dict[str, str]] = []

    def add(name: str, state: str, detail: str) -> None:
        checks.append({"name": name, "status": state, "detail": detail})

    add(
        "config",
        "ok" if settings.config_path else "warn",
        str(settings.config_path)
        if settings.config_path
        else "no config file, using defaults; run `screentime setup`",
    )
    if not re.fullmatch(r"[a-z0-9_]+", settings.ha_entity_prefix):
        add(
            "home_assistant",
            "failed",
            f"entity_prefix {settings.ha_entity_prefix!r} may only contain a-z, 0-9, _",
        )
    for name, target, check, configured in (
        (
            "influx",
            "influx",
            check_influx,
            settings.influx_url or settings.influx_token,
        ),
        ("home_assistant", "ha", check_ha, settings.ha_url or settings.ha_token),
    ):
        if not integration_complete(settings, target):
            if configured:
                add(name, "warn", "incomplete config, export disabled")
        elif online:
            error = check(settings)
            add(name, "failed" if error else "ok", error or "reachable")
        else:
            add(name, "ok", "configured (`doctor --online` tests the connection)")
    denied = denied_sources(settings)
    if not denied:
        add("full_disk_access", "ok", "Screen Time stores readable")
    for path in denied:
        add(
            "full_disk_access",
            "failed",
            f"cannot read {path}. Add your terminal app in System Settings → "
            "Privacy & Security → Full Disk Access, then restart it",
        )
    if sys.platform == "darwin":
        _agent_checks(add)
    if not settings.db_path.exists():
        add(
            "database",
            "warn",
            f"{settings.db_path} not created yet; run `screentime sync`",
        )
    else:
        add("database", "ok", str(settings.db_path))
        conn = connect(settings.db_path, read_only=True)
        try:
            facts = _db_facts(conn)
        finally:
            conn.close()
        for device in facts["devices"]:
            if device["device_id"] in settings.ignored_devices:
                continue
            last = device["last_event_at"]
            stale = last is None or _age(last) > STALE_DAYS * 86400
            hint = ""
            if last is None:
                # Biome keeps a directory for every iPhone ever signed in.
                hint = (
                    " (an old device? Ignore it with `-` in `screentime setup` "
                    f'or `"{device["device_id"]}" = false` in [devices])'
                )
            elif stale and device["source"] == "biome":
                hint = (
                    " (no new usage for days: device unused, or iCloud sync of "
                    "Screen Time (Share Across Devices) is off)"
                )
            add(
                f"device {device['name']}",
                "warn" if stale else "ok",
                f"{device['platform']}, last event {last or 'never'}{hint}",
            )
        _last_run_check(add, facts["last_run"])
    states = {item["status"] for item in checks}
    overall = (
        "failed" if "failed" in states else "partial" if "warn" in states else "success"
    )
    return {"status": overall, "checks": checks}


def _agent_checks(add: Callable[[str, str, str], None]) -> None:
    path = launchd.plist_path()
    if not path.exists():
        add("agent", "warn", "not installed; run `screentime setup`")
        return
    executable = plistlib.loads(path.read_bytes())["ProgramArguments"][0]
    if not Path(executable).exists():
        add("agent", "failed", f"{executable} is gone; re-run `screentime setup`")
    elif not launchd.loaded():
        add(
            "agent",
            "failed",
            f"installed but not loaded; run `screentime setup` or "
            f"`launchctl bootstrap gui/$(id -u) '{path}'`",
        )
    else:
        add("agent", "ok", f"{launchd.LABEL} runs {executable} every 15 min")


def _last_run_check(
    add: Callable[[str, str, str], None], run: dict[str, Any] | None
) -> None:
    if run is None:
        add("last_run", "warn", "none recorded; run `screentime run`")
        return
    minutes = round(_age(run["started_at"]) / 60)
    detail = f"{run['status']} {minutes} min ago"
    state = "ok" if run["status"] == "success" else "warn"
    if run["error"]:
        detail += f" ({run['error']})"
        if "Operation not permitted" in run["error"]:
            detail += (
                ". The agent lacks Full Disk Access: add "
                f"{launchd.fda_binary()} in System Settings → Privacy & "
                "Security → Full Disk Access"
            )
        if "No route to host" in run["error"]:
            detail += (
                ". macOS blocks the agent from the local network: allow "
                f"{Path(launchd.fda_binary()).name} in System Settings → Privacy & "
                "Security → Local Network (macOS asks on the agent's first "
                "connection)"
            )
    if minutes > 3 * launchd.INTERVAL_S / 60 and launchd.plist_path().exists():
        state = "warn"
        detail += f"; the agent should run every 15 min, see {launchd.log_dir()}/"
    add("last_run", state, detail)
