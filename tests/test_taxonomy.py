from __future__ import annotations

from dataclasses import replace
import plistlib
import subprocess
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import requests

from screentime import cli, taxonomy
from screentime.config import Settings, region_of
from screentime.db import connect, init_db
from screentime.importer import sync_sources
from screentime.sources import Device, UsageEvent
from screentime.taxonomy import APPS, CATEGORIES, GENRES, classify, guess, short_name

PHONE = Device("phone-1", "iPhone", "iphone", "fake")


class Source:
    name = "fake"

    def __init__(self, usage: dict[str, float]) -> None:
        self.usage = usage

    def devices(self):
        return [PHONE]

    def read(self, state):
        start = datetime(2026, 7, 25, 10, tzinfo=UTC)
        return [
            UsageEvent(PHONE.id, bundle_id, start, start + timedelta(seconds=seconds))
            for bundle_id, seconds in self.usage.items()
        ], {}


class Response:
    def __init__(self, results: list[dict]) -> None:
        self.results = results

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {"resultCount": len(self.results), "results": self.results}


def app_store(monkeypatch, results: list[dict]) -> list[str]:
    """Serve ``results`` for every lookup; return the requested bundle ids."""
    asked: list[str] = []

    def get(url, params, timeout):
        asked.extend(params["bundleId"].split(","))
        return Response(results)

    monkeypatch.setattr(taxonomy.requests, "get", get)
    return asked


NOTES = {
    "bundleId": "com.example.Notes",
    "trackName": "Example Notes - Write Anything",
    "primaryGenreName": "Productivity",
    "genres": ["Productivity", "Business"],
}


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "screentime.db")
    init_db(connection)
    yield connection
    connection.close()


def test_shipped_mapping_uses_known_categories():
    assert {category for _, category in APPS.values()} <= set(CATEGORIES)
    assert set(GENRES.values()) <= set(CATEGORIES)


def test_priority_override_then_apps_json_then_cache_then_heuristic():
    taxonomy.LOOKUP["com.spotify.client"] = ("Spotify Lookup", "Other")
    taxonomy.LOOKUP["com.example.cached"] = ("Cached", "Finance")
    assert classify("com.spotify.client") == ("Spotify", "Media")
    assert classify("com.example.cached") == ("Cached", "Finance")
    assert classify("com.example.fooBar") == ("Foo Bar", "Other")

    taxonomy.apply_overrides(
        {
            "com.spotify.client": {"category": "Productivity"},
            "com.example.cached": {"name": "Mine"},
        }
    )
    assert classify("com.spotify.client") == ("Spotify", "Productivity")
    assert classify("com.example.cached") == ("Mine", "Finance")


@pytest.mark.parametrize(
    ("bundle_id", "expected"),
    [
        ("com.example.my-app", ("My App", "Other")),
        ("com.google.Chrome.app.abcdef", ("Web App", "Browser")),
        ("com.apple.SomethingUIService", ("Something Uiservice", "System")),
        ("com.example.Spotify", ("Spotify", "Media")),
    ],
)
def test_heuristic_fallback(bundle_id, expected):
    assert guess(bundle_id) == expected


@pytest.mark.parametrize(
    ("track", "name"),
    [
        ("Spotify: Music and Podcasts", "Spotify"),
        ("Example Notes - Write Anything", "Example Notes"),
        ("Plain", "Plain"),
    ],
)
def test_short_name(track, name):
    assert short_name(track) == name


def test_lookup_maps_genres(monkeypatch):
    app_store(
        monkeypatch,
        [
            NOTES,
            {
                "bundleId": "com.example.Game",
                "trackName": "Puzzle",
                "primaryGenreName": "Puzzle",
                "genres": ["Puzzle", "Games"],
            },
            {"bundleId": "com.example.Odd", "trackName": "Odd", "genres": ["Kids"]},
        ],
    )
    found = taxonomy.itunes_lookup(["x"], "us")
    assert found == {
        "com.example.notes": ("Example Notes", "Productivity", "Productivity"),
        "com.example.game": ("Puzzle", "Gaming", "Puzzle"),
        "com.example.odd": ("Odd", "Other", ""),
    }


def test_lookup_caches_hits_and_misses_and_retries_misses_later(conn, monkeypatch):
    sync_sources(
        conn,
        [Source({"com.example.Notes": 60, "com.example.gone": 30})],
        ZoneInfo("UTC"),
    )
    asked = app_store(monkeypatch, [NOTES])

    assert taxonomy.lookup_missing(conn, "us") == {"com.example.Notes"}
    assert asked == ["com.example.Notes", "com.example.gone"]  # most-used first
    assert classify("com.example.Notes") == ("Example Notes", "Productivity")
    rows = dict(conn.execute("SELECT bundle_id, name FROM app_lookup").fetchall())
    assert rows == {"com.example.Notes": "Example Notes", "com.example.gone": None}

    asked.clear()
    assert taxonomy.lookup_missing(conn, "us") == set()
    assert asked == []  # hit is known, miss is fresh

    stale = (datetime.now(UTC) - taxonomy.MISS_RETRY - timedelta(hours=1)).isoformat()
    conn.execute("UPDATE app_lookup SET checked_at=?", (stale,))
    conn.commit()
    taxonomy.lookup_missing(conn, "us")
    assert asked == ["com.example.gone"]


def test_network_failure_caches_nothing_and_sync_succeeds(conn):
    # conftest makes requests.get raise
    stats = sync_sources(
        conn, [Source({"com.example.Notes": 60})], ZoneInfo("UTC"), lookup_country="us"
    )
    assert stats.events_inserted == 1 and not stats.errors
    assert conn.execute("SELECT COUNT(*) FROM app_lookup").fetchone()[0] == 0


def test_sync_lookup_reclassifies_stored_events(conn, monkeypatch):
    app_store(monkeypatch, [NOTES])
    sync_sources(
        conn, [Source({"com.example.Notes": 60})], ZoneInfo("UTC"), lookup_country="us"
    )
    row = conn.execute("SELECT title, category FROM screen_events").fetchone()
    assert tuple(row) == ("Example Notes", "Productivity")
    day = conn.execute("SELECT category FROM daily_category_stats").fetchone()
    assert day[0] == "Productivity"


def test_apps_unknown_lists_heuristic_apps_by_time(tmp_path, monkeypatch, capsys):
    settings = Settings(
        db_path=tmp_path / "screentime.db",
        timezone=ZoneInfo("UTC"),
        lookup_enabled=False,
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    usage = {
        "com.spotify.client": 9000,
        "com.example.big": 7200,
        "com.example.small": 60,
    }
    monkeypatch.setattr(cli, "enabled_sources", lambda _: [Source(usage)])
    assert cli.main(["sync"]) == 0
    capsys.readouterr()

    assert cli.main(["apps", "unknown", "--limit", "1"]) == 0
    out = capsys.readouterr().out
    assert "com.example.big" in out and "2.0" in out
    assert "com.example.small" not in out and "com.spotify.client" not in out


def test_apps_lookup_reports_network_errors(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(taxonomy.requests, "get", fail)
    assert cli.main(["apps", "lookup", "com.example.app"]) == 1
    assert "lookup failed" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("locale", "country"),
    [
        ("de_DE", "de"),
        ("en_US.UTF-8", "us"),
        ("en_GB@rg=dezzzz", "de"),
        ("C.UTF-8", "us"),
        ("", "us"),
    ],
)
def test_storefront_follows_the_region(locale, country):
    assert region_of(locale) == country


def test_lookup_falls_back_to_the_us_store(conn, monkeypatch):
    stores: list[str] = []

    def get(url, params, timeout):
        stores.append(params["country"])
        return Response([NOTES] if params["country"] == "us" else [])

    monkeypatch.setattr(taxonomy.requests, "get", get)
    sync_sources(
        conn, [Source({"com.example.Notes": 60})], ZoneInfo("UTC"), lookup_country="de"
    )
    assert stores == ["de", "us"]
    assert (
        conn.execute("SELECT title FROM screen_events").fetchone()[0] == "Example Notes"
    )


def test_changed_app_overrides_rename_stored_sessions(tmp_path, monkeypatch):
    settings = Settings(
        db_path=tmp_path / "screentime.db",
        timezone=ZoneInfo("UTC"),
        lookup_enabled=False,
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(
        cli, "enabled_sources", lambda _: [Source({"dev.example.tool": 600})]
    )
    assert cli.main(["sync"]) == 0

    renamed = replace(
        settings, apps={"dev.example.tool": {"name": "My Tool", "category": "Coding"}}
    )
    monkeypatch.setattr(cli, "load_settings", lambda: renamed)
    assert cli.main(["sync"]) == 0
    conn = connect(settings.db_path)
    assert tuple(
        conn.execute("SELECT title, category FROM screen_events").fetchone()
    ) == (
        "My Tool",
        "Coding",
    )
    assert conn.execute("SELECT title FROM daily_app_stats").fetchone()[0] == "My Tool"


def fake_app(root, name: str, info: dict) -> str:
    contents = root / f"{name}.app" / "Contents"
    contents.mkdir(parents=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    return str(contents.parent)


def test_installed_mac_apps_are_named_before_the_app_store(conn, tmp_path, monkeypatch):
    tool = fake_app(
        tmp_path,
        "Deploy Tool",
        {
            "CFBundleIdentifier": "dev.example.Tool",
            "CFBundleName": "tool",
            "LSApplicationCategoryType": "public.app-category.developer-tools",
        },
    )
    game = fake_app(
        tmp_path,
        "Emulator",
        {
            "CFBundleIdentifier": "org.example.emu",
            "LSApplicationCategoryType": "public.app-category.role-playing-games",
        },
    )
    monkeypatch.setattr(taxonomy.sys, "platform", "darwin")
    monkeypatch.setattr(
        taxonomy.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, f"{tool}\n{game}\n", ""),
    )
    asked = app_store(monkeypatch, [NOTES])
    usage = {"dev.example.Tool": 60, "org.example.emu": 30, "com.example.Notes": 10}
    sync_sources(conn, [Source(usage)], ZoneInfo("UTC"), lookup_country="us")

    assert asked == ["com.example.Notes"]  # only what the Mac doesn't have
    names = dict(
        conn.execute("SELECT bundle_id, title || '/' || category FROM screen_events")
    )
    assert names == {
        "dev.example.Tool": "Deploy Tool/Coding",
        "org.example.emu": "Emulator/Gaming",
        "com.example.Notes": "Example Notes/Productivity",
    }
