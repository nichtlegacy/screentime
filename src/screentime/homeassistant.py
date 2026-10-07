"""Home Assistant sink: today's numbers as REST states (``POST /api/states``).

The sensor set is documented in ``docs/home-assistant.md``.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
import re
import sqlite3
from typing import Any
import unicodedata

import requests

from .config import Settings, integration_complete
from .db import now_utc_iso, parse_timestamp, set_state, transaction

ICONS = {"iphone": "mdi:cellphone", "ipad": "mdi:tablet", "mac": "mdi:laptop"}
# Suffixes of the fixed sensors; a device slug must not shadow them.
RESERVED = {"total", "week", "top_app", "top_apps", "by_category", "last_sync"}


def slug(name: str) -> str:
    """``"Alex’s iPhone"`` → ``alexs_iphone``, close to Home Assistant's slugify."""
    text = unicodedata.normalize("NFKD", name.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"['’`]", "", text)
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _minutes(seconds: float) -> float:
    return round(seconds / 60, 1)


def _local(value: str | None, settings: Settings) -> str | None:
    return (
        parse_timestamp(value).astimezone(settings.timezone).isoformat()
        if value
        else None
    )


def _duration(
    name: str, icon: str, seconds: float, last_reset: datetime, **attributes: Any
) -> dict[str, Any]:
    # `total` + `last_reset` tells the recorder exactly when the counter
    # restarts, so long-term statistics see daily (weekly) sums, not dips.
    return {
        "state": _minutes(seconds),
        "attributes": {
            "friendly_name": name,
            "icon": icon,
            "unit_of_measurement": "min",
            "device_class": "duration",
            "state_class": "total",
            "last_reset": last_reset.isoformat(),
            **attributes,
        },
    }


def ha_states(conn: sqlite3.Connection, settings: Settings) -> dict[str, dict]:
    """Entity id → ``{"state", "attributes"}`` for the local day of ``now``."""
    tz = settings.timezone
    now = datetime.now(tz)
    today = now.date()
    midnight = datetime.combine(today, time(0), tz)
    week_start = today - timedelta(days=today.weekday())
    # Devices active in the last 7 days keep a sensor (0 when idle today).
    rows = conn.execute(
        """SELECT s.*, COALESCE(d.name, s.device_id) AS name, d.platform
        FROM daily_device_stats s LEFT JOIN devices d ON d.device_id = s.device_id
        WHERE s.day BETWEEN ? AND ? ORDER BY s.device_id, s.day""",
        ((today - timedelta(days=6)).isoformat(), today.isoformat()),
    ).fetchall()
    today_rows = [r for r in rows if r["day"] == today.isoformat()]
    apps = conn.execute(
        """SELECT title, bundle_id, category, SUM(duration_s) AS duration_s
        FROM daily_app_stats WHERE day = ?
        GROUP BY bundle_id ORDER BY duration_s DESC LIMIT 10""",
        (today.isoformat(),),
    ).fetchall()
    categories = conn.execute(
        """SELECT category, SUM(duration_s) AS duration_s FROM daily_category_stats
        WHERE day = ? GROUP BY category ORDER BY duration_s DESC""",
        (today.isoformat(),),
    ).fetchall()

    def total(key: str) -> float:
        return sum(r[key] for r in today_rows)

    week = sum(r["duration_s"] for r in rows if r["day"] >= week_start.isoformat())
    firsts = [r["first_activity_at"] for r in today_rows if r["first_activity_at"]]
    lasts = [r["last_activity_at"] for r in today_rows if r["last_activity_at"]]
    top = [
        {
            "name": a["title"],
            "bundle_id": a["bundle_id"],
            "category": a["category"],
            "minutes": _minutes(a["duration_s"]),
        }
        for a in apps
    ]
    p = f"sensor.{settings.ha_entity_prefix}"
    states: dict[str, dict] = {
        f"{p}_total": _duration(
            "Screen Time Total",
            "mdi:cellphone-screen",
            total("duration_s"),
            midnight,
            late_night_minutes=_minutes(total("late_duration_s")),
            deep_night_minutes=_minutes(total("deep_night_duration_s")),
            first_activity=_local(min(firsts, default=None), settings),
            last_activity=_local(max(lasts, default=None), settings),
        ),
        f"{p}_week": _duration(
            "Screen Time This Week",
            "mdi:calendar-week",
            week,
            datetime.combine(week_start, time(0), tz),
            average_minutes=_minutes(week / (today.weekday() + 1)),
            week_start=week_start.isoformat(),
        ),
        f"{p}_top_app": {
            "state": top[0]["name"] if top else "none",
            "attributes": {
                "friendly_name": "Top App Today",
                "icon": "mdi:trophy",
                "minutes": top[0]["minutes"] if top else 0,
                "bundle_id": top[0]["bundle_id"] if top else None,
                "category": top[0]["category"] if top else None,
                "apps": top,
            },
        },
        # v1 format: one attribute per app name.
        f"{p}_top_apps": {
            "state": len(top),
            "attributes": {
                "friendly_name": "Screen Time Top Apps",
                "icon": "mdi:format-list-numbered",
                "unit_of_measurement": "apps",
                **{a["name"]: a["minutes"] for a in top},
            },
        },
        # v1 format: `category_<Name>` attributes.
        f"{p}_by_category": {
            "state": categories[0]["category"] if categories else "none",
            "attributes": {
                "friendly_name": "Screen Time by Category",
                "icon": "mdi:chart-pie",
                **{
                    f"category_{c['category']}": _minutes(c["duration_s"])
                    for c in categories
                },
            },
        },
        f"{p}_last_sync": {
            "state": now_utc_iso(),
            "attributes": {
                "friendly_name": "Screen Time Last Sync",
                "icon": "mdi:cloud-sync",
                "device_class": "timestamp",
            },
        },
    }
    seen = set(RESERVED)
    for device_id in dict.fromkeys(r["device_id"] for r in rows):
        latest = [r for r in rows if r["device_id"] == device_id][-1]
        row = latest if latest["day"] == today.isoformat() else None
        name = latest["name"]
        base = slug(name) or "device"
        suffix, n = base, 1
        while suffix in seen:
            n += 1
            suffix = f"{base}_{n}"
        seen.add(suffix)
        icon = ICONS.get(latest["platform"] or "", "mdi:monitor")
        states[f"{p}_{suffix}"] = _duration(
            f"Screen Time {name}",
            icon,
            row["duration_s"] if row else 0,
            midnight,
            platform=latest["platform"],
            first_activity=_local(row["first_activity_at"] if row else None, settings),
            last_activity=_local(row["last_activity_at"] if row else None, settings),
        )
        states[f"{p}_{suffix}_late_night"] = _duration(
            f"Screen Time {name} Late Night",
            "mdi:weather-night",
            row["late_duration_s"] if row else 0,
            midnight,
        )
        states[f"{p}_{suffix}_deep_night"] = _duration(
            f"Screen Time {name} Deep Night",
            "mdi:sleep",
            row["deep_night_duration_s"] if row else 0,
            midnight,
        )
    return states


def export_home_assistant(conn: sqlite3.Connection, settings: Settings) -> int:
    if not integration_complete(settings, "ha"):
        return 0
    states = ha_states(conn, settings)
    for entity_id, payload in states.items():
        response = requests.post(
            f"{settings.ha_url}/api/states/{entity_id}",
            headers={"Authorization": f"Bearer {settings.ha_token}"},
            json=payload,
            timeout=settings.request_timeout,
        )
        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"Home Assistant POST {entity_id} failed with HTTP {response.status_code}"
            )
    with transaction(conn):
        set_state(conn, "last_ha_success_at", now_utc_iso())
    return len(states)
