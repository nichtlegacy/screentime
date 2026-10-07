"""InfluxDB 2 export.

Schema (all durations in seconds, all points tagged ``device``, ``device_id``
and ``platform``):

* ``screentime``           one point per session at its start (µs precision);
                           tags ``app`` (display name), ``bundle_id``,
                           ``category``; field ``duration_s``.
* ``screentime_daily``     one point per device and local day at local
                           midnight; fields ``duration_s``, ``late_duration_s``
                           (00–06), ``deep_night_duration_s`` (03–06),
                           ``event_count``, ``first_activity_s`` and
                           ``last_activity_s`` (wall-clock seconds after
                           midnight).
* ``screentime_app_daily`` per device, local day and app at local midnight;
                           tags as ``screentime``; fields ``duration_s``,
                           ``event_count``.
* ``screentime_hourly``    per device and local hour at the hour's start;
                           tags ``hour`` (``00``–``23``) and ``weekday``
                           (ISO ``1`` = Monday … ``7``); field ``duration_s``.

The unit of export is a (device, local day): its points are deleted and
rewritten, so re-exports are idempotent even after a remap or device rename.
Only days whose aggregates changed since the last export are sent.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator
from datetime import UTC, date, datetime, time, timedelta
import json
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

import requests

from .config import Settings, integration_complete
from .db import now_utc_iso, parse_timestamp, set_state, transaction

PLATFORM_LABELS = {"iphone": "iPhone", "ipad": "iPad", "mac": "Mac"}
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _escape_tag(value: str) -> str:
    return (
        value.replace("\r", " ")
        .replace("\n", " ")
        .replace("\\", "\\\\")
        .replace(" ", "\\ ")
        .replace(",", "\\,")
        .replace("=", "\\=")
    )


def _us(moment: datetime) -> int:
    return (moment - _EPOCH) // timedelta(microseconds=1)


def line(
    measurement: str, tags: dict[str, Any], fields: dict[str, Any], at: datetime
) -> str:
    # Influx rejects empty tag values, so they become "Unknown".
    tag_text = ",".join(
        f"{key}={_escape_tag(str(value or 'Unknown'))}" for key, value in tags.items()
    )
    field_text = ",".join(
        f"{key}={value}i" if isinstance(value, int) else f"{key}={float(value)}"
        for key, value in fields.items()
        if value is not None
    )
    return f"{measurement},{tag_text} {field_text} {_us(at)}"


def _midnight(day: date, timezone: ZoneInfo) -> datetime:
    return datetime.combine(day, time(0), timezone).astimezone(UTC)


def _seconds_after_midnight(moment: str | None, day: date, tz: ZoneInfo) -> int | None:
    if not moment:
        return None
    local = parse_timestamp(moment).astimezone(tz)
    if local.date() > day:
        return 86400
    return local.hour * 3600 + local.minute * 60 + local.second


def hour_pieces(
    start: datetime, end: datetime, timezone: ZoneInfo
) -> Iterator[tuple[datetime, float]]:
    """Split [start, end) at local hour boundaries: (local hour start, seconds)."""
    moment = start.astimezone(UTC)
    end = end.astimezone(UTC)
    while moment < end:
        offset = moment.astimezone(timezone).utcoffset() or timedelta()
        hour_start = (moment + offset).replace(minute=0, second=0, microsecond=0)
        # Offsets only change on hour boundaries, so this is the next local hour.
        following = min(hour_start - offset + timedelta(hours=1), end)
        yield (
            (hour_start - offset).astimezone(timezone),
            (following - moment).total_seconds(),
        )
        moment = following


def _runs(days: list[date]) -> Iterator[tuple[date, date]]:
    """Group sorted days into contiguous [first, last] runs."""
    first = last = days[0]
    for day in days[1:]:
        if day != last + timedelta(days=1):
            yield first, last
            first = day
        last = day
    yield first, last


def _device_lines(
    conn: sqlite3.Connection,
    device: sqlite3.Row,
    days: set[date],
    timezone: ZoneInfo,
) -> Iterator[str]:
    base = {
        "device": device["name"],
        "device_id": device["device_id"],
        "platform": PLATFORM_LABELS.get(device["platform"] or "", "Unknown"),
    }
    first, last = min(days), max(days)
    range_start = _midnight(first, timezone)
    range_end = _midnight(last + timedelta(days=1), timezone)
    params = (device["device_id"], first.isoformat(), last.isoformat())
    for row in conn.execute(
        "SELECT * FROM daily_device_stats WHERE device_id=? AND day BETWEEN ? AND ?",
        params,
    ):
        day = date.fromisoformat(row["day"])
        if day in days:
            yield line(
                "screentime_daily",
                base,
                {
                    "duration_s": row["duration_s"],
                    "late_duration_s": row["late_duration_s"],
                    "deep_night_duration_s": row["deep_night_duration_s"],
                    "event_count": int(row["event_count"]),
                    "first_activity_s": _seconds_after_midnight(
                        row["first_activity_at"], day, timezone
                    ),
                    "last_activity_s": _seconds_after_midnight(
                        row["last_activity_at"], day, timezone
                    ),
                },
                _midnight(day, timezone),
            )
    for row in conn.execute(
        "SELECT * FROM daily_app_stats WHERE device_id=? AND day BETWEEN ? AND ?",
        params,
    ):
        day = date.fromisoformat(row["day"])
        if day in days:
            yield line(
                "screentime_app_daily",
                {**base, **_app_tags(row)},
                {"duration_s": row["duration_s"], "event_count": row["event_count"]},
                _midnight(day, timezone),
            )
    hourly: dict[datetime, float] = defaultdict(float)
    # Same overlap predicate as aggregates.rebuild_days: sessions crossing
    # into the range from the previous day count towards its hours.
    for row in conn.execute(
        """SELECT * FROM screen_events WHERE device_id = ?
          AND julianday(started_at_utc) < julianday(?)
          AND julianday(started_at_utc) + (duration_s / 86400.0) > julianday(?)""",
        (device["device_id"], range_end.isoformat(), range_start.isoformat()),
    ):
        start = parse_timestamp(row["started_at_utc"])
        if start.astimezone(timezone).date() in days:
            yield line(
                "screentime",
                {**base, **_app_tags(row)},
                {"duration_s": row["duration_s"]},
                start,
            )
        end = start + timedelta(seconds=row["duration_s"])
        for local_hour, seconds in hour_pieces(start, end, timezone):
            if local_hour.date() in days:
                hourly[local_hour] += seconds
    for local_hour, seconds in hourly.items():
        yield line(
            "screentime_hourly",
            {
                **base,
                "hour": f"{local_hour.hour:02d}",
                "weekday": str(local_hour.isoweekday()),
            },
            {"duration_s": seconds},
            local_hour,
        )


def _app_tags(row: sqlite3.Row) -> dict[str, str]:
    return {
        "app": row["title"],
        "bundle_id": row["bundle_id"],
        "category": row["category"],
    }


class _Client:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def post(self, path: str, body: str, content_type: str, **params: str) -> None:
        settings = self.settings
        response = requests.post(
            f"{settings.influx_url}/api/v2/{path}",
            params={
                "org": settings.influx_org,
                "bucket": settings.influx_bucket,
                **params,
            },
            headers={
                "Authorization": f"Token {settings.influx_token}",
                "Content-Type": content_type,
            },
            data=body.encode("utf-8"),
            timeout=settings.request_timeout,
        )
        if response.status_code != 204:
            raise RuntimeError(
                f"InfluxDB {path} failed with HTTP {response.status_code}: "
                f"{response.text[:200]}"
            )

    def delete(self, device_id: str, start: datetime, stop: datetime) -> None:
        # The delete range is inclusive; stop short of the next day's midnight point.
        body = {
            "start": start.isoformat(),
            "stop": (stop - timedelta(microseconds=1)).isoformat(),
            "predicate": f'device_id="{device_id}"',
        }
        self.post("delete", json.dumps(body), "application/json")

    def write(self, lines: Iterable[str], batch_size: int) -> int:
        written = 0
        batch: list[str] = []
        for item in lines:
            batch.append(item)
            if len(batch) >= batch_size:
                written += self._send(batch)
                batch = []
        return written + (self._send(batch) if batch else 0)

    def _send(self, batch: list[str]) -> int:
        self.post(
            "write", "\n".join(batch), "text/plain; charset=utf-8", precision="us"
        )
        return len(batch)


def touched_days(
    conn: sqlite3.Connection, from_date: date | None = None
) -> list[sqlite3.Row]:
    """Device days to export: since ``from_date``, else changed since the cursor."""
    select = "SELECT day, device_id, updated_at FROM daily_device_stats"
    if from_date is not None:
        return conn.execute(
            f"{select} WHERE day >= ?", (from_date.isoformat(),)
        ).fetchall()
    cursor = conn.execute(
        "SELECT updated_at FROM influx_sync_cursors WHERE destination='influx'"
    ).fetchone()
    since = (cursor and cursor["updated_at"]) or ""
    return conn.execute(f"{select} WHERE updated_at > ?", (since,)).fetchall()


def export_influx(
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    from_date: date | None = None,
    batch_size: int = 5000,
) -> int:
    """Rewrite every touched (device, day) in InfluxDB; return points written.

    ``from_date`` re-sends all days from that local date and leaves the
    incremental cursor alone.
    """
    if not integration_complete(settings, "influx"):
        return 0
    rows = touched_days(conn, from_date)
    if not rows:
        return 0
    by_device: dict[str, set[date]] = defaultdict(set)
    for row in rows:
        by_device[row["device_id"]].add(date.fromisoformat(row["day"]))
    client = _Client(settings)
    tz = settings.timezone
    written = 0
    # ponytail: the cursor only advances once everything is written, so a
    # failed first export starts over; fine at Screen Time volumes.
    for device in conn.execute("SELECT * FROM devices").fetchall():
        days = by_device.get(device["device_id"])
        if not days:
            continue
        for first, last in _runs(sorted(days)):
            client.delete(
                device["device_id"],
                _midnight(first, tz),
                _midnight(last + timedelta(days=1), tz),
            )
        written += client.write(_device_lines(conn, device, days, tz), batch_size)
    with transaction(conn):
        if from_date is None:
            conn.execute(
                """INSERT INTO influx_sync_cursors(destination, updated_at)
                VALUES ('influx', ?) ON CONFLICT(destination)
                DO UPDATE SET updated_at=excluded.updated_at""",
                (max(row["updated_at"] for row in rows),),
            )
        set_state(conn, "last_influx_success_at", now_utc_iso())
    return written
