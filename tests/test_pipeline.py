from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from screentime import cli
from screentime.aggregates import rebuild_all, rebuild_days, split_interval
from screentime.config import Settings
from screentime.db import connect, init_db
from screentime.exporter import ExportResult, export_all
from screentime.importer import sync_sources
from screentime.locking import LockBusyError, process_lock
from screentime.sources import Device, UsageEvent

BERLIN = ZoneInfo("Europe/Berlin")
PHONE = Device("phone-1", "iPhone", "iphone", "fake")
MAC = Device("mac:1", "MacBook", "mac", "fake")


class FakeSource:
    """In-memory `Source`: serves fixed events and records the state it gets."""

    def __init__(self, events: list[UsageEvent], *, name: str = "fake") -> None:
        self.name = name
        self.events = events
        self.states: list[dict[str, Any] | None] = []

    def devices(self) -> list[Device]:
        return [PHONE, MAC]

    def read(
        self, state: dict[str, Any] | None
    ) -> tuple[list[UsageEvent], dict[str, Any]]:
        self.states.append(state)
        return self.events, {"cursor": len(self.states)}


class BrokenSource(FakeSource):
    def read(self, state):
        raise PermissionError("Operation not permitted")


def event(
    start: str = "2026-07-25T10:00:00+00:00",
    seconds: float = 60,
    bundle_id: str = "com.example.app",
    device: Device = PHONE,
) -> UsageEvent:
    begin = datetime.fromisoformat(start)
    return UsageEvent(device.id, bundle_id, begin, begin + timedelta(seconds=seconds))


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values: dict[str, Any] = dict(
        db_path=tmp_path / "data" / "screentime.db", timezone=BERLIN
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "data" / "screentime.db")
    init_db(connection)
    yield connection
    connection.close()


def test_sync_upserts_devices_events_state_and_days(conn):
    source = FakeSource(
        [event(), event("2026-07-24T21:30:00+00:00", 3600, "com.burbn.instagram")]
    )

    first = sync_sources(conn, [source], BERLIN, device_names={"phone-1": "Work"})
    second = sync_sources(conn, [source], BERLIN)
    source.events = [event(seconds=90)]
    third = sync_sources(conn, [source], BERLIN)

    assert (first.events_inserted, second.events_unchanged) == (2, 2)
    assert (third.events_updated, third.events_inserted) == (1, 0)
    assert source.states == [None, {"cursor": 1}, {"cursor": 2}]
    stored = conn.execute("SELECT state_json FROM source_state").fetchone()[0]
    assert json.loads(stored) == {"cursor": 3}
    devices = {
        row["device_id"]: tuple(row)[1:4]
        for row in conn.execute("SELECT * FROM devices")
    }
    # The configured name only lives as long as the config entry does.
    assert devices == {
        "phone-1": ("iPhone", "iphone", "fake"),
        "mac:1": ("MacBook", "mac", "fake"),
    }
    instagram = conn.execute(
        "SELECT title, category FROM screen_events WHERE bundle_id='com.burbn.instagram'"
    ).fetchone()
    assert tuple(instagram) == ("Instagram", "Social")
    days = dict(conn.execute("SELECT day, duration_s FROM daily_device_stats"))
    # 21:30 UTC + 1 h crosses Berlin midnight; the 10:00 event grew to 90 s.
    assert days == {"2026-07-24": 1800, "2026-07-25": 1800 + 90}


def test_configured_device_name_overrides_source_name(conn):
    sync_sources(conn, [FakeSource([])], BERLIN, device_names={"phone-1": "Work"})
    name = conn.execute("SELECT name FROM devices WHERE device_id='phone-1'")
    assert name.fetchone()[0] == "Work"


def test_ignored_device_is_not_imported(conn):
    events = [event(), event(device=MAC)]
    stats = sync_sources(
        conn, [FakeSource(events)], BERLIN, ignored_devices=frozenset({"phone-1"})
    )
    assert stats.events_inserted == 1
    devices = [row[0] for row in conn.execute("SELECT device_id FROM devices")]
    assert devices == ["mac:1"]


def test_failing_source_and_invalid_event_do_not_block_others(conn):
    naive = UsageEvent(
        "phone-1", "com.example.app", datetime(2026, 7, 25), datetime(2026, 7, 26)
    )
    stats = sync_sources(
        conn, [BrokenSource([], name="broken"), FakeSource([event(), naive])], BERLIN
    )

    assert (stats.sources_ok, stats.sources_failed) == (1, 1)
    assert (stats.events_inserted, stats.events_invalid) == (1, 1)
    assert stats.errors[0].startswith("broken: PermissionError")
    assert conn.execute("SELECT source FROM source_state").fetchall()[0][0] == "fake"


def test_cli_run_records_partial_run(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(
        cli,
        "enabled_sources",
        lambda _settings: [FakeSource([event()]), BrokenSource([], name="broken")],
    )

    assert cli.main(["run"]) == 0

    connection = connect(settings.db_path, read_only=True)
    run = connection.execute("SELECT * FROM runs").fetchone()
    assert (run["run_type"], run["status"], run["events_inserted"]) == (
        "run",
        "partial",
        1,
    )
    assert "broken: PermissionError" in run["error"]
    connection.close()


def test_interval_splits_midnight_late_and_deep():
    pieces = split_interval(
        datetime(2026, 7, 24, 23, 30, tzinfo=BERLIN), 8 * 3600, BERLIN
    )
    by_day: dict[str, list[float]] = {}
    for day, _left, _right, total, late, deep in pieces:
        item = by_day.setdefault(day, [0, 0, 0])
        item[0] += total
        item[1] += late
        item[2] += deep
    assert by_day["2026-07-24"] == [1800, 0, 0]
    assert by_day["2026-07-25"] == [27000, 21600, 10800]


@pytest.mark.parametrize(
    ("start", "duration"),
    [
        (datetime(2026, 3, 29, 0, 0, tzinfo=BERLIN), 23 * 3600),
        (datetime(2026, 10, 25, 0, 0, tzinfo=BERLIN), 25 * 3600),
    ],
)
def test_interval_split_preserves_real_seconds_on_dst_days(start, duration):
    pieces = split_interval(start, duration, BERLIN)
    assert sum(piece[3] for piece in pieces) == duration


def test_aggregate_invariants(conn):
    sync_sources(
        conn, [FakeSource([event("2026-07-24T21:30:00+00:00", 8 * 3600)])], BERLIN
    )
    rebuild_all(conn, BERLIN)
    rows = conn.execute("SELECT * FROM daily_device_stats ORDER BY day").fetchall()
    assert len(rows) == 2
    for row in rows:
        assert (
            row["deep_night_duration_s"] <= row["late_duration_s"] <= row["duration_s"]
        )


def test_single_day_rebuild_includes_event_crossing_local_midnight(conn):
    sync_sources(conn, [FakeSource([event("2026-07-24T21:59:00+00:00", 120)])], BERLIN)
    rebuild_days(conn, {"2026-07-25"}, BERLIN)
    row = conn.execute(
        "SELECT duration_s FROM daily_device_stats WHERE day='2026-07-25'"
    ).fetchone()
    assert row["duration_s"] == 60


def test_missing_optional_destinations_are_skipped(conn, tmp_path):
    assert export_all(conn, make_settings(tmp_path)) == ExportResult()


def test_process_lock_rejects_parallel_holder(tmp_path):
    path = tmp_path / "sync.lock"
    with process_lock(path):
        with pytest.raises(LockBusyError):
            with process_lock(path):
                pass
