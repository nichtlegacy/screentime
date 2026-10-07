"""Screen Time sources.

A source turns one Apple data store into `UsageEvent`s:

* ``biome``      — iPhone/iPad usage synced to this Mac via iCloud
                   (``~/Library/Biome/streams/restricted/App.InFocus/remote``)
* ``knowledgec`` — this Mac's own usage (``knowledgeC.db``, ``/app/usage``)

Sources are read-only and stateless between runs: the importer stores the
opaque ``state`` a source returns and hands it back on the next run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from importlib.resources import files
import json
from typing import Any, Literal, Protocol

Platform = Literal["iphone", "ipad", "mac", "unknown"]


@dataclass(frozen=True)
class Device:
    id: str  # stable: Biome device UUID, or "mac:<IOPlatformUUID>"
    name: str  # best-effort human name; the user can override it in config
    platform: Platform
    source: str  # "biome" | "knowledgec"
    model: str = ""  # hardware identifier, e.g. "iPhone17,1", if known


_MODELS: dict[str, str] = json.loads(
    files("screentime").joinpath("data/models.json").read_text("utf-8")
)


def model_name(model: str) -> str:
    """``"iPhone18,1"`` → ``"iPhone 17 Pro"``; unknown identifiers pass through."""
    return _MODELS.get(model, model)


@dataclass(frozen=True)
class UsageEvent:
    device_id: str
    bundle_id: str
    start: datetime  # timezone-aware, UTC
    end: datetime  # timezone-aware, UTC, end > start


class Source(Protocol):
    name: str

    def devices(self) -> list[Device]: ...

    def read(
        self, state: dict[str, Any] | None
    ) -> tuple[list[UsageEvent], dict[str, Any]]:
        """Return events at or after ``state`` plus the new JSON-serialisable state.

        Returning events that were already imported is fine: the importer
        upserts by (device_id, start, bundle_id).
        """
        ...
