"""Read-only queries shared by `summary`, `dump` and the MCP server.

Days are local calendar dates (``YYYY-MM-DD``) as stored in the daily
aggregate tables, ranges are inclusive and durations are whole seconds.
Timestamps are returned as ISO 8601 in the configured timezone. ``device``
filters accept a device id or a display name (case-insensitive).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

from .db import connect, parse_timestamp

PERIODS = (
    "today",
    "yesterday",
    "this_week",
    "last_week",
    "last_7_days",
    "last_30_days",
)


def open_db(path: Path) -> sqlite3.Connection:
    if not Path(path).is_file():
        raise FileNotFoundError(f"no database at {path}; run `screentime sync` first")
    return connect(path, read_only=True)


def resolve_range(
    today: date,
    period: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> tuple[date, date]:
    """Explicit ``start``/``end`` win over ``period``; the default is the last 7 days.

    Weeks are ISO weeks (Monday to Sunday).
    """
    if start or end:
        end = end or today
        start = start or end - timedelta(days=6)
    else:
        monday = today - timedelta(days=today.weekday())
        ranges = {
            "today": (today, today),
            "yesterday": (today - timedelta(days=1),) * 2,
            "this_week": (monday, monday + timedelta(days=6)),
            "last_week": (monday - timedelta(days=7), monday - timedelta(days=1)),
            "last_7_days": (today - timedelta(days=6), today),
            "last_30_days": (today - timedelta(days=29), today),
        }
        if (period or "last_7_days") not in ranges:
            raise ValueError(
                f"unknown period {period!r}; use one of {', '.join(PERIODS)}"
            )
        start, end = ranges[period or "last_7_days"]
    if start > end:
        raise ValueError(f"start {start} is after end {end}")
    return start, end


def _device_filter(
    conn: sqlite3.Connection, device: str | None, column: str = "device_id"
) -> tuple[str, list[str]]:
    """SQL fragment (``AND column IN (...)``) restricting rows to ``device``."""
    if not device:
        return "", []
    ids = [
        row[0]
        for row in conn.execute(
            "SELECT device_id FROM devices WHERE device_id = ? OR name = ? COLLATE NOCASE",
            (device, device),
        )
    ]
    if not ids:
        names = [
            row[0] for row in conn.execute("SELECT name FROM devices ORDER BY name")
        ]
        raise LookupError(
            f"unknown device {device!r}; known devices: {', '.join(names) or 'none'}"
        )
    return f" AND {column} IN ({','.join('?' for _ in ids)})", ids


def _share(part: float, total: float) -> float:
    return round(100 * part / total, 1) if total else 0.0


def list_devices(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        {**dict(row), "total_s": round(row["total_s"])}
        for row in conn.execute(
            """SELECT d.device_id, d.name, d.platform, d.source,
              MIN(s.day) AS first_day, MAX(s.day) AS last_day,
              COALESCE(SUM(s.duration_s), 0) AS total_s
            FROM devices d LEFT JOIN daily_device_stats s ON s.device_id = d.device_id
            GROUP BY d.device_id ORDER BY total_s DESC, d.name"""
        )
    ]


def total_seconds(
    conn: sqlite3.Connection, start: date, end: date, device: str | None = None
) -> int:
    where, ids = _device_filter(conn, device)
    row = conn.execute(
        f"SELECT COALESCE(SUM(duration_s), 0) FROM daily_device_stats WHERE day BETWEEN ? AND ?{where}",
        [start.isoformat(), end.isoformat(), *ids],
    ).fetchone()
    return round(row[0])


def device_totals(
    conn: sqlite3.Connection, start: date, end: date, device: str | None = None
) -> list[dict[str, Any]]:
    where, ids = _device_filter(conn, device, "s.device_id")
    rows = conn.execute(
        f"""SELECT s.device_id, COALESCE(d.name, s.device_id) AS name,
          COALESCE(d.platform, 'unknown') AS platform, SUM(s.duration_s) AS total_s
        FROM daily_device_stats s LEFT JOIN devices d ON d.device_id = s.device_id
        WHERE s.day BETWEEN ? AND ?{where}
        GROUP BY s.device_id ORDER BY total_s DESC""",
        [start.isoformat(), end.isoformat(), *ids],
    ).fetchall()
    total = sum(row["total_s"] for row in rows)
    return [
        {
            **dict(row),
            "total_s": round(row["total_s"]),
            "share_pct": _share(row["total_s"], total),
        }
        for row in rows
    ]


def daily_usage(
    conn: sqlite3.Connection,
    tz: ZoneInfo,
    start: date,
    end: date,
    device: str | None = None,
) -> list[dict[str, Any]]:
    """One entry per day in the range (zero-filled), devices keyed by name."""
    where, ids = _device_filter(conn, device, "s.device_id")
    days: dict[str, dict[str, Any]] = {}
    for offset in range((end - start).days + 1):
        day = (start + timedelta(days=offset)).isoformat()
        days[day] = {
            "day": day,
            "total_s": 0.0,
            "late_s": 0.0,
            "deep_night_s": 0.0,
            "first_activity": None,
            "last_activity": None,
            "devices": {},
        }
    for row in conn.execute(
        f"""SELECT s.*, COALESCE(d.name, s.device_id) AS name
        FROM daily_device_stats s LEFT JOIN devices d ON d.device_id = s.device_id
        WHERE s.day BETWEEN ? AND ?{where}""",
        [start.isoformat(), end.isoformat(), *ids],
    ):
        item = days[row["day"]]
        item["total_s"] += row["duration_s"]
        item["late_s"] += row["late_duration_s"]
        item["deep_night_s"] += row["deep_night_duration_s"]
        item["devices"][row["name"]] = (
            item["devices"].get(row["name"], 0.0) + row["duration_s"]
        )
        for key, column, pick in (
            ("first_activity", "first_activity_at", min),
            ("last_activity", "last_activity_at", max),
        ):
            if row[column]:
                value = parse_timestamp(row[column])
                item[key] = pick(item[key], value) if item[key] else value
    for item in days.values():
        for key in ("total_s", "late_s", "deep_night_s"):
            item[key] = round(item[key])
        item["devices"] = {name: round(s) for name, s in item["devices"].items()}
        for key in ("first_activity", "last_activity"):
            item[key] = item[key] and item[key].astimezone(tz).isoformat()
    return list(days.values())


def top_apps(
    conn: sqlite3.Connection,
    start: date,
    end: date,
    device: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    where, ids = _device_filter(conn, device)
    total = total_seconds(conn, start, end, device)
    return [
        {
            **dict(row),
            "total_s": round(row["total_s"]),
            "share_pct": _share(row["total_s"], total),
        }
        for row in conn.execute(
            f"""SELECT bundle_id, MAX(title) AS name, MAX(category) AS category,
              SUM(duration_s) AS total_s, SUM(event_count) AS sessions
            FROM daily_app_stats WHERE day BETWEEN ? AND ?{where}
            GROUP BY bundle_id ORDER BY total_s DESC, bundle_id LIMIT ?""",
            [start.isoformat(), end.isoformat(), *ids, limit],
        )
    ]


def categories(
    conn: sqlite3.Connection, start: date, end: date, device: str | None = None
) -> list[dict[str, Any]]:
    where, ids = _device_filter(conn, device)
    rows = conn.execute(
        f"""SELECT category, SUM(duration_s) AS total_s FROM daily_category_stats
        WHERE day BETWEEN ? AND ?{where} GROUP BY category ORDER BY total_s DESC, category""",
        [start.isoformat(), end.isoformat(), *ids],
    ).fetchall()
    total = sum(row["total_s"] for row in rows)
    return [
        {
            "category": row["category"],
            "total_s": round(row["total_s"]),
            "share_pct": _share(row["total_s"], total),
        }
        for row in rows
    ]


def late_night(
    conn: sqlite3.Connection,
    tz: ZoneInfo,
    start: date,
    end: date,
    device: str | None = None,
) -> dict[str, Any]:
    """Usage 00:00–06:00 (late) and 03:00–06:00 (deep) per day it falls on."""
    nights = [
        {key: day[key] for key in ("day", "late_s", "deep_night_s")}
        for day in daily_usage(conn, tz, start, end, device)
        if day["late_s"]
    ]
    return {
        "late_s": sum(night["late_s"] for night in nights),
        "deep_night_s": sum(night["deep_night_s"] for night in nights),
        "nights_with_late_use": len(nights),
        "worst_night": max(nights, key=lambda night: night["late_s"], default=None),
        "nights": nights,
    }


def _range_utc(tz: ZoneInfo, start: date, end: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(start, time(0), tz).astimezone(UTC),
        datetime.combine(end + timedelta(days=1), time(0), tz).astimezone(UTC),
    )


def hourly(
    conn: sqlite3.Connection,
    tz: ZoneInfo,
    start: date,
    end: date,
    device: str | None = None,
) -> list[int]:
    """Seconds per local hour of day (index 0–23) over the range."""
    where, ids = _device_filter(conn, device)
    low, high = _range_utc(tz, start, end)
    buckets = [0.0] * 24
    for row in conn.execute(
        f"""SELECT started_at_utc, duration_s FROM screen_events
        WHERE julianday(started_at_utc) < julianday(?)
          AND julianday(started_at_utc) + (duration_s / 86400.0) > julianday(?){where}""",
        [high.isoformat(), low.isoformat(), *ids],
    ):
        begin = parse_timestamp(row["started_at_utc"])
        stop = min(high, begin + timedelta(seconds=row["duration_s"]))
        cursor = max(low, begin)
        while cursor < stop:
            local = cursor.astimezone(tz)
            into_hour = local.minute * 60 + local.second + local.microsecond / 1e6
            step = min(stop, cursor + timedelta(seconds=3600 - into_hour))
            buckets[local.hour] += (step - cursor).total_seconds()
            cursor = step
    return [round(seconds) for seconds in buckets]


def summary(
    conn: sqlite3.Connection,
    tz: ZoneInfo,
    start: date,
    end: date,
    device: str | None = None,
    *,
    today: date | None = None,
    top: int = 10,
) -> dict[str, Any]:
    """Everything `screentime summary` and the MCP `get_summary` tool report.

    The previous period has the same length and ends where this one would if it
    were shifted back, cut to the days already elapsed: a week-to-date on
    Wednesday compares against Monday–Wednesday of the week before.
    """
    today = today or datetime.now(tz).date()
    days = daily_usage(conn, tz, start, end, device)
    total = sum(day["total_s"] for day in days)
    elapsed = max(0, (min(end, today) - start).days + 1)
    length = timedelta(days=(end - start).days + 1)
    previous = None
    if elapsed:
        prev_start, prev_end = start - length, min(end, today) - length
        prev_total = total_seconds(conn, prev_start, prev_end, device)
        previous = {
            "from": prev_start.isoformat(),
            "to": prev_end.isoformat(),
            "total_s": prev_total,
            "change_pct": round(100 * (total - prev_total) / prev_total, 1)
            if prev_total
            else None,
        }
    firsts = [day["first_activity"] for day in days if day["first_activity"]]
    lasts = [day["last_activity"] for day in days if day["last_activity"]]
    return {
        "period": {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "days": len(days),
            "elapsed_days": elapsed,
            "timezone": tz.key,
        },
        "device": device,
        "total_s": total,
        "avg_per_day_s": round(total / elapsed) if elapsed else 0,
        "active_days": sum(1 for day in days if day["total_s"]),
        "first_activity": min(firsts, key=datetime.fromisoformat, default=None),
        "last_activity": max(lasts, key=datetime.fromisoformat, default=None),
        "previous": previous,
        "devices": device_totals(conn, start, end, device),
        "daily": days,
        "top_apps": top_apps(conn, start, end, device, top),
        "categories": categories(conn, start, end, device),
        "late_night": late_night(conn, tz, start, end, device),
        "hourly_s": hourly(conn, tz, start, end, device),
    }


SESSION_FIELDS = (
    "device_id",
    "device",
    "bundle_id",
    "title",
    "category",
    "start",
    "end",
    "duration_s",
)
DAILY_FIELDS = (
    "day",
    "device_id",
    "device",
    "bundle_id",
    "title",
    "category",
    "duration_s",
    "sessions",
)


def _utc(day: date, tz: ZoneInfo) -> str:
    """Start of local ``day`` in the stored ``started_at_utc`` format."""
    return (
        datetime.combine(day, time(0), tz)
        .astimezone(UTC)
        .isoformat(timespec="microseconds")
    )


def iter_sessions(
    conn: sqlite3.Connection,
    tz: ZoneInfo,
    start: date | None = None,
    end: date | None = None,
    device: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Raw usage intervals that start within the range, oldest first (streamed)."""
    where, params = _device_filter(conn, device, "e.device_id")
    if start:
        where += " AND e.started_at_utc >= ?"
        params.append(_utc(start, tz))
    if end:
        where += " AND e.started_at_utc < ?"
        params.append(_utc(end + timedelta(days=1), tz))
    rows = conn.execute(
        f"""SELECT e.*, COALESCE(d.name, e.device_id) AS device
        FROM screen_events e LEFT JOIN devices d ON d.device_id = e.device_id
        WHERE 1{where} ORDER BY e.started_at_utc, e.event_key""",
        params,
    )

    def session(row: sqlite3.Row) -> dict[str, Any]:
        begin = parse_timestamp(row["started_at_utc"])  # UTC: DST-safe arithmetic
        return {
            "device_id": row["device_id"],
            "device": row["device"],
            "bundle_id": row["bundle_id"],
            "title": row["title"],
            "category": row["category"],
            "start": begin.astimezone(tz).isoformat(timespec="seconds"),
            "end": (begin + timedelta(seconds=row["duration_s"]))
            .astimezone(tz)
            .isoformat(timespec="seconds"),
            "duration_s": round(row["duration_s"], 3),
        }

    return map(session, rows)


def iter_daily_apps(
    conn: sqlite3.Connection,
    start: date | None = None,
    end: date | None = None,
    device: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Rows of ``daily_app_stats`` (per local day, device and app), streamed."""
    where, ids = _device_filter(conn, device, "s.device_id")
    rows = conn.execute(
        f"""SELECT s.*, COALESCE(d.name, s.device_id) AS device
        FROM daily_app_stats s LEFT JOIN devices d ON d.device_id = s.device_id
        WHERE s.day BETWEEN ? AND ?{where}
        ORDER BY s.day, device, s.duration_s DESC""",
        [(start or date.min).isoformat(), (end or date.max).isoformat(), *ids],
    )
    return (
        {
            "day": row["day"],
            "device_id": row["device_id"],
            "device": row["device"],
            "bundle_id": row["bundle_id"],
            "title": row["title"],
            "category": row["category"],
            "duration_s": round(row["duration_s"], 3),
            "sessions": row["event_count"],
        }
        for row in rows
    )
