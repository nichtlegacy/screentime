from __future__ import annotations

from datetime import UTC, date, datetime
import json
from zoneinfo import ZoneInfo

import pytest

from screentime.importer import sync_sources
from screentime.influx import export_influx, hour_pieces, line
from screentime.db import connect, init_db
from test_pipeline import BERLIN, MAC, FakeSource, event, make_settings


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "screentime.db")
    init_db(connection)
    yield connection
    connection.close()


def test_line_protocol_escapes_tags_and_types_fields():
    at = datetime(2026, 7, 25, 10, 0, 0, 123456, tzinfo=UTC)
    text = line(
        "screentime",
        {"device": "Alex's iPad, 2=x", "app": "a\\b", "category": ""},
        {"duration_s": 5.0, "event_count": 3, "first_activity_s": None},
        at,
    )
    assert text == (
        "screentime,device=Alex's\\ iPad\\,\\ 2\\=x,app=a\\\\b,category=Unknown "
        "duration_s=5.0,event_count=3i 1784973600123456"
    )


def test_hour_pieces_split_at_local_hours_across_dst():
    # Berlin falls back at 03:00 CEST → 02:00 CET: the 02 hour happens twice.
    start = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)  # 02:30 CEST
    end = datetime(2026, 10, 25, 2, 0, tzinfo=UTC)  # 03:00 CET
    pieces = [
        (local.strftime("%H:%M%z"), sec)
        for local, sec in hour_pieces(start, end, BERLIN)
    ]
    assert pieces == [("02:00+0200", 1800), ("02:00+0100", 3600)]


class FakeInflux:
    def __init__(self, status: int = 204) -> None:
        self.status = status
        self.calls: list[tuple[str, str]] = []

    def __call__(self, url, params, data, **kwargs):
        self.calls.append((url.rsplit("/", 1)[-1], data.decode()))
        return type("Response", (), {"status_code": self.status, "text": ""})()

    def lines(self) -> list[str]:
        return [
            item
            for kind, body in self.calls
            if kind == "write"
            for item in body.split("\n")
        ]

    def deletes(self) -> list[dict]:
        return [json.loads(body) for kind, body in self.calls if kind == "delete"]


@pytest.fixture
def influx(monkeypatch):
    fake = FakeInflux()
    monkeypatch.setattr("screentime.influx.requests.post", fake)
    return fake


def influx_settings(tmp_path, **overrides):
    return make_settings(
        tmp_path,
        influx_url="http://influx.test:8086",
        influx_token="secret",
        influx_org="home",
        **overrides,
    )


def test_export_schema_and_incremental_days(conn, tmp_path, influx):
    events = [
        # Same device, app and second: µs timestamps keep both sessions.
        event("2026-07-25T10:00:00.100000+00:00", 60),
        event("2026-07-25T10:00:00.200000+00:00", 30),
        event("2026-07-24T21:30:00+00:00", 3600, "com.burbn.instagram"),
        event("2026-07-25T11:00:00+00:00", 600, device=MAC),
    ]
    source = FakeSource(events)
    sync_sources(conn, [source], BERLIN, device_names={"mac:1": "Work Mac"})
    settings = influx_settings(tmp_path)

    written = export_influx(conn, settings)

    by_measurement: dict[str, list[str]] = {}
    for item in influx.lines():
        by_measurement.setdefault(item.split(",", 1)[0], []).append(item)
    assert written == len(influx.lines())
    sessions = by_measurement["screentime"]
    assert len(sessions) == 4
    assert {s.rsplit(" ", 1)[1] for s in sessions} >= {
        "1784973600100000",
        "1784973600200000",
    }
    assert any(
        s.startswith(
            "screentime,device=Work\\ Mac,device_id=mac:1,platform=Mac,"
            "app=App,bundle_id=com.example.app,category=Other duration_s=600.0 "
        )
        for s in sessions
    )
    # iPhone: 2026-07-24 (30 min before midnight) and 2026-07-25; Mac: 25th.
    assert len(by_measurement["screentime_daily"]) == 3
    late = next(
        d for d in by_measurement["screentime_daily"] if "late_duration_s=1800.0" in d
    )
    assert "first_activity_s=0i" in late and "last_activity_s=" in late
    assert len(by_measurement["screentime_app_daily"]) == 4
    hours = [h for h in by_measurement["screentime_hourly"] if "hour=00" in h]
    assert len(hours) == 1 and "weekday=6" in hours[0]  # Saturday
    # One contiguous delete per device covers both iPhone days.
    assert sorted((d["start"], d["predicate"]) for d in influx.deletes()) == [
        ("2026-07-23T22:00:00+00:00", 'device_id="phone-1"'),
        ("2026-07-24T22:00:00+00:00", 'device_id="mac:1"'),
    ]

    influx.calls.clear()
    assert export_influx(conn, settings) == 0
    assert influx.calls == []

    source.events = [event("2026-07-25T12:00:00+00:00", 60, device=MAC)]
    sync_sources(conn, [source], BERLIN)
    export_influx(conn, settings)
    # Only the rebuilt day goes out again (for every device on it).
    assert {d["start"] for d in influx.deletes()} == {"2026-07-24T22:00:00+00:00"}
    sessions = [s for s in influx.lines() if s.startswith("screentime,")]
    assert len(sessions) == 4


def test_backfill_resends_from_date_without_moving_cursor(conn, tmp_path, influx):
    sync_sources(
        conn,
        [FakeSource([event("2026-07-20T10:00:00+00:00"), event()])],
        BERLIN,
    )
    export_influx(conn, influx_settings(tmp_path), from_date=date(2026, 7, 25))
    assert [d["start"] for d in influx.deletes()] == ["2026-07-24T22:00:00+00:00"]
    assert conn.execute("SELECT COUNT(*) FROM influx_sync_cursors").fetchone()[0] == 0


def test_influx_failure_does_not_advance_cursor(conn, tmp_path, monkeypatch):
    sync_sources(conn, [FakeSource([event()])], BERLIN)
    monkeypatch.setattr("screentime.influx.requests.post", FakeInflux(500))
    with pytest.raises(RuntimeError):
        export_influx(conn, influx_settings(tmp_path))
    assert conn.execute("SELECT COUNT(*) FROM influx_sync_cursors").fetchone()[0] == 0


def test_export_skipped_without_config(conn, tmp_path):
    assert export_influx(conn, make_settings(tmp_path, timezone=ZoneInfo("UTC"))) == 0
