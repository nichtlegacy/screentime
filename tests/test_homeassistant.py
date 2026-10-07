from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from screentime.config import Settings
from screentime.db import connect, init_db
from screentime.homeassistant import export_home_assistant, slug
from screentime.importer import sync_sources
from screentime.sources import Device, UsageEvent

UTC_ZONE = ZoneInfo("UTC")
PHONE = Device("phone-1", "Alex’s iPhone", "iphone", "fake")
MAC = Device("mac:1", "Büro Mac", "mac", "fake")
OLD = Device("phone-2", "iPhone", "iphone", "fake")


class Source:
    name = "fake"

    def __init__(self, events: list[UsageEvent]) -> None:
        self.events = events

    def devices(self) -> list[Device]:
        return [PHONE, MAC, OLD]

    def read(self, state):
        return self.events, {}


def usage(device: Device, start: datetime, minutes: float, bundle: str) -> UsageEvent:
    return UsageEvent(device.id, bundle, start, start + timedelta(minutes=minutes))


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Alex’s iPhone 15 Pro", "alexs_iphone_15_pro"),
        ("Büro-Mac (Straße)", "buro_mac_strasse"),
        ("  iPad  ", "ipad"),
        ("📱", ""),
    ],
)
def test_slug(name, expected):
    assert slug(name) == expected


def test_posts_v2_sensor_set(tmp_path, monkeypatch):
    conn = connect(tmp_path / "st.db")
    init_db(conn)
    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    old_day = midnight - timedelta(days=3)
    sync_sources(
        conn,
        [
            Source(
                [
                    # 01:00–01:30 late, 04:00–04:10 deep night, 12:00 daytime.
                    usage(
                        PHONE, midnight + timedelta(hours=1), 30, "com.burbn.instagram"
                    ),
                    usage(PHONE, midnight + timedelta(hours=4), 10, "com.example.app"),
                    usage(MAC, midnight + timedelta(hours=12), 60, "com.example.app"),
                    usage(OLD, old_day + timedelta(hours=12), 5, "com.example.app"),
                ]
            )
        ],
        UTC_ZONE,
    )
    posted: dict[str, Any] = {}

    class Response:
        status_code = 201

    def post(url, json, headers, **kwargs):
        assert headers["Authorization"] == "Bearer secret"
        posted[url.removeprefix("http://ha.test/api/states/")] = json
        return Response()

    monkeypatch.setattr("screentime.homeassistant.requests.post", post)
    settings = Settings(
        db_path=tmp_path / "st.db",
        timezone=UTC_ZONE,
        ha_url="http://ha.test",
        ha_token="secret",
        ha_entity_prefix="test_st",
    )

    assert export_home_assistant(conn, settings) == len(posted) == 15

    s = "sensor.test_st"
    total = posted[f"{s}_total"]
    assert total["state"] == 100.0
    attributes = dict(total["attributes"])
    assert (
        attributes.pop("first_activity") == (midnight + timedelta(hours=1)).isoformat()
    )
    assert (
        attributes.pop("last_activity") == (midnight + timedelta(hours=13)).isoformat()
    )
    assert attributes == {
        "friendly_name": "Screen Time Total",
        "icon": "mdi:cellphone-screen",
        "unit_of_measurement": "min",
        "device_class": "duration",
        "state_class": "total",
        "last_reset": midnight.isoformat(),
        "late_night_minutes": 40.0,
        "deep_night_minutes": 10.0,
    }
    assert posted[f"{s}_alexs_iphone"]["state"] == 40.0
    assert posted[f"{s}_alexs_iphone_late_night"]["state"] == 40.0
    assert posted[f"{s}_alexs_iphone_deep_night"]["state"] == 10.0
    assert posted[f"{s}_buro_mac"]["attributes"]["icon"] == "mdi:laptop"
    assert posted[f"{s}_buro_mac_late_night"]["state"] == 0.0
    # Active this week but idle today: reported as 0, not left stale.
    assert posted[f"{s}_iphone"]["state"] == 0
    week_start = midnight - timedelta(days=midnight.weekday())
    assert posted[f"{s}_week"]["state"] == (105.0 if old_day >= week_start else 100.0)
    top = posted[f"{s}_top_app"]
    assert (top["state"], top["attributes"]["minutes"]) == ("App", 70.0)
    assert [a["name"] for a in top["attributes"]["apps"]] == ["App", "Instagram"]
    assert posted[f"{s}_top_apps"]["attributes"]["Instagram"] == 30.0
    by_category = posted[f"{s}_by_category"]
    assert by_category["attributes"]["category_Social"] == 30.0
    assert posted[f"{s}_last_sync"]["attributes"]["device_class"] == "timestamp"
