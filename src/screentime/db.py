from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from typing import Any, Iterator


SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
  device_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  platform TEXT NOT NULL,
  source TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_state (
  source TEXT PRIMARY KEY,
  state_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS screen_events (
  event_key TEXT PRIMARY KEY,
  device_id TEXT NOT NULL,
  started_at_utc TEXT NOT NULL,
  duration_s REAL NOT NULL CHECK(duration_s >= 0),
  bundle_id TEXT NOT NULL,
  title TEXT NOT NULL,
  category TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_screen_events_started ON screen_events(started_at_utc);
CREATE INDEX IF NOT EXISTS idx_screen_events_updated ON screen_events(updated_at, event_key);
CREATE INDEX IF NOT EXISTS idx_screen_events_device ON screen_events(device_id, started_at_utc);
CREATE TABLE IF NOT EXISTS daily_app_stats (
  day TEXT NOT NULL,
  device_id TEXT NOT NULL,
  bundle_id TEXT NOT NULL,
  title TEXT NOT NULL,
  category TEXT NOT NULL,
  duration_s REAL NOT NULL,
  event_count INTEGER NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(day, device_id, bundle_id)
);
CREATE TABLE IF NOT EXISTS daily_category_stats (
  day TEXT NOT NULL,
  device_id TEXT NOT NULL,
  category TEXT NOT NULL,
  duration_s REAL NOT NULL,
  event_count INTEGER NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(day, device_id, category)
);
CREATE TABLE IF NOT EXISTS daily_device_stats (
  day TEXT NOT NULL,
  device_id TEXT NOT NULL,
  duration_s REAL NOT NULL,
  late_duration_s REAL NOT NULL,
  deep_night_duration_s REAL NOT NULL,
  event_count INTEGER NOT NULL,
  first_activity_at TEXT,
  last_activity_at TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(day, device_id)
);
CREATE TABLE IF NOT EXISTS influx_sync_cursors (
  destination TEXT PRIMARY KEY,
  updated_at TEXT,
  event_key TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  run_type TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('running', 'success', 'partial', 'failed')),
  started_at TEXT NOT NULL,
  finished_at TEXT,
  events_read INTEGER NOT NULL DEFAULT 0,
  events_inserted INTEGER NOT NULL DEFAULT 0,
  events_updated INTEGER NOT NULL DEFAULT 0,
  points_written INTEGER NOT NULL DEFAULT 0,
  details_json TEXT,
  error TEXT
);
-- iTunes lookup cache; name NULL = not found (retried later).
CREATE TABLE IF NOT EXISTS app_lookup (
  bundle_id TEXT PRIMARY KEY,
  name TEXT,
  category TEXT,
  genre TEXT,
  checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS service_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""


def now_utc_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def parse_timestamp(value: Any) -> datetime:
    """Parse an offset-bearing instant and normalize it to UTC.

    Naive values have no safe interpretation and are rejected.
    """
    text = str(value or "").strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("naive timestamp requires an explicit offset")
    return parsed.astimezone(UTC)


def connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    path = Path(path)
    if read_only:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            path.chmod(0o600)
        except OSError:
            pass
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        "SELECT value FROM service_state WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """INSERT INTO service_state(key, value, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
        (key, value, now_utc_iso()),
    )
