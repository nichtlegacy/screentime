from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import sqlite3

from .config import Settings, integration_complete
from .homeassistant import export_home_assistant
from .influx import export_influx


@dataclass
class ExportResult:
    points_written: int = 0
    influx_status: str = "skipped"
    ha_status: str = "skipped"
    errors: list[str] = field(default_factory=list)


def export_all(
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    from_date: date | None = None,
) -> ExportResult:
    result = ExportResult()
    if integration_complete(settings, "influx"):
        try:
            result.points_written = export_influx(conn, settings, from_date=from_date)
            result.influx_status = "success"
        except Exception as exc:
            result.influx_status = "failed"
            result.errors.append(f"InfluxDB: {exc}")
    if integration_complete(settings, "ha") and from_date is None:
        try:
            export_home_assistant(conn, settings)
            result.ha_status = "success"
        except Exception as exc:
            result.ha_status = "failed"
            result.errors.append(f"Home Assistant: {exc}")
    return result
