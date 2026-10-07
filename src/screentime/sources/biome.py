"""iPhone/iPad app focus intervals from Biome (``App.InFocus/remote``).

Each remote device that syncs through iCloud gets a directory
``~/Library/Biome/streams/restricted/App.InFocus/remote/<device-uuid>/`` of
SEGB segment files, named by their creation time in microseconds since
2001-01-01. Every written record is a protobuf ``AppInFocus`` event:

* field 3 (varint)  1 = gained focus, 0 = lost focus
* field 4 (double)  CFAbsoluteTime of the transition (seconds since 2001, UTC)
* field 6 (string)  bundle id

Pruned records stay in the file as zeroed "deleted" entries and are skipped.
``App.InFocus/local`` is this Mac and is ignored here (see ``knowledgec``).

Intervals are stitched from transitions: a gain opens an interval, a loss of
the same bundle or a gain of another bundle closes it. The interval that is
still open at the end of the stream is not emitted; it is emitted once a
later run sees it closed, with the same start, so upserts dedupe.

State per device: ``file`` (newest segment seen), ``carry`` (interval open at
the start of that segment, or null) and ``cursor`` (end of the newest emitted
interval, CFAbsoluteTime). Older segments are complete, so a run re-reads only
the newest known segment and anything after it.
"""

from __future__ import annotations

import logging
import sqlite3
import struct
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from screentime._vendor import ccl_segb
from screentime.sources import Device, Platform, UsageEvent, model_name

log = logging.getLogger(__name__)

STREAM = Path("Biome/streams/restricted/App.InFocus/remote")
SYNC_DB = Path("Biome/sync/sync.db")
KNOWLEDGE_DB = Path("Application Support/Knowledge/knowledgeC.db")
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
PLATFORM_IOS = 2  # DevicePeer.platform for iOS/iPadOS (4 = macOS, 5 = tvOS)
WRITTEN = 1  # SEGB entry state; 3 = deleted, 4 = empty

Transition = tuple[float, str, bool]  # (cf time, bundle id, gained focus)
Open = tuple[str, float]  # (bundle id, start cf time)


def _query(db: Path, sql: str) -> list[tuple[Any, ...]]:
    # immutable=1: never touch the -wal/-shm files of a database Apple owns.
    uri = db.absolute().as_uri() + "?mode=ro&immutable=1"
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            return conn.execute(sql).fetchall()
    except sqlite3.Error as exc:
        log.warning("cannot read %s: %s", db, exc)
        return []


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = buf[i]
        i += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, i
        shift += 7


def parse_transition(data: bytes) -> Transition | None:
    """Decode the fields we need from one ``AppInFocus`` protobuf record."""
    fields: dict[int, Any] = {}
    i = 0
    try:
        while i < len(data):
            key, i = _varint(data, i)
            number, wire = key >> 3, key & 7
            if wire == 0:
                fields[number], i = _varint(data, i)
            elif wire == 1:
                fields[number] = struct.unpack_from("<d", data, i)[0]
                i += 8
            elif wire == 2:
                size, i = _varint(data, i)
                fields[number] = data[i : i + size]
                i += size
            elif wire == 5:
                i += 4
            else:
                return None
        if i != len(data):
            return None
        bundle = fields.get(6, b"").decode()
    except (IndexError, struct.error, UnicodeDecodeError):
        return None
    cf = fields.get(4)
    if not bundle or not isinstance(cf, float):
        return None
    return cf, bundle, fields.get(3) == 1


def read_segment(path: Path) -> Iterator[Transition]:
    """Yield transitions from one SEGB file; stop quietly at corruption."""
    try:
        for record in ccl_segb.read_segb_file(path):
            if record.state != WRITTEN or not record.crc_passed:
                continue
            if (transition := parse_transition(record.data)) is not None:
                yield transition
    except (OSError, ValueError, struct.error) as exc:
        log.warning("stopped reading %s: %s", path, exc)


def stitch(
    transitions: Iterator[Transition], carry: Open | None
) -> tuple[list[tuple[str, float, float]], Open | None]:
    """Turn focus transitions into closed ``(bundle, start, end)`` intervals."""
    current = carry
    closed: list[tuple[str, float, float]] = []
    for cf, bundle, gained in transitions:
        if current is not None and current[0] == bundle and gained:
            continue  # repeated gain: keep the original start
        if current is not None and (gained or current[0] == bundle):
            if cf > current[1]:
                closed.append((current[0], current[1], cf))
            current = None
        if gained:
            current = (bundle, cf)
    return closed, current


def _cf_to_utc(cf: float) -> datetime:
    return APPLE_EPOCH + timedelta(seconds=cf)


def _segments(directory: Path) -> list[Path]:
    try:
        files = [p for p in directory.iterdir() if p.is_file() and p.name.isdigit()]
    except OSError as exc:
        log.warning("cannot list %s: %s", directory, exc)
        return []
    return sorted(files, key=lambda p: int(p.name))


class BiomeSource:
    name = "biome"

    def __init__(self, library: Path) -> None:
        self.library = Path(library)

    def devices(self) -> list[Device]:
        """iOS/iPadOS peers from ``sync.db`` that have an App.InFocus stream."""
        streams = self.library / STREAM
        if not streams.is_dir():
            return []
        peers = {
            row[0]: row[1:]
            for row in _query(
                self.library / SYNC_DB,
                "SELECT device_identifier, ids_device_identifier, name, platform"
                " FROM DevicePeer",
            )
        }
        # knowledgeC knows the hardware model (e.g. "iPhone16,1") by IDS id.
        models = dict(
            _query(
                self.library / KNOWLEDGE_DB,
                "SELECT ZRAPPORTID, ZMODEL FROM ZSYNCPEER"
                " WHERE ZRAPPORTID IS NOT NULL AND ZMODEL IS NOT NULL",
            )
        )
        devices = []
        for directory in sorted(streams.iterdir()):
            if directory.name not in peers:
                continue  # unknown peer: could be an Apple TV
            ids, name, platform_code = peers[directory.name]
            model = str(models.get(ids) or "")
            platform: Platform = (
                "iphone"
                if model.startswith("iPhone")
                else "ipad"
                if model.startswith("iPad")
                else "unknown"
            )
            if platform == "unknown" and platform_code != PLATFORM_IOS:
                continue
            # The short id keeps two unnamed devices of one model apart.
            label = (
                name or f"{model_name(model) or 'iOS device'} ({directory.name[:8]})"
            )
            devices.append(Device(directory.name, label, platform, self.name, model))
        return devices

    def read(
        self, state: dict[str, Any] | None
    ) -> tuple[list[UsageEvent], dict[str, Any]]:
        state = dict(state or {})
        events: list[UsageEvent] = []
        for device in self.devices():
            prev = state.get(device.id) or {}
            files = _segments(self.library / STREAM / device.id)
            last = int(prev.get("file") or -1)
            carry: Open | None = None
            if any(int(p.name) == last for p in files):
                if prev.get("carry"):
                    carry = (prev["carry"][0], float(prev["carry"][1]))
                files = [p for p in files if int(p.name) >= last]
            else:
                files = [p for p in files if int(p.name) > last]
            if not files:
                continue
            cursor = prev.get("cursor")
            for path in files:
                newest_carry = carry
                closed, carry = stitch(read_segment(path), carry)
                for bundle, start, end in closed:
                    if cursor is not None and start < cursor:
                        continue  # emitted by an earlier run
                    events.append(
                        UsageEvent(
                            device.id, bundle, _cf_to_utc(start), _cf_to_utc(end)
                        )
                    )
                    cursor = end
            state[device.id] = {
                "file": files[-1].name,
                "carry": list(newest_carry) if newest_carry else None,
                "cursor": cursor,
            }
        return events, state
