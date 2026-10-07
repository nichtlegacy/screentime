from __future__ import annotations

from contextlib import contextmanager
import fcntl
from pathlib import Path
from typing import Iterator, TextIO


class LockBusyError(RuntimeError):
    pass


def db_lock_path(db_path: Path) -> Path:
    """Return the lock file that serialises screentime runs on one database."""

    return Path(db_path).with_suffix(".lock")


@contextmanager
def process_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle: TextIO = path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LockBusyError("another screentime run is active") from exc
        yield
    finally:
        handle.close()
