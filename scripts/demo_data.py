"""Fill a fresh SQLite database (and InfluxDB) with synthetic Screen Time.

Three fake devices ("iPhone", "iPad", "MacBook") with a plausible daily rhythm,
some late-night and the odd deep-night session. No real data is involved, so
this is what screenshots and first looks at the dashboard should use.

    uv run python scripts/demo_data.py --days 35
    INFLUX_URL=http://localhost:8086 INFLUX_TOKEN=… INFLUX_ORG=home \\
        uv run python scripts/demo_data.py --bucket screentime_demo

InfluxDB settings come from the usual config file / environment; the bucket
must exist (``influx bucket create -n screentime_demo``). In Grafana pick it
with the dashboard's *Bucket* variable.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
import random
import tempfile
from typing import Any

from screentime.config import integration_complete, load_settings
from screentime.db import connect, init_db
from screentime.importer import sync_sources
from screentime.influx import export_influx
from screentime.sources import Device, UsageEvent

DEVICES = [
    Device("demo-iphone", "iPhone", "iphone", "demo"),
    Device("demo-ipad", "iPad", "ipad", "demo"),
    Device("demo-mac", "MacBook", "mac", "demo"),
]

# bundle id → relative weight
APPS = {
    "demo-iphone": {
        "net.whatsapp.WhatsApp": 14,
        "com.burbn.instagram": 12,
        "com.apple.mobilesafari": 9,
        "com.google.ios.youtube": 8,
        "com.zhiliaoapp.musically": 7,
        "com.apple.MobileSMS": 6,
        "com.spotify.client": 5,
        "com.reddit.Reddit": 5,
        "com.openai.chat": 4,
        "com.apple.mobilemail": 3,
        "com.apple.Maps": 2,
        "com.apple.mobileslideshow": 2,
        "com.apple.weather": 1,
        "com.apple.mobilecal": 1,
        "com.apple.Preferences": 1,
    },
    "demo-ipad": {
        "com.netflix.Netflix": 10,
        "com.google.ios.youtube": 9,
        "com.apple.iBooks": 6,
        "com.apple.mobilesafari": 5,
        "com.supercell.magic": 4,
        "com.apple.mobilenotes": 3,
        "com.spotify.client": 1,
    },
    "demo-mac": {
        "com.microsoft.VSCode": 12,
        "com.google.Chrome": 10,
        "com.apple.Terminal": 6,
        "com.microsoft.skype.teams": 5,
        "com.microsoft.Office.Outlook": 4,
        "md.obsidian": 3,
        "com.anthropic.claude": 3,
        "com.spotify.client": 2,
        "com.apple.finder": 2,
        "com.hnc.Discord": 2,
    },
}

# Mean minutes of use per local hour 0–23: (weekday, weekend).
# fmt: off
PROFILES = {
    "demo-iphone": (
        [6, 2, 0, 0, 0, 0, 3, 14, 10, 5, 4, 5, 15, 8, 4, 5, 6, 10, 12, 12, 13, 16, 18, 12],
        [10, 5, 1, 0, 0, 0, 0, 2, 6, 12, 14, 12, 12, 10, 10, 12, 12, 10, 10, 12, 14, 16, 18, 15],
    ),
    "demo-ipad": (
        [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 6, 15, 20, 15, 5],
        [2, 0, 0, 0, 0, 0, 0, 0, 3, 8, 10, 6, 4, 6, 10, 12, 8, 5, 6, 10, 20, 25, 18, 8],
    ),
    "demo-mac": (
        [0, 0, 0, 0, 0, 0, 0, 0, 20, 45, 50, 45, 15, 35, 50, 50, 45, 30, 8, 3, 10, 12, 6, 2],
        [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 5, 10, 6, 5, 5, 8, 6, 3, 0, 0, 8, 10, 5, 0],
    ),
}
# fmt: on

# Typical session length range in minutes.
SESSION_MINUTES = {"demo-iphone": (0.3, 6), "demo-ipad": (3, 25), "demo-mac": (2, 30)}


def generate(days: int, timezone: Any, seed: int) -> list[UsageEvent]:
    rng = random.Random(seed)
    now = datetime.now(UTC)
    today = now.astimezone(timezone).date()
    events: list[UsageEvent] = []
    for offset in range(days, -1, -1):
        day = today - timedelta(days=offset)
        weekend = day.weekday() >= 5
        mood = rng.uniform(0.6, 1.4)  # some days are just heavier
        for device in DEVICES:
            profile = list(PROFILES[device.id][weekend])
            if device.id == "demo-iphone" and rng.random() < 0.3:
                profile[0] += 25  # doomscrolling past midnight
                profile[1] += 15
                if rng.random() < 0.35:
                    profile[3] += 20  # couldn't sleep
            apps, weights = zip(*APPS[device.id].items())
            low, high = SESSION_MINUTES[device.id]
            for hour, minutes in enumerate(profile):
                budget = rng.gauss(minutes, minutes / 3) * mood * 60
                start = datetime.combine(day, time(hour), timezone) + timedelta(
                    seconds=rng.uniform(0, 600)
                )
                while budget > 0 and start.hour == hour:
                    seconds = min(budget, rng.uniform(low, high) * 60)
                    end = start + timedelta(seconds=seconds)
                    if end > now:
                        break
                    bundle_id = rng.choices(apps, weights)[0]
                    events.append(
                        UsageEvent(
                            device.id,
                            bundle_id,
                            start.astimezone(UTC),
                            end.astimezone(UTC),
                        )
                    )
                    budget -= seconds
                    start = end + timedelta(seconds=rng.expovariate(1 / 240))
    return events


class DemoSource:
    name = "demo"

    def __init__(self, events: list[UsageEvent]) -> None:
        self.events = events

    def devices(self) -> list[Device]:
        return DEVICES

    def read(self, state: dict[str, Any] | None) -> tuple[list[UsageEvent], dict]:
        return self.events, {}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--days", type=int, default=35)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--bucket", default="screentime_demo")
    parser.add_argument(
        "--db", type=Path, help="new SQLite file (default: a temporary one)"
    )
    args = parser.parse_args()
    db = args.db or Path(tempfile.mkdtemp()) / "screentime-demo.db"
    if db.exists():
        parser.error(f"{db} exists; the demo only writes to a new database")
    settings = replace(load_settings(), db_path=db, influx_bucket=args.bucket)
    conn = connect(db)
    init_db(conn)
    events = generate(args.days, settings.timezone, args.seed)
    sync_sources(conn, [DemoSource(events)], settings.timezone)
    print(f"{len(events)} demo events → {db}")
    if integration_complete(settings, "influx"):
        start = datetime.now(settings.timezone).date() - timedelta(days=args.days)
        points = export_influx(conn, settings, from_date=start)
        print(f"{points} points → InfluxDB bucket {args.bucket}")
    else:
        print("InfluxDB not configured; skipped export")


if __name__ == "__main__":
    main()
