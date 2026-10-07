"""Import `UsageEvent`s from the enabled sources into SQLite."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

from .aggregates import affected_days, rebuild_days
from .config import Settings
from .db import now_utc_iso, parse_timestamp, transaction
from .sources import Device, Source, UsageEvent
from .taxonomy import classify, load_cache, lookup_missing

# SCREENTIME_LIBRARY points at a copied ~/Library for development and tests.
LIBRARY = Path(os.environ.get("SCREENTIME_LIBRARY") or Path.home() / "Library")
BIOME_STREAM = LIBRARY / "Biome" / "streams" / "restricted" / "App.InFocus"
KNOWLEDGEC_DB = LIBRARY / "Application Support" / "Knowledge" / "knowledgeC.db"


def enabled_sources(settings: Settings) -> list[Source]:
    # Imported lazily so the package works without (or before) a source module.
    sources: list[Source] = []
    if settings.sources.get("biome", True):
        from .sources.biome import BiomeSource

        sources.append(BiomeSource(LIBRARY))
    if settings.sources.get("knowledgec", True):
        from .sources.knowledgec import KnowledgeCSource

        sources.append(KnowledgeCSource(KNOWLEDGEC_DB))
    return sources


@dataclass
class ImportStats:
    sources_ok: int = 0
    sources_failed: int = 0
    events_read: int = 0
    events_inserted: int = 0
    events_updated: int = 0
    events_unchanged: int = 0
    events_invalid: int = 0
    days_rebuilt: int = 0
    changed_keys: set[str] = field(default_factory=set, repr=False)
    errors: list[str] = field(default_factory=list)


def event_key(device_id: str, started_at: Any, bundle_id: str) -> str:
    started = parse_timestamp(started_at).isoformat(timespec="microseconds")
    raw = "\0".join((device_id, started, bundle_id.strip()))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def content_hash(duration: float, bundle_id: str, title: str, category: str) -> str:
    raw = json.dumps(
        [round(duration, 6), bundle_id, title, category],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _upsert_device(
    conn: sqlite3.Connection, device: Device, names: dict[str, str], now: str
) -> None:
    conn.execute(
        """INSERT INTO devices(device_id, name, platform, source, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(device_id) DO UPDATE SET
          name=excluded.name, platform=excluded.platform, source=excluded.source,
          last_seen_at=excluded.last_seen_at""",
        (
            device.id,
            names.get(device.id) or device.name,
            device.platform,
            device.source,
            now,
            now,
        ),
    )


def _upsert_event(
    conn: sqlite3.Connection, event: UsageEvent, now: str
) -> tuple[str, str]:
    """Insert or update one event; return (inserted|updated|unchanged, key)."""
    bundle_id = event.bundle_id.strip()
    if not bundle_id:
        raise ValueError("event has no bundle id")
    started = parse_timestamp(event.start.isoformat())
    duration = (event.end - event.start).total_seconds()
    if duration <= 0:
        raise ValueError("event does not end after it starts")
    title, category = classify(bundle_id)
    key = event_key(event.device_id, started, bundle_id)
    digest = content_hash(duration, bundle_id, title, category)
    existing = conn.execute(
        "SELECT content_hash FROM screen_events WHERE event_key = ?", (key,)
    ).fetchone()
    if existing is not None and existing["content_hash"] == digest:
        return "unchanged", key
    conn.execute(
        """INSERT INTO screen_events(
          event_key, device_id, started_at_utc, duration_s, bundle_id, title,
          category, content_hash, first_seen_at, last_seen_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_key) DO UPDATE SET
          duration_s=excluded.duration_s, title=excluded.title,
          category=excluded.category, content_hash=excluded.content_hash,
          last_seen_at=excluded.last_seen_at, updated_at=excluded.updated_at""",
        (
            key,
            event.device_id,
            started.isoformat(timespec="microseconds"),
            duration,
            bundle_id,
            title,
            category,
            digest,
            now,
            now,
            now,
        ),
    )
    return "updated" if existing is not None else "inserted", key


def _import_source(
    conn: sqlite3.Connection,
    source: Source,
    device_names: dict[str, str],
    stats: ImportStats,
    ignored: frozenset[str],
) -> None:
    row = conn.execute(
        "SELECT state_json FROM source_state WHERE source = ?", (source.name,)
    ).fetchone()
    events, new_state = source.read(json.loads(row["state_json"]) if row else None)
    events = [event for event in events if event.device_id not in ignored]
    devices = {d.id: d for d in source.devices() if d.id not in ignored}
    for device_id in {event.device_id for event in events} - devices.keys():
        devices[device_id] = Device(device_id, device_id, "unknown", source.name)
    now = now_utc_iso()
    # Events and the new source state commit together, so a crash re-reads.
    with transaction(conn):
        for device in devices.values():
            _upsert_device(conn, device, device_names, now)
        for event in events:
            try:
                outcome, key = _upsert_event(conn, event, now)
            except (TypeError, ValueError, OverflowError) as exc:
                stats.events_invalid += 1
                error = f"{source.name}: invalid event: {exc}"
                if error not in stats.errors:
                    stats.errors.append(error)
                continue
            setattr(stats, f"events_{outcome}", getattr(stats, f"events_{outcome}") + 1)
            if outcome != "unchanged":
                stats.changed_keys.add(key)
        conn.execute(
            """INSERT INTO source_state(source, state_json, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
              state_json=excluded.state_json, updated_at=excluded.updated_at""",
            (source.name, json.dumps(new_state, sort_keys=True), now),
        )
    stats.events_read += len(events)


def reclassify(
    conn: sqlite3.Connection, bundle_ids: set[str] | None = None
) -> set[str]:
    """Reapply the taxonomy to stored events (all, or only ``bundle_ids``).

    Returns the keys of changed events; the caller rebuilds days and commits.
    """
    changed = set()
    now = now_utc_iso()
    query, params = "SELECT * FROM screen_events", list(bundle_ids or ())
    if bundle_ids is not None:
        query += f" WHERE bundle_id IN ({','.join('?' * len(params))})"
    for row in conn.execute(query, params).fetchall():
        title, category = classify(row["bundle_id"])
        if (title, category) == (row["title"], row["category"]):
            continue
        conn.execute(
            """UPDATE screen_events SET title=?, category=?, content_hash=?, updated_at=?
            WHERE event_key=?""",
            (
                title,
                category,
                content_hash(row["duration_s"], row["bundle_id"], title, category),
                now,
                row["event_key"],
            ),
        )
        changed.add(row["event_key"])
    return changed


def sync_sources(
    conn: sqlite3.Connection,
    sources: Iterable[Source],
    timezone: ZoneInfo,
    *,
    device_names: dict[str, str] | None = None,
    ignored_devices: frozenset[str] = frozenset(),
    lookup_country: str | None = None,
) -> ImportStats:
    """Read every source, upsert its devices and events, rebuild touched days.

    A failing source is recorded in ``errors`` and does not stop the others.
    Unknown bundle ids are then looked up among the installed Mac apps and,
    with ``lookup_country`` set, in the App Store (best effort); their stored
    events are reclassified.
    """
    stats = ImportStats()
    load_cache(conn)
    for source in sources:
        try:
            _import_source(conn, source, device_names or {}, stats, ignored_devices)
        except Exception as exc:  # noqa: BLE001 - one broken source must not block others
            stats.sources_failed += 1
            stats.errors.append(f"{source.name}: {type(exc).__name__}: {exc}")
        else:
            stats.sources_ok += 1
    if resolved := lookup_missing(conn, lookup_country):
        with transaction(conn):
            stats.changed_keys |= reclassify(conn, resolved)
    stats.days_rebuilt = len(days := affected_days(conn, stats.changed_keys, timezone))
    rebuild_days(conn, days, timezone)
    return stats
