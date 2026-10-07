"""Read-only MCP server over the local Screen Time database (`screentime mcp`).

Needs the optional ``mcp`` extra. Every tool opens SQLite read-only per call,
so the server can run next to the 15-minute sync.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from importlib.metadata import version
import sqlite3
from typing import Annotated, Any, Literal, cast

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field
from typing_extensions import TypedDict  # pydantic needs it on Python < 3.12

from . import queries
from .config import Settings

INSTRUCTIONS = """\
Apple Screen Time of the user's iPhone, iPad and Mac, read from a local
database that syncs every 15 minutes. Durations are whole seconds (`*_s`).
Days are local calendar dates (YYYY-MM-DD) in the user's timezone; date
ranges are inclusive. Late night is usage between 00:00 and 06:00 local time,
deep night the 03:00-06:00 part; it counts towards the day it happens on (the
night from Friday to Saturday is Saturday). Weeks are ISO weeks (Monday to
Sunday). Call list_devices first when the user names a device. Without dates,
tools cover the last 7 days including today."""

READ_ONLY = ToolAnnotations(
    read_only_hint=True, idempotent_hint=True, open_world_hint=False
)

PeriodArg = Annotated[
    Literal[
        "today",
        "yesterday",
        "this_week",
        "last_week",
        "last_7_days",
        "last_30_days",
    ],
    Field(
        description="Named period; ignored when start_date or end_date is set. "
        "Weeks are ISO weeks (Monday-Sunday)."
    ),
]
StartArg = Annotated[
    date | None,
    Field(
        description="First local day, inclusive (YYYY-MM-DD). "
        "Default: 6 days before end_date."
    ),
]
EndArg = Annotated[
    date | None,
    Field(description="Last local day, inclusive (YYYY-MM-DD). Default: today."),
]
DeviceArg = Annotated[
    str | None,
    Field(
        description="Device name or device_id from list_devices "
        "(case-insensitive). Default: all devices combined."
    ),
]

Range = TypedDict("Range", {"from": str, "to": str})


class Device(TypedDict):
    """A device whose Screen Time is stored; total_s covers all stored days."""

    device_id: str
    name: str
    platform: str
    source: str
    first_day: str | None
    last_day: str | None
    total_s: int


class DeviceList(TypedDict):
    devices: list[Device]


class DeviceTotal(TypedDict):
    device_id: str
    name: str
    platform: str
    total_s: int
    share_pct: float


class Day(TypedDict):
    """One local day; first/last activity are ISO timestamps with offset."""

    day: str
    total_s: int
    late_s: int
    deep_night_s: int
    first_activity: str | None
    last_activity: str | None
    devices: dict[str, int]


class App(TypedDict):
    bundle_id: str
    name: str
    category: str
    total_s: int
    sessions: int
    share_pct: float


class Category(TypedDict):
    category: str
    total_s: int
    share_pct: float


class Night(TypedDict):
    day: str
    late_s: int
    deep_night_s: int


class LateNight(TypedDict):
    late_s: int
    deep_night_s: int
    nights_with_late_use: int
    worst_night: Night | None
    nights: list[Night]


Period = TypedDict(
    "Period",
    {"from": str, "to": str, "days": int, "elapsed_days": int, "timezone": str},
)
Previous = TypedDict(
    "Previous",
    {"from": str, "to": str, "total_s": int, "change_pct": float | None},
)


class Summary(TypedDict):
    """Report for a period. previous is the same span shifted back (cut to elapsed days)."""

    period: Period
    device: str | None
    total_s: int
    avg_per_day_s: int
    active_days: int
    first_activity: str | None
    last_activity: str | None
    previous: Previous | None
    devices: list[DeviceTotal]
    daily: list[Day]
    top_apps: list[App]
    categories: list[Category]
    late_night: LateNight
    hourly_s: list[int]


class DailyUsage(Range):
    days: list[Day]


class TopApps(Range):
    apps: list[App]


class Categories(Range):
    categories: list[Category]


class LateNightRange(Range, LateNight):
    pass


def create_server(settings: Settings) -> MCPServer:
    tz = settings.timezone
    server: MCPServer = MCPServer(
        "screentime",
        title="Screen Time",
        instructions=INSTRUCTIONS,
        version=version("screentime-exporter"),
    )

    @contextmanager
    def database() -> Iterator[sqlite3.Connection]:
        """Open the database; turn expected failures into tool errors."""
        try:
            conn = queries.open_db(settings.db_path)
        except (FileNotFoundError, sqlite3.Error) as exc:
            raise ToolError(str(exc)) from exc
        try:
            yield conn
        except (LookupError, ValueError, sqlite3.Error) as exc:
            raise ToolError(str(exc)) from exc
        finally:
            conn.close()

    def resolve(
        period: str, start: date | None, end: date | None
    ) -> tuple[date, date, Range]:
        first, last = queries.resolve_range(datetime.now(tz).date(), period, start, end)
        return first, last, {"from": first.isoformat(), "to": last.isoformat()}

    @server.tool(title="List devices", annotations=READ_ONLY)
    def list_devices() -> DeviceList:
        """List the iPhones, iPads and Macs with stored Screen Time.

        Use the returned name or device_id as the `device` argument of other tools.
        """
        with database() as conn:
            return {"devices": cast(list[Device], queries.list_devices(conn))}

    @server.tool(title="Screen time summary", annotations=READ_ONLY)
    def get_summary(
        period: PeriodArg = "last_7_days",
        start_date: StartArg = None,
        end_date: EndArg = None,
        device: DeviceArg = None,
        top_n: Annotated[
            int, Field(ge=1, le=50, description="Number of top apps to return.")
        ] = 10,
    ) -> Summary:
        """Full screen-time report for a period: total and average per day,
        change vs. the previous period, split per device, per-day totals,
        top apps, categories, late-night use and the hour-of-day profile
        (hourly_s, index 0-23). Best first call for questions like
        "how was my screen time last week?"."""
        with database() as conn:
            first, last, _ = resolve(period, start_date, end_date)
            return cast(
                Summary, queries.summary(conn, tz, first, last, device, top=top_n)
            )

    @server.tool(title="Daily usage", annotations=READ_ONLY)
    def get_daily_usage(
        period: PeriodArg = "last_7_days",
        start_date: StartArg = None,
        end_date: EndArg = None,
        device: DeviceArg = None,
    ) -> DailyUsage:
        """Screen time per day (zero-filled), with late-night seconds,
        first/last activity and a per-device split. Use for trends and
        comparing specific days."""
        with database() as conn:
            first, last, span = resolve(period, start_date, end_date)
            days = queries.daily_usage(conn, tz, first, last, device)
            return {**span, "days": cast(list[Day], days)}

    @server.tool(title="Top apps", annotations=READ_ONLY)
    def get_top_apps(
        period: PeriodArg = "last_7_days",
        start_date: StartArg = None,
        end_date: EndArg = None,
        device: DeviceArg = None,
        limit: Annotated[
            int, Field(ge=1, le=100, description="Maximum number of apps.")
        ] = 10,
    ) -> TopApps:
        """Apps ranked by screen time, with category, number of sessions and
        share of the period's total."""
        with database() as conn:
            first, last, span = resolve(period, start_date, end_date)
            apps = queries.top_apps(conn, first, last, device, limit)
            return {**span, "apps": cast(list[App], apps)}

    @server.tool(title="Categories", annotations=READ_ONLY)
    def get_categories(
        period: PeriodArg = "last_7_days",
        start_date: StartArg = None,
        end_date: EndArg = None,
        device: DeviceArg = None,
    ) -> Categories:
        """Screen time per app category (Social, Entertainment, Productivity, ...)
        with share of the total."""
        with database() as conn:
            first, last, span = resolve(period, start_date, end_date)
            rows = queries.categories(conn, first, last, device)
            return {**span, "categories": cast(list[Category], rows)}

    @server.tool(title="Late-night usage", annotations=READ_ONLY)
    def get_late_night(
        period: PeriodArg = "last_7_days",
        start_date: StartArg = None,
        end_date: EndArg = None,
        device: DeviceArg = None,
    ) -> LateNightRange:
        """Usage between 00:00 and 06:00 (late) and 03:00-06:00 (deep night):
        totals, number of nights, the worst night and every night with use.
        A night is reported on the day it ends (Friday night = Saturday)."""
        with database() as conn:
            first, last, span = resolve(period, start_date, end_date)
            data: dict[str, Any] = queries.late_night(conn, tz, first, last, device)
            return cast(LateNightRange, {**span, **data})

    @server.resource(
        "screentime://today",
        name="today",
        title="Screen time today",
        description="Summary of today's screen time (same shape as get_summary).",
        mime_type="application/json",
    )
    def today() -> dict[str, Any]:
        day = datetime.now(tz).date()
        conn = queries.open_db(settings.db_path)
        try:
            return queries.summary(conn, tz, day, day)
        finally:
            conn.close()

    return server


def serve(
    settings: Settings, *, http: bool = False, host: str = "127.0.0.1", port: int = 8765
) -> None:
    server = create_server(settings)
    if http:
        server.run("streamable-http", host=host, port=port)
    else:
        server.run()
