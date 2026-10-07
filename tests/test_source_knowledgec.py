import itertools
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from screentime.sources import Device
from screentime.sources import knowledgec as kc

UUID = "00000000-1111-2222-3333-444444444444"
IOREG = f'  | "IOPlatformUUID" = "{UUID}"\n  | "serial-number" = <00>\n'
REAL_RUN = kc._run
T0 = 800_000_000.0  # Core Data seconds, 2026-05-08


@pytest.fixture(autouse=True)
def fake_mac(monkeypatch):
    outputs = {"ioreg": IOREG, "scutil": "Test Mac\n"}
    monkeypatch.setattr(kc, "_run", lambda cmd: outputs[cmd[0]])
    return outputs


def make_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=wal")
    conn.execute("PRAGMA wal_autocheckpoint=0")  # keep rows in the WAL
    conn.executescript(
        """
        CREATE TABLE ZSOURCE (Z_PK INTEGER PRIMARY KEY, ZBUNDLEID VARCHAR,
                              ZDEVICEID VARCHAR);
        CREATE TABLE ZOBJECT (Z_PK INTEGER PRIMARY KEY, ZSOURCE INTEGER,
                              ZCREATIONDATE TIMESTAMP, ZSTARTDATE TIMESTAMP,
                              ZENDDATE TIMESTAMP, ZSTREAMNAME VARCHAR,
                              ZVALUESTRING VARCHAR);
        INSERT INTO ZSOURCE VALUES (1, 'com.example.app', NULL);
        INSERT INTO ZSOURCE VALUES (2, 'com.example.app', 'AAAAAAAA-PEER');
        """
    )
    return conn


def add(
    conn,
    start,
    end,
    bundle="com.example.app",
    stream="/app/usage",
    source=None,
    created=None,
):
    conn.execute(
        "INSERT INTO ZOBJECT (ZSOURCE, ZCREATIONDATE, ZSTARTDATE, ZENDDATE,"
        " ZSTREAMNAME, ZVALUESTRING) VALUES (?, ?, ?, ?, ?, ?)",
        (source, end if created is None else created, start, end, stream, bundle),
    )
    conn.commit()


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "knowledgeC.db"
    conn = make_db(path)
    yield path, conn
    conn.close()


def test_device_from_ioreg_and_scutil(db):
    source = kc.KnowledgeCSource(db[0])
    assert source.name == "knowledgec"
    assert source.devices() == [
        Device(id=f"mac:{UUID}", name="Test Mac", platform="mac", source="knowledgec")
    ]


def test_off_macos_degrades_to_nothing(db, fake_mac):
    path, conn = db
    add(conn, T0, T0 + 60)
    fake_mac.update(ioreg="", scutil="")
    source = kc.KnowledgeCSource(path)
    assert source.devices() == []
    assert source.read({"created": 1.0}) == ([], {"created": 1.0})


def test_run_returns_empty_for_missing_command():
    assert REAL_RUN(["screentime-no-such-command"]) == ""


def test_converts_core_data_time_to_utc(db):
    path, conn = db
    add(conn, 0.0, 90.5)
    [event], _ = kc.KnowledgeCSource(path).read(None)
    assert event.device_id == f"mac:{UUID}"
    assert event.bundle_id == "com.example.app"
    assert event.start == datetime(2001, 1, 1, tzinfo=timezone.utc)
    assert event.end == datetime(2001, 1, 1, 0, 1, 30, 500000, tzinfo=timezone.utc)


def test_filters_streams_empty_and_zero_length_rows(db):
    path, conn = db
    add(conn, T0, T0 + 10)
    add(conn, T0, T0, bundle="com.example.app")  # zero length, same start
    add(conn, T0 + 20, T0 + 10)  # negative
    add(conn, T0 + 30, T0 + 40, bundle="")
    add(conn, T0 + 50, T0 + 60, stream="/app/intents")
    events, _ = kc.KnowledgeCSource(path).read(None)
    assert [(e.start, e.end) for e in events] == [(kc._to_utc(T0), kc._to_utc(T0 + 10))]


def test_excludes_rows_synced_from_other_devices(db):
    path, conn = db
    add(conn, T0, T0 + 10, bundle="com.example.local", source=1)
    add(conn, T0 + 20, T0 + 30, bundle="com.example.nosource")
    add(conn, T0 + 40, T0 + 50, bundle="com.example.remote", source=2)
    events, _ = kc.KnowledgeCSource(path).read(None)
    assert [e.bundle_id for e in events] == [
        "com.example.local",
        "com.example.nosource",
    ]


def test_incremental_state_with_overlap(db):
    path, conn = db
    source = kc.KnowledgeCSource(path)
    add(conn, T0, T0 + 10)
    first, state = source.read(None)
    assert state == {"created": T0 + 10}

    # Written late: ended before the cursor, created after it.
    add(conn, T0 - 600, T0 - 300, bundle="com.example.late", created=T0 + 900)
    # Too old for the overlap window: never re-read.
    add(
        conn,
        T0 - 9000,
        T0 - 8000,
        bundle="com.example.old",
        created=T0 - 2 * kc.OVERLAP_SECONDS,
    )
    second, state2 = source.read(state)
    assert state2 == {"created": T0 + 900}
    assert {e.bundle_id for e in second} == {"com.example.app", "com.example.late"}
    # The same row maps to the same event, so the importer's upsert dedupes.
    assert first[0] in second

    assert source.read(state2)[1] == state2
    assert source.read({})[1] == state2  # empty state reads everything


def test_reads_wal_and_leaves_original_untouched(db):
    path, conn = db
    add(conn, T0, T0 + 10)
    assert (path.parent / "knowledgeC.db-wal").stat().st_size > 0
    before = {p.name: p.stat().st_mtime_ns for p in path.parent.iterdir()}

    events, _ = kc.KnowledgeCSource(path).read(None)

    assert len(events) == 1
    assert {p.name: p.stat().st_mtime_ns for p in path.parent.iterdir()} == before


def test_ignores_uncommitted_rows_while_db_is_locked(db):
    path, conn = db
    add(conn, T0, T0 + 10)
    writer = sqlite3.connect(path)
    writer.execute("BEGIN EXCLUSIVE")
    writer.execute(
        "INSERT INTO ZOBJECT (ZCREATIONDATE, ZSTARTDATE, ZENDDATE, ZSTREAMNAME,"
        " ZVALUESTRING) VALUES (?, ?, ?, '/app/usage', 'com.example.pending')",
        (T0 + 30, T0 + 20, T0 + 30),
    )
    try:
        events, _ = kc.KnowledgeCSource(path).read(None)
    finally:
        writer.rollback()
        writer.close()
    assert [e.bundle_id for e in events] == ["com.example.app"]


def test_missing_or_corrupt_db_returns_nothing(tmp_path):
    state = {"created": T0}
    missing = kc.KnowledgeCSource(tmp_path / "knowledgeC.db")
    assert missing.read(state) == ([], state)

    corrupt = tmp_path / "corrupt" / "knowledgeC.db"
    corrupt.parent.mkdir()
    corrupt.write_bytes(b"not a database" * 100)
    assert kc.KnowledgeCSource(corrupt).read(state) == ([], state)


def test_retries_copy_when_db_changes_during_copy(db, monkeypatch):
    path, conn = db
    add(conn, T0, T0 + 10)
    signatures = iter([(0, 0), (1, 1), (1, 1), (1, 1)])
    monkeypatch.setattr(kc, "_signature", lambda _: next(signatures))
    assert len(kc.KnowledgeCSource(path).read(None)[0]) == 1

    ticks = itertools.count()  # always changing: give up gracefully
    monkeypatch.setattr(kc, "_signature", lambda _: (next(ticks), 0))
    assert kc.KnowledgeCSource(path).read({}) == ([], {})


def test_mac_uuid_env_override_reads_off_mac(monkeypatch):
    monkeypatch.setenv("SCREENTIME_MAC_UUID", "TEST-UUID")
    monkeypatch.setattr(kc, "_run", lambda cmd: "")
    assert kc.KnowledgeCSource(Path("unused.db")).devices()[0].id == "mac:TEST-UUID"
