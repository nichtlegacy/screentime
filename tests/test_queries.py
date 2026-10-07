"""Query layer, `summary`, `dump` and the MCP server on a synthetic database."""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
import io
import json
import os
from pathlib import Path
import sys
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from screentime import cli, queries
from screentime.config import Settings
from screentime.db import connect, init_db
from screentime.importer import sync_sources
from screentime.sources import Device, UsageEvent

BERLIN = ZoneInfo("Europe/Berlin")
PHONE = Device("phone-1", "Phone", "iphone", "fake")
LAPTOP = Device("mac:1", "Laptop", "mac", "fake")
WEEK = (date(2026, 3, 23), date(2026, 3, 29))  # ISO week 13, ends on the DST change


def ev(device: Device, local: str, minutes: float, bundle: str) -> UsageEvent:
    start = datetime.fromisoformat(local).replace(tzinfo=BERLIN)
    return UsageEvent(device.id, bundle, start, start + timedelta(minutes=minutes))


EVENTS = [
    # Tue 23:30 -> Wed 01:30: 30 min on Tuesday, 90 late minutes on Wednesday.
    ev(PHONE, "2026-03-24T23:30", 120, "com.burbn.instagram"),
    ev(LAPTOP, "2026-03-25T10:00", 60, "com.example.editor"),
    # Sun 01:30 CET + 60 real minutes = 03:30 CEST (clocks skip 02:00-03:00).
    ev(PHONE, "2026-03-29T01:30", 60, "com.burbn.instagram"),
    # Previous week, for the comparison.
    ev(PHONE, "2026-03-17T12:00", 90, "com.example.editor"),
]


class Fake:
    name = "fake"

    def devices(self) -> list[Device]:
        return [PHONE, LAPTOP]

    def read(self, state: Any) -> tuple[list[UsageEvent], dict[str, Any]]:
        return EVENTS, {}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    path = tmp_path / "screentime.db"
    conn = connect(path)
    init_db(conn)
    sync_sources(conn, [Fake()], BERLIN)
    conn.close()
    return Settings(db_path=path, timezone=BERLIN)


@pytest.fixture
def conn(settings):
    connection = queries.open_db(settings.db_path)
    yield connection
    connection.close()


def test_daily_usage_splits_midnight_and_devices(conn):
    days = {d["day"]: d for d in queries.daily_usage(conn, BERLIN, *WEEK)}
    assert len(days) == 7 and days["2026-03-23"]["total_s"] == 0
    assert days["2026-03-24"]["total_s"] == 1800
    wed = days["2026-03-25"]
    assert (wed["total_s"], wed["late_s"], wed["deep_night_s"]) == (9000, 5400, 0)
    assert wed["devices"] == {"Phone": 5400, "Laptop": 3600}
    assert wed["first_activity"] == "2026-03-25T00:00:00+01:00"
    assert wed["last_activity"] == "2026-03-25T11:00:00+01:00"
    sun = days["2026-03-29"]
    assert (sun["total_s"], sun["late_s"], sun["deep_night_s"]) == (3600, 3600, 1800)
    assert sun["last_activity"] == "2026-03-29T03:30:00+02:00"


def test_device_filter_by_name_or_id(conn):
    by_name = queries.top_apps(conn, *WEEK, device="laptop")
    assert [a["bundle_id"] for a in by_name] == ["com.example.editor"]
    assert queries.total_seconds(conn, *WEEK, device="phone-1") == 10800
    with pytest.raises(LookupError, match="known devices: Laptop, Phone"):
        queries.total_seconds(conn, *WEEK, device="tablet")


def test_hourly_buckets_follow_local_clock_across_dst(conn):
    sunday = date(2026, 3, 29)
    hours = queries.hourly(conn, BERLIN, sunday, sunday)
    assert hours[1] == 1800 and hours[3] == 1800 and sum(hours) == 3600
    week = queries.hourly(conn, BERLIN, *WEEK)
    assert sum(week) == queries.total_seconds(conn, *WEEK)
    assert week[23] == 1800 + 0 and week[0] == 3600


def test_top_apps_categories_and_late_night(conn):
    apps = queries.top_apps(conn, *WEEK)
    assert [(a["name"], a["total_s"], a["sessions"]) for a in apps][0] == (
        "Instagram",
        10800,
        3,  # the midnight-crossing session counts on both days
    )
    assert apps[0]["share_pct"] == 75.0
    cats = queries.categories(conn, *WEEK)
    assert cats[0]["category"] == "Social" and sum(c["total_s"] for c in cats) == 14400
    late = queries.late_night(conn, BERLIN, *WEEK)
    assert (late["late_s"], late["deep_night_s"], late["nights_with_late_use"]) == (
        9000,
        1800,
        2,
    )
    assert late["worst_night"] == {
        "day": "2026-03-25",
        "late_s": 5400,
        "deep_night_s": 0,
    }


@pytest.mark.parametrize(
    ("period", "expected"),
    [
        ("today", ("2026-03-25", "2026-03-25")),
        ("this_week", ("2026-03-23", "2026-03-29")),
        ("last_week", ("2026-03-16", "2026-03-22")),
        (None, ("2026-03-19", "2026-03-25")),
    ],
)
def test_resolve_range(period, expected):
    start, end = queries.resolve_range(date(2026, 3, 25), period)
    assert (start.isoformat(), end.isoformat()) == expected


def test_resolve_range_rejects_bad_input():
    with pytest.raises(ValueError):
        queries.resolve_range(date(2026, 3, 25), "fortnight")
    with pytest.raises(ValueError):
        queries.resolve_range(date(2026, 3, 25), start=date(2026, 3, 26))


def test_summary_compares_week_to_date(conn):
    data = queries.summary(conn, BERLIN, *WEEK, today=date(2026, 3, 25))
    assert data["period"]["elapsed_days"] == 3
    assert data["avg_per_day_s"] == round(14400 / 3)  # Sunday is in the fixture
    # Mon-Wed vs Mon-Wed of the week before.
    assert data["previous"] == {
        "from": "2026-03-16",
        "to": "2026-03-18",
        "total_s": 5400,
        "change_pct": 166.7,
    }
    assert data["first_activity"] == "2026-03-24T23:30:00+01:00"


@pytest.fixture
def cli_settings(settings, monkeypatch):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    return settings


def test_summary_cli_json_and_renderings(cli_settings, capsys):
    assert cli.main(["summary", "--week", "2026-W13", "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert list(data) == [
        "period",
        "device",
        "total_s",
        "avg_per_day_s",
        "active_days",
        "first_activity",
        "last_activity",
        "previous",
        "devices",
        "daily",
        "top_apps",
        "categories",
        "late_night",
        "hourly_s",
    ]
    assert data["period"]["from"] == "2026-03-23" and data["total_s"] == 14400
    assert data["devices"][0] == {
        "device_id": "phone-1",
        "name": "Phone",
        "platform": "iphone",
        "total_s": 10800,
        "share_pct": 75.0,
    }
    assert data["previous"]["total_s"] == 5400

    assert cli.main(["summary", "--from", "2026-03-24", "--to", "2026-03-25"]) == 0
    text = capsys.readouterr().out
    assert "Total   3h 00m" in text and "Wed 03-25" in text and "00:00-11:00" in text
    assert cli.main(["summary", "--day", "2026-03-25", "--format", "markdown"]) == 0
    assert "| Instagram | Social | 1h 30m |" in capsys.readouterr().out
    assert cli.main(["summary", "--device", "tablet"]) == 2


def test_dump_sessions_csv_and_daily_json(cli_settings, capsys, tmp_path):
    out = tmp_path / "sessions.csv"
    argv = ["dump", "--from", "2026-03-23", "--to", "2026-03-29", "-o", str(out)]
    assert cli.main(argv) == 0
    rows = list(csv.DictReader(io.StringIO(out.read_text())))
    assert [r["start"] for r in rows] == [
        "2026-03-24T23:30:00+01:00",
        "2026-03-25T10:00:00+01:00",
        "2026-03-29T01:30:00+01:00",
    ]
    assert rows[2]["end"] == "2026-03-29T03:30:00+02:00"
    assert rows[0]["device"] == "Phone" and rows[0]["category"] == "Social"

    assert cli.main(["dump", "--daily", "--format", "json", "--device", "Phone"]) == 0
    daily = json.loads(capsys.readouterr().out)
    assert [(r["day"], r["duration_s"]) for r in daily] == [
        ("2026-03-17", 5400.0),
        ("2026-03-24", 1800.0),
        ("2026-03-25", 5400.0),
        ("2026-03-29", 3600.0),
    ]
    assert list(daily[0]) == list(queries.DAILY_FIELDS)


def test_dump_without_database_fails_cleanly(tmp_path, monkeypatch, capsys):
    settings = Settings(db_path=tmp_path / "missing.db", timezone=BERLIN)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    assert cli.main(["dump"]) == 1
    assert "run `screentime sync` first" in capsys.readouterr().err


def test_mcp_command_explains_missing_extra(cli_settings, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "mcp", None)
    monkeypatch.delitem(sys.modules, "screentime.mcp_server", raising=False)
    assert cli.main(["mcp"]) == 1
    assert "screentime-exporter[mcp]" in capsys.readouterr().err


# --- MCP server (skipped without the optional extra) ---


def mcp_call(settings: Settings, calls: list[tuple[str, dict[str, Any]]]) -> list[Any]:
    pytest.importorskip("mcp")
    import anyio
    from mcp import Client

    from screentime.mcp_server import create_server

    async def run() -> list[Any]:
        async with Client(create_server(settings)) as client:
            tools = await client.list_tools()
            results: list[Any] = [tools]
            for name, args in calls:
                results.append(await client.call_tool(name, args))
            results.append(await client.read_resource("screentime://today"))
            return results

    return anyio.run(run)


def test_mcp_tools_in_memory(settings):
    week = {"start_date": "2026-03-23", "end_date": "2026-03-29"}
    tools, devices, summary, daily, apps, cats, late, bad_device, bad_date, today = (
        mcp_call(
            settings,
            [
                ("list_devices", {}),
                ("get_summary", {**week, "top_n": 1}),
                ("get_daily_usage", {**week, "device": "Laptop"}),
                ("get_top_apps", {**week, "limit": 1}),
                ("get_categories", week),
                ("get_late_night", week),
                ("get_summary", {"device": "tablet"}),
                ("get_top_apps", {"start_date": "2026-02-30"}),
            ],
        )
    )
    assert {tool.name for tool in tools.tools} == {
        "list_devices",
        "get_summary",
        "get_daily_usage",
        "get_top_apps",
        "get_categories",
        "get_late_night",
    }
    for tool in tools.tools:
        assert tool.title and tool.description and tool.output_schema
        assert tool.annotations.read_only_hint and not tool.annotations.open_world_hint
    assert [d["name"] for d in devices.structured_content["devices"]] == [
        "Phone",
        "Laptop",
    ]
    assert summary.structured_content["total_s"] == 14400
    assert len(summary.structured_content["top_apps"]) == 1
    assert json.loads(summary.content[0].text)["total_s"] == 14400  # text fallback
    assert [d["total_s"] for d in daily.structured_content["days"]][2] == 3600
    assert apps.structured_content["apps"][0]["name"] == "Instagram"
    assert cats.structured_content["categories"][0]["category"] == "Social"
    assert late.structured_content["worst_night"]["day"] == "2026-03-25"
    assert bad_device.is_error and "known devices" in bad_device.content[0].text
    assert bad_date.is_error
    assert json.loads(today.contents[0].text)["period"]["days"] == 1


def test_mcp_over_stdio(settings, tmp_path):
    pytest.importorskip("mcp")
    import anyio
    from mcp import Client, StdioServerParameters

    config = tmp_path / "config.toml"
    config.write_text(f'db_path = "{settings.db_path}"\ntimezone = "Europe/Berlin"\n')
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "screentime.cli", "mcp"],
        env={**os.environ, "SCREENTIME_CONFIG": str(config)},
    )

    async def run() -> Any:
        async with Client(server) as client:
            return await client.call_tool("list_devices", {})

    result = anyio.run(run)
    assert not result.is_error
    assert len(result.structured_content["devices"]) == 2
