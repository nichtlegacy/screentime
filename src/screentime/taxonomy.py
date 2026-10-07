"""Bundle id → (display name, category).

Priority, per field: config override (``[apps."id"]``) > shipped
``data/apps.json`` > iTunes lookup cache > heuristic from the bundle id.

``data/apps.json`` holds the category list, the App Store genre → category
map used for lookups, and ``apps``: bundle id → ``[name, category]``.

Bundle ids that would fall back to the heuristic are first looked for among
the apps installed on this Mac (Spotlight, no network), then, if enabled, in
Apple's public iTunes Lookup API (bundle ids and nothing else are sent).
Hits and misses are cached in the ``app_lookup`` table.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from importlib.resources import files
import json
import logging
from pathlib import Path
import plistlib
import re
import sqlite3
import subprocess
import sys
import time
from typing import Any

import requests

from .db import now_utc_iso, transaction

log = logging.getLogger(__name__)

_DATA = json.loads(files("screentime").joinpath("data/apps.json").read_text("utf-8"))
CATEGORIES: list[str] = _DATA["categories"]
GENRES: dict[str, str] = _DATA["genres"]
APPS: dict[str, tuple[str, str]] = {k: (v[0], v[1]) for k, v in _DATA["apps"].items()}
# Heuristic category for a guessed name that matches a shipped app name.
_NAME_CATEGORY = {name: category for name, category in APPS.values()}
_WEB_APP = re.compile(r"com\.(apple\.Safari\.WebApp|google\.Chrome\.app)\.")
_APPLE_SERVICE = re.compile(r"com\.apple\..*(Service|Angel|Agent|UI)$")

# ponytail: process-wide state, fine for a one-shot CLI; pass a taxonomy
# object around if one process ever needs two configs.
OVERRIDES: dict[str, dict[str, Any]] = {}
LOOKUP: dict[str, tuple[str, str]] = {}  # cached iTunes hits

LOOKUP_URL = "https://itunes.apple.com/lookup"
BATCH = 50  # bundle ids per request
MAX_BATCHES = 4  # per run; Apple allows roughly 20 requests per minute
MISS_RETRY = timedelta(days=7)
TIMEOUT = (3.05, 5)

# LSApplicationCategoryType (without "public.app-category.") → category;
# any "*-games" type is Gaming.
APP_TYPES = {
    "developer-tools": "Coding",
    "productivity": "Productivity",
    "business": "Productivity",
    "education": "Productivity",
    "reference": "Productivity",
    "graphics-design": "Productivity",
    "utilities": "Utilities",
    "lifestyle": "Utilities",
    "weather": "Utilities",
    "travel": "Utilities",
    "navigation": "Utilities",
    "music": "Media",
    "video": "Media",
    "photography": "Media",
    "entertainment": "Media",
    "news": "Media",
    "sports": "Media",
    "social-networking": "Social",
    "finance": "Finance",
    "healthcare-fitness": "Health & Fitness",
    "medical": "Health & Fitness",
    "games": "Gaming",
}


def apply_overrides(apps: dict[str, dict[str, Any]]) -> None:
    """Apply ``[apps."bundle.id"]`` config entries (``name``, ``category``)."""
    OVERRIDES.update(apps)


def guess(bundle_id: str) -> tuple[str, str]:
    """Heuristic fallback: name from the bundle id's last component."""
    if _WEB_APP.match(bundle_id):
        return "Web App", "Browser"
    tail = bundle_id.rsplit(".", 1)[-1]
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", tail).replace("-", " ").title()
    name = name or "Unknown"
    if name in _NAME_CATEGORY:
        return name, _NAME_CATEGORY[name]
    return name, "System" if _APPLE_SERVICE.match(bundle_id) else "Other"


def is_known(bundle_id: str) -> bool:
    """True unless the bundle id falls back to the heuristic."""
    return bool(
        bundle_id in OVERRIDES
        or bundle_id in APPS
        or bundle_id in LOOKUP
        or _WEB_APP.match(bundle_id)
    )


def classify(bundle_id: str) -> tuple[str, str]:
    """Return ``(title, category)`` for a bundle id."""
    override = OVERRIDES.get(bundle_id, {})
    name, category = APPS.get(bundle_id) or LOOKUP.get(bundle_id) or guess(bundle_id)
    return (
        str(override.get("name") or name),
        str(override.get("category") or category),
    )


def short_name(track_name: str) -> str:
    """``"Spotify: Music and Podcasts"`` → ``"Spotify"``."""
    return re.split(r":| [-–—|] ", track_name, maxsplit=1)[0].strip() or track_name


def itunes_lookup(
    bundle_ids: list[str], country: str
) -> dict[str, tuple[str, str, str]]:
    """Return lower-cased bundle id → (name, category, genre) for App Store hits.

    Raises ``requests.RequestException`` / ``ValueError`` on network or
    response errors.
    """
    response = requests.get(
        LOOKUP_URL,
        # lang=en_us: English names and genres in every storefront.
        params={"bundleId": ",".join(bundle_ids), "country": country, "lang": "en_us"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    found = {}
    for result in response.json().get("results", []):
        if not result.get("bundleId") or not result.get("trackName"):
            continue
        genres = [result.get("primaryGenreName"), *result.get("genres", [])]
        genre = next((g for g in genres if g in GENRES), "")
        found[result["bundleId"].lower()] = (
            short_name(result["trackName"]),
            GENRES.get(genre, "Other"),
            str(result.get("primaryGenreName") or ""),
        )
    return found


def local_lookup(bundle_ids: list[str]) -> dict[str, tuple[str, str, str]]:
    """Return lower-cased bundle id → (name, category, type) for apps on this Mac.

    Spotlight finds the app bundles; the name is the bundle's display name or
    its file name (what Finder and the Dock show), the category comes from
    its ``LSApplicationCategoryType``. Empty off macOS or without Spotlight.
    """
    ids = [b for b in bundle_ids if '"' not in b]
    if sys.platform != "darwin" or not ids:
        return {}
    query = " || ".join(f'kMDItemCFBundleIdentifier == "{b}"' for b in ids)
    try:
        paths = subprocess.run(
            ["mdfind", query], capture_output=True, text=True, timeout=20
        ).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return {}
    wanted = {b.lower() for b in ids}
    found: dict[str, tuple[str, str, str]] = {}
    # Prefer installed copies over ones in Downloads or build folders.
    for path in sorted(paths, key=lambda p: not p.startswith("/Applications/")):
        try:
            info = plistlib.loads((Path(path) / "Contents" / "Info.plist").read_bytes())
        except (OSError, ValueError, plistlib.InvalidFileException):
            continue
        bundle_id = str(info.get("CFBundleIdentifier", "")).lower()
        if bundle_id not in wanted or bundle_id in found:
            continue
        kind = str(info.get("LSApplicationCategoryType", "")).removeprefix(
            "public.app-category."
        )
        category = "Gaming" if kind.endswith("-games") else APP_TYPES.get(kind, "Other")
        name = str(info.get("CFBundleDisplayName") or Path(path).stem)
        found[bundle_id] = (name, category, f"mac:{kind}" if kind else "mac")
    return found


def load_cache(conn: sqlite3.Connection) -> None:
    LOOKUP.clear()
    for row in conn.execute(
        "SELECT bundle_id, name, category FROM app_lookup WHERE name IS NOT NULL"
    ):
        LOOKUP[row[0]] = (row[1], row[2])


def lookup_missing(conn: sqlite3.Connection, country: str | None) -> set[str]:
    """Name stored bundle ids that would fall back to the heuristic.

    Most-used first, at most ``BATCH * MAX_BATCHES`` per call: installed Mac
    apps first, then (with ``country``) the App Store. Misses are retried
    after ``MISS_RETRY``; network errors are logged, never raised, and leave
    nothing cached. Returns the bundle ids that resolved.
    """
    if country is None and sys.platform != "darwin":
        return set()
    load_cache(conn)
    retry_before = (datetime.now(UTC) - MISS_RETRY).isoformat()
    candidates = [
        row[0]
        for row in conn.execute(
            """SELECT bundle_id FROM screen_events
            WHERE bundle_id NOT IN (SELECT bundle_id FROM app_lookup WHERE checked_at > ?)
            GROUP BY bundle_id ORDER BY SUM(duration_s) DESC""",
            (retry_before,),
        )
        if not is_known(row[0])
    ][: BATCH * MAX_BATCHES]
    found = local_lookup(candidates)
    checked = [b for b in candidates if b.lower() in found]
    remaining = [b for b in candidates if b.lower() not in found]
    if country is None:  # misses stay open for the App Store once it is on
        remaining = []
    for start in range(0, len(remaining), BATCH):
        batch = remaining[start : start + BATCH]
        if start:
            time.sleep(1)
        try:
            hits = itunes_lookup(batch, str(country))
            missing = [b for b in batch if b.lower() not in hits]
            if missing and country != "us":  # the US store carries most apps
                hits |= itunes_lookup(missing, "us")
        except Exception as exc:  # a lookup must never fail a sync
            log.warning("app lookup failed, retrying next run: %s", exc)
            break
        found |= hits
        checked += batch
    resolved: set[str] = set()
    now = now_utc_iso()
    with transaction(conn):
        for bundle_id in checked:
            name, category, genre = found.get(bundle_id.lower(), (None, None, None))
            conn.execute(
                """INSERT INTO app_lookup(bundle_id, name, category, genre, checked_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(bundle_id) DO UPDATE SET name=excluded.name,
                  category=excluded.category, genre=excluded.genre,
                  checked_at=excluded.checked_at""",
                (bundle_id, name, category, genre, now),
            )
            if name:
                resolved.add(bundle_id)
    load_cache(conn)
    return resolved
