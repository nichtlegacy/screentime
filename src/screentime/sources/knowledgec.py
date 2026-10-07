"""This Mac's own app usage from ``knowledgeC.db`` (stream ``/app/usage``).

Read strategy: knowledgeC is a live WAL database owned by ``knowledged``. We copy
the main file and its ``-wal`` into a private temp dir and query the copy, so we
never lock, checkpoint or create files next to the original. The ``-shm`` is not
copied; SQLite rebuilds it from the WAL copy. A checkpoint running during the
copy would make db and WAL disagree, so the copy is retried until the main
file's mtime/size is unchanged across it. (``immutable=1`` would ignore the WAL
and miss the newest rows; ``mode=ro`` still takes locks in, and may create, the
original ``-shm``.)

Only rows without a source device id are emitted: rows synced from other
devices (e.g. an iPhone's ``/app/intents``) carry ``ZSOURCE.ZDEVICEID``, and
iPhone/iPad usage comes from the Biome source instead.

State is ``{"created": <Core Data seconds>}``, the newest ``ZCREATIONDATE``
seen. Rows are written when an interval ends, sometimes minutes later, so the
cursor is the creation date (not the end) and re-reads ``OVERLAP_SECONDS``.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from functools import cached_property
from pathlib import Path
from typing import Any

from . import Device, UsageEvent

log = logging.getLogger(__name__)

CORE_DATA_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
OVERLAP_SECONDS = 3600.0
COPY_ATTEMPTS = 3

QUERY = """
SELECT o.ZVALUESTRING, o.ZSTARTDATE, o.ZENDDATE, o.ZCREATIONDATE
FROM ZOBJECT o
LEFT JOIN ZSOURCE s ON s.Z_PK = o.ZSOURCE
WHERE o.ZSTREAMNAME = '/app/usage'
  AND s.ZDEVICEID IS NULL
  AND o.ZCREATIONDATE > ?
ORDER BY o.ZCREATIONDATE
"""


def _run(cmd: list[str]) -> str:
    """Command output, or "" if it is unavailable (e.g. not on macOS)."""
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=10, check=True
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _to_utc(seconds: float) -> datetime:
    return CORE_DATA_EPOCH + timedelta(seconds=seconds)


class KnowledgeCSource:
    name = "knowledgec"

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    @cached_property
    def _device(self) -> Device | None:
        # SCREENTIME_MAC_UUID lets a copied knowledgeC.db be read off-Mac (dev/tests).
        uuid = os.environ.get("SCREENTIME_MAC_UUID")
        if not uuid:
            match = re.search(
                r'"IOPlatformUUID"\s*=\s*"([^"]+)"',
                _run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"]),
            )
            if not match:
                log.warning("knowledgec: no IOPlatformUUID (not macOS?); skipping")
                return None
            uuid = match.group(1)
        name = _run(["scutil", "--get", "ComputerName"]).strip() or "Mac"
        return Device(id=f"mac:{uuid}", name=name, platform="mac", source=self.name)

    def devices(self) -> list[Device]:
        return [self._device] if self._device else []

    def read(
        self, state: dict[str, Any] | None
    ) -> tuple[list[UsageEvent], dict[str, Any]]:
        state = dict(state or {})
        device = self._device
        if device is None:
            return [], state
        cursor = float(state.get("created", 0.0))
        try:
            rows = self._query(cursor - OVERLAP_SECONDS)
        except (OSError, sqlite3.Error) as exc:
            log.warning("knowledgec: cannot read %s: %s", self.db_path, exc)
            return [], state

        events = []
        for bundle_id, start, end, created in rows:
            cursor = max(cursor, created)
            # Zero-length rows exist and often share their start with a real
            # row; they would collide in the (device, start, bundle) upsert.
            if bundle_id and end > start:
                events.append(
                    UsageEvent(device.id, bundle_id, _to_utc(start), _to_utc(end))
                )
        state["created"] = cursor
        return events, state

    def _query(self, since: float) -> list[tuple[str, float, float, float]]:
        wal = self.db_path.with_name(self.db_path.name + "-wal")
        error: Exception | None = None
        for _ in range(COPY_ATTEMPTS):
            with tempfile.TemporaryDirectory(prefix="screentime-kc-") as tmp:
                copy = Path(tmp) / "knowledgeC.db"
                before = _signature(self.db_path)
                shutil.copyfile(self.db_path, copy)
                if wal.exists():
                    shutil.copyfile(wal, copy.with_name(copy.name + "-wal"))
                if _signature(self.db_path) != before:
                    error = OSError("knowledgeC.db changed while copying")
                    continue
                conn = sqlite3.connect(copy)
                try:
                    return list(conn.execute(QUERY, (since,)))
                except sqlite3.DatabaseError as exc:
                    error = exc
                finally:
                    conn.close()
        assert error is not None
        raise error


def _signature(path: Path) -> tuple[int, int]:
    st = path.stat()
    return st.st_mtime_ns, st.st_size
