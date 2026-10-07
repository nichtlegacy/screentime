"""BiomeSource against synthetic SEGB v2 files and sync databases."""

from __future__ import annotations

import json
import sqlite3
import struct
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from screentime.sources import model_name
from screentime.sources.biome import (
    STREAM,
    BiomeSource,
    parse_transition,
    stitch,
)

PHONE = "00000000-0000-4000-8000-000000000001"
PAD = "00000000-0000-4000-8000-000000000002"
TV = "00000000-0000-4000-8000-000000000003"
STRAY = "00000000-0000-4000-8000-000000000004"
T0 = 800_000_000.0  # CFAbsoluteTime, 2026-05-08
EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


def event(bundle: str, cf: float, gained: bool) -> bytes:
    """Encode an AppInFocus protobuf like Biome does (plus fields we ignore)."""
    name = bundle.encode()
    reason = b"SBWorkspace"
    return (
        bytes([0x0A, len(reason)])
        + reason
        + bytes([0x10, 1, 0x18, int(gained), 0x21])
        + struct.pack("<d", cf)
        + bytes([0x32, len(name)])
        + name
        + bytes([0x68, 1])
    )


def segb(records: list[bytes], deleted: frozenset[int] = frozenset()) -> bytes:
    """Build a SEGB v2 file: header, 4-byte aligned entries, trailer at the end."""
    body = b""
    trailer = b""
    for index, data in enumerate(records):
        # Biome also zeroes pruned records; keeping the data here makes the
        # entry state the only thing that marks them deleted.
        body += struct.pack("<Ii", zlib.crc32(data), 0) + data
        state = 3 if index in deleted else 1
        trailer += struct.pack("<iid", len(body), state, T0)
        body += bytes(-len(body) % 4)
    header = struct.pack("<4sid16s", b"SEGB", len(records), T0, bytes(16))
    return header + body + bytes(64) + trailer


def make_library(tmp_path: Path, with_models: bool = True) -> Path:
    library = tmp_path / "Library"
    for device in (PHONE, PAD, TV, STRAY):
        (library / STREAM / device).mkdir(parents=True)
    (library / STREAM.parent / "local").mkdir()
    sync = library / "Biome/sync/sync.db"
    sync.parent.mkdir(parents=True)
    with sqlite3.connect(sync) as conn:
        conn.execute(
            "CREATE TABLE DevicePeer (device_identifier, ids_device_identifier,"
            " me, name, model, platform, last_sync_date, protocol_version)"
        )
        conn.executemany(
            "INSERT INTO DevicePeer VALUES (?, ?, ?, ?, '', ?, 0, 5)",
            [
                ("00000000-0000-4000-8000-0000000000AA", "", 1, "", 4),  # this Mac
                (PHONE, "IDS-PHONE", 0, "", 2),
                (PAD, "IDS-PAD", 0, "Test iPad", 2),
                (TV, "IDS-TV", 0, "", 5),
                ("00000000-0000-4000-8000-0000000000BB", "IDS-OLD", 0, "", 2),
            ],
        )
    if with_models:
        knowledge = library / "Application Support/Knowledge/knowledgeC.db"
        knowledge.parent.mkdir(parents=True)
        with sqlite3.connect(knowledge) as conn:
            conn.execute("CREATE TABLE ZSYNCPEER (ZRAPPORTID, ZMODEL)")
            conn.executemany(
                "INSERT INTO ZSYNCPEER VALUES (?, ?)",
                [("IDS-PHONE", "iPhone16,1"), ("IDS-PAD", "iPad13,4"), ("X", None)],
            )
    return library


def write(library: Path, device: str, name: int, records: list[bytes]) -> None:
    (library / STREAM / device / str(name)).write_bytes(segb(records))


def at(cf: float) -> datetime:
    return EPOCH + timedelta(seconds=cf)


def snapshot(root: Path) -> dict[str, int]:
    return {str(p): p.stat().st_mtime_ns for p in root.rglob("*")}


def test_parse_transition() -> None:
    assert parse_transition(event("com.example.app", T0, True)) == (
        T0,
        "com.example.app",
        True,
    )
    assert parse_transition(event("com.example.app", T0, False))[2] is False  # type: ignore[index]
    data = event("com.example.app", T0, True)
    assert parse_transition(data[:-5]) is None  # truncated string
    assert parse_transition(bytes(len(data))) is None  # zeroed (deleted) record
    assert parse_transition(b"\x0f\x01") is None  # unsupported wire type


def test_stitch_intervals() -> None:
    transitions = [
        (T0, "a", True),
        (T0 + 5, "a", True),  # repeated gain keeps the start
        (T0 + 10, "b", True),  # switch closes a
        (T0 + 12, "a", False),  # loss of another bundle is ignored
        (T0 + 20, "b", False),
        (T0 + 25, "b", False),  # nothing open
        (T0 + 30, "c", True),
        (T0 + 30, "c", False),  # zero length is dropped
        (T0 + 40, "d", True),
    ]
    closed, open_ = stitch(iter(transitions), None)
    assert closed == [("a", T0, T0 + 10), ("b", T0 + 10, T0 + 20)]
    assert open_ == ("d", T0 + 40)
    closed, open_ = stitch(iter([(T0 + 50, "d", False)]), open_)
    assert closed == [("d", T0 + 40, T0 + 50)] and open_ is None


def test_devices(tmp_path: Path) -> None:
    library = make_library(tmp_path)
    devices = {d.id: d for d in BiomeSource(library).devices()}
    assert set(devices) == {PHONE, PAD}  # no Apple TV, no stream without a peer
    assert devices[PHONE].platform == "iphone"
    assert devices[PHONE].name == f"iPhone 15 Pro ({PHONE[:8]})"
    assert devices[PHONE].model == "iPhone16,1"
    assert (devices[PAD].platform, devices[PAD].name) == ("ipad", "Test iPad")
    assert devices[PAD].source == "biome"

    bare = BiomeSource(make_library(tmp_path / "bare", with_models=False))
    assert {(d.id, d.platform) for d in bare.devices()} == {
        (PHONE, "unknown"),
        (PAD, "unknown"),
    }
    assert BiomeSource(tmp_path / "missing").devices() == []


def test_read_incremental(tmp_path: Path) -> None:
    library = make_library(tmp_path)
    source = BiomeSource(library)
    first = [
        event("com.example.mail", T0, True),
        event("com.example.mail", T0 + 60, False),
        event("com.example.chat", T0 + 100, True),  # still open
    ]
    write(library, PHONE, 1000, first)
    write(library, PAD, 1000, [event("com.example.video", T0, True)])
    (library / STREAM.parent / "local" / "1000").write_bytes(
        segb([event("com.example.mac", T0, True), event("com.example.x", T0 + 9, True)])
    )
    events, state = source.read(None)
    assert [(e.device_id, e.bundle_id, e.start, e.end) for e in events] == [
        (PHONE, "com.example.mail", at(T0), at(T0 + 60))
    ]
    assert events[0].start.tzinfo is timezone.utc
    state = json.loads(json.dumps(state))  # the importer stores it as JSON
    assert source.read(state) == ([], state)

    # The newest segment grows: the open interval closes with its original start.
    write(library, PHONE, 1000, [*first, event("com.example.chat", T0 + 160, False)])
    events, state = source.read(state)
    assert [(e.bundle_id, e.start, e.end) for e in events] == [
        ("com.example.chat", at(T0 + 100), at(T0 + 160))
    ]

    # A new segment continues an interval opened in the previous one.
    first += [
        event("com.example.chat", T0 + 160, False),
        event("com.example.maps", T0 + 200, True),
    ]
    write(library, PHONE, 1000, first)
    events, state = source.read(state)
    assert events == []
    write(library, PHONE, 2000, [event("com.example.maps", T0 + 230, True)])
    events, state = source.read(state)
    assert events == []  # repeated gain: maps is still open since T0 + 200
    write(
        library,
        PHONE,
        2000,
        [
            event("com.example.maps", T0 + 230, True),
            event("com.example.mail", T0 + 260, True),
        ],
    )
    events, state = source.read(state)
    assert [(e.bundle_id, e.start, e.end) for e in events] == [
        ("com.example.maps", at(T0 + 200), at(T0 + 260))
    ]
    assert state[PHONE]["file"] == "2000"
    assert source.read(state)[0] == []

    # A fresh read sees the same intervals with the same starts.
    replay, _ = BiomeSource(library).read(None)
    assert [(e.bundle_id, e.start) for e in replay if e.device_id == PHONE] == [
        ("com.example.mail", at(T0)),
        ("com.example.chat", at(T0 + 100)),
        ("com.example.maps", at(T0 + 200)),
    ]


def test_skips_deleted_and_corrupt_records(tmp_path: Path) -> None:
    library = make_library(tmp_path)
    records = [
        event("com.example.old", T0, True),
        event("com.example.old", T0 + 5, False),
        event("com.example.app", T0 + 10, True),
        event("com.example.app", T0 + 20, False),
    ]
    path = library / STREAM / PHONE / "1000"
    path.write_bytes(segb(records, deleted=frozenset({0, 1})))
    events, _ = BiomeSource(library).read(None)
    assert [e.bundle_id for e in events] == ["com.example.app"]

    raw = bytearray(segb(records))
    raw[70] = ord("X")  # first record still parses ("coX.example.old"), bad CRC
    path.write_bytes(bytes(raw))
    events, _ = BiomeSource(library).read(None)
    assert [e.bundle_id for e in events] == ["com.example.app"]


def test_truncated_and_foreign_files(tmp_path: Path) -> None:
    library = make_library(tmp_path)
    good = [
        event("com.example.app", T0, True),
        event("com.example.app", T0 + 30, False),
    ]
    write(library, PHONE, 1000, good)
    (library / STREAM / PHONE / "2000").write_bytes(segb(good)[:50])  # truncated
    (library / STREAM / PHONE / "3000").write_bytes(b"not a segb file")
    (library / STREAM / PHONE / "4000").write_bytes(b"")
    (library / STREAM / PHONE / ".lock").write_bytes(b"")
    write(library, PAD, 1000, good)
    before = snapshot(library)

    events, state = BiomeSource(library).read(None)
    assert sorted((e.device_id, e.bundle_id) for e in events) == [
        (PHONE, "com.example.app"),
        (PAD, "com.example.app"),
    ]
    assert state[PHONE]["file"] == "4000"
    assert snapshot(library) == before  # read-only: no -shm/-wal, no touched files


def test_model_name() -> None:
    assert model_name("iPhone18,1") == "iPhone 17 Pro"
    assert model_name("iPad16,3") == "iPad Pro 11-inch (M4)"
    assert model_name("iPhone99,9") == "iPhone99,9"
