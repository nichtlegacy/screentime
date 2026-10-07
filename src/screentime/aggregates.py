from __future__ import annotations

from collections import defaultdict
from contextlib import nullcontext
from datetime import UTC, date, datetime, time, timedelta
import sqlite3
from zoneinfo import ZoneInfo

from .db import now_utc_iso, parse_timestamp, transaction


def split_interval(
    start: datetime, duration_s: float, timezone: ZoneInfo
) -> list[tuple[str, datetime, datetime, float, float, float]]:
    start_utc = start.astimezone(UTC)
    end_utc = start_utc + timedelta(seconds=max(0.0, duration_s))
    if end_utc <= start_utc:
        return []
    boundaries = {start_utc, end_utc}
    first_day = start_utc.astimezone(timezone).date()
    last_day = end_utc.astimezone(timezone).date()
    current = first_day
    while current <= last_day + timedelta(days=1):
        for wall_time in (time(0, 0), time(3, 0), time(6, 0)):
            boundary = datetime.combine(current, wall_time, timezone).astimezone(UTC)
            if start_utc < boundary < end_utc:
                boundaries.add(boundary)
        current += timedelta(days=1)
    points = sorted(boundaries)
    result = []
    for left, right in zip(points, points[1:]):
        seconds = (right - left).total_seconds()
        local = left.astimezone(timezone)
        local_time = local.timetz().replace(tzinfo=None)
        late = seconds if time(0, 0) <= local_time < time(6, 0) else 0.0
        deep = seconds if time(3, 0) <= local_time < time(6, 0) else 0.0
        result.append((local.date().isoformat(), left, right, seconds, late, deep))
    return result


def affected_days(
    conn: sqlite3.Connection, changed_keys: set[str], timezone: ZoneInfo
) -> set[str]:
    if not changed_keys:
        return set()
    days: set[str] = set()
    keys = sorted(changed_keys)
    for offset in range(0, len(keys), 500):
        batch = keys[offset : offset + 500]
        rows = conn.execute(
            f"SELECT started_at_utc, duration_s FROM screen_events WHERE event_key IN ({','.join('?' for _ in batch)})",
            batch,
        )
        for row in rows:
            for day, *_rest in split_interval(
                parse_timestamp(row["started_at_utc"]), row["duration_s"], timezone
            ):
                days.add(day)
    return days


def rebuild_days(
    conn: sqlite3.Connection,
    days: set[str],
    timezone: ZoneInfo,
    *,
    manage_transaction: bool = True,
) -> int:
    if not days:
        return 0
    app: dict[tuple[str, str, str], dict] = defaultdict(
        lambda: {"duration": 0.0, "count": 0, "title": "", "category": "Other"}
    )
    category: dict[tuple[str, str, str], dict] = defaultdict(
        lambda: {"duration": 0.0, "count": 0}
    )
    device: dict[tuple[str, str], dict] = defaultdict(
        lambda: {
            "duration": 0.0,
            "late": 0.0,
            "deep": 0.0,
            "keys": set(),
            "first": None,
            "last": None,
        }
    )
    first_day = date.fromisoformat(min(days))
    last_day = date.fromisoformat(max(days)) + timedelta(days=1)
    range_start = datetime.combine(first_day, time(0), timezone).astimezone(UTC)
    range_end = datetime.combine(last_day, time(0), timezone).astimezone(UTC)
    # Iterate the SQLite cursor directly.  A historical rebuild may cover
    # millions of events; ``fetchall()`` used to duplicate that history in
    # Python before the bounded daily accumulators were populated.
    for row in conn.execute(
        """SELECT * FROM screen_events
        WHERE julianday(started_at_utc) < julianday(?)
          AND julianday(started_at_utc) + (duration_s / 86400.0) > julianday(?)
        ORDER BY started_at_utc, event_key""",
        (range_end.isoformat(), range_start.isoformat()),
    ):
        pieces = split_interval(
            parse_timestamp(row["started_at_utc"]), row["duration_s"], timezone
        )
        counted_days: set[str] = set()
        for day, left, right, seconds, late, deep in pieces:
            if day not in days:
                continue
            app_key = (day, row["device_id"], row["bundle_id"])
            app[app_key]["duration"] += seconds
            app[app_key]["title"] = row["title"]
            app[app_key]["category"] = row["category"]
            category_key = (day, row["device_id"], row["category"])
            category[category_key]["duration"] += seconds
            device_key = (day, row["device_id"])
            device[device_key]["duration"] += seconds
            device[device_key]["late"] += late
            device[device_key]["deep"] += deep
            device[device_key]["keys"].add(row["event_key"])
            device[device_key]["first"] = min(
                filter(None, (device[device_key]["first"], left.isoformat())),
                default=None,
            )
            device[device_key]["last"] = max(
                filter(None, (device[device_key]["last"], right.isoformat())),
                default=None,
            )
            if day not in counted_days:
                app[app_key]["count"] += 1
                category[category_key]["count"] += 1
                counted_days.add(day)
    now = now_utc_iso()
    transaction_context = transaction(conn) if manage_transaction else nullcontext()
    with transaction_context:
        for day in days:
            conn.execute("DELETE FROM daily_app_stats WHERE day = ?", (day,))
            conn.execute("DELETE FROM daily_category_stats WHERE day = ?", (day,))
            conn.execute("DELETE FROM daily_device_stats WHERE day = ?", (day,))
        for (day, device_id, bundle_id), values in app.items():
            conn.execute(
                """INSERT INTO daily_app_stats(
                  day, device_id, bundle_id, title, category, duration_s,
                  event_count, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    day,
                    device_id,
                    bundle_id,
                    values["title"],
                    values["category"],
                    values["duration"],
                    values["count"],
                    now,
                ),
            )
        for (day, device_id, category_name), values in category.items():
            conn.execute(
                """INSERT INTO daily_category_stats(
                  day, device_id, category, duration_s, event_count, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    day,
                    device_id,
                    category_name,
                    values["duration"],
                    values["count"],
                    now,
                ),
            )
        for (day, device_id), values in device.items():
            conn.execute(
                """INSERT INTO daily_device_stats(
                  day, device_id, duration_s, late_duration_s,
                  deep_night_duration_s, event_count, first_activity_at,
                  last_activity_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    day,
                    device_id,
                    values["duration"],
                    values["late"],
                    values["deep"],
                    len(values["keys"]),
                    values["first"],
                    values["last"],
                    now,
                ),
            )
    return len(app) + len(category) + len(device)


def rebuild_all(
    conn: sqlite3.Connection,
    timezone: ZoneInfo,
    *,
    manage_transaction: bool = True,
) -> int:
    days: set[str] = set()
    for row in conn.execute("SELECT started_at_utc, duration_s FROM screen_events"):
        days.update(
            piece[0]
            for piece in split_interval(
                parse_timestamp(row["started_at_utc"]), row["duration_s"], timezone
            )
        )
    return rebuild_days(conn, days, timezone, manage_transaction=manage_transaction)
