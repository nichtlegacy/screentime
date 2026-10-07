"""`screentime summary`, `screentime dump` and `screentime mcp` (CLI side).

Read-only: these commands open the database with ``mode=ro`` and never take
the pipeline lock.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
import csv
from datetime import date, datetime, timedelta
import json
import os
import sqlite3
import sys
from typing import IO, Any

from . import queries
from .config import Settings

BLOCKS = " ▏▎▍▌▋▊▉█"
SPARKS = "▁▂▃▄▅▆▇█"


def hm(seconds: float) -> str:
    minutes = round(seconds / 60)
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


def bar(value: float, peak: float, width: int = 20) -> str:
    eighths = round(8 * width * value / peak) if peak else 0
    return ("█" * (eighths // 8) + BLOCKS[eighths % 8].strip()).ljust(width)


def spark(values: list[int]) -> str:
    peak = max(values) or 1
    return "".join(
        SPARKS[min(7, int(8 * value / peak))] if value else " " for value in values
    )


def clock(iso: str | None, day: str) -> str:
    """Local HH:MM; the midnight that ends ``day`` reads 24:00."""
    if not iso:
        return "--:--"
    moment = datetime.fromisoformat(iso)
    return "24:00" if moment.date().isoformat() > day else moment.strftime("%H:%M")


def weekday(day: str) -> str:
    return date.fromisoformat(day).strftime("%a %m-%d")


def change(previous: dict[str, Any] | None) -> str:
    if not previous:
        return ""
    pct = previous["change_pct"]
    delta = "n/a" if pct is None else f"{pct:+.1f}%"
    return (
        f"{delta} vs {previous['from']}..{previous['to']} ({hm(previous['total_s'])})"
    )


def render_text(data: dict[str, Any], label: str) -> str:
    period, late = data["period"], data["late_night"]
    lines = [
        f"Screen time {period['from']} .. {period['to']}  {label}  [{period['timezone']}]",
        f"Device: {data['device'] or 'all'}",
        "",
        f"Total   {hm(data['total_s'])}   avg {hm(data['avg_per_day_s'])}/day   "
        f"{data['active_days']}/{period['elapsed_days']} active days",
    ]
    if data["previous"]:
        lines.append(f"Change  {change(data['previous'])}")
    lines.append(
        f"First   {(data['first_activity'] or '-')[:16].replace('T', ' ')}   "
        f"Last {(data['last_activity'] or '-')[:16].replace('T', ' ')}"
    )

    def table(title: str, rows: Iterable[tuple[str, float, str]]) -> None:
        rows = list(rows)
        if not rows:
            return
        width = min(24, max(len(name) for name, _value, _extra in rows))
        peak = max(value for _name, value, _extra in rows)
        lines.extend(["", title])
        for name, value, extra in rows:
            lines.append(
                f"  {name[:width]:<{width}} {hm(value):>8}  {bar(value, peak)}  {extra}".rstrip()
            )

    table(
        "Devices",
        ((d["name"], d["total_s"], f"{d['share_pct']:.0f}%") for d in data["devices"]),
    )
    table(
        "Days",
        (
            (
                weekday(d["day"]),
                d["total_s"],
                f"{clock(d['first_activity'], d['day'])}-{clock(d['last_activity'], d['day'])}"
                if d["total_s"]
                else "",
            )
            for d in data["daily"]
        ),
    )
    table(
        "Top apps", ((a["name"], a["total_s"], a["category"]) for a in data["top_apps"])
    )
    table(
        "Categories",
        (
            (c["category"], c["total_s"], f"{c['share_pct']:.0f}%")
            for c in data["categories"]
        ),
    )
    worst = late["worst_night"]
    lines += [
        "",
        f"Late night (00-06)  {hm(late['late_s'])} on {late['nights_with_late_use']} nights, "
        f"deep night (03-06) {hm(late['deep_night_s'])}"
        + (f", worst {weekday(worst['day'])} ({hm(worst['late_s'])})" if worst else ""),
        f"Hours   |{spark(data['hourly_s'])}|  00..23",
    ]
    return "\n".join(lines) + "\n"


def render_markdown(data: dict[str, Any], label: str) -> str:
    period, late = data["period"], data["late_night"]
    out = [
        f"## Screen time {period['from']} – {period['to']}",
        "",
        f"*{label}, {period['timezone']}, device: {data['device'] or 'all'}*",
        "",
        f"- **Total:** {hm(data['total_s'])} (avg {hm(data['avg_per_day_s'])}/day, "
        f"{data['active_days']}/{period['elapsed_days']} active days)",
    ]
    if data["previous"]:
        out.append(f"- **Change:** {change(data['previous'])}")
    worst = late["worst_night"]
    out += [
        f"- **Late night (00–06):** {hm(late['late_s'])} on {late['nights_with_late_use']} nights; "
        f"deep night (03–06) {hm(late['deep_night_s'])}"
        + (f"; worst {worst['day']} ({hm(worst['late_s'])})" if worst else ""),
        f"- **First / last activity:** {data['first_activity'] or '–'} / {data['last_activity'] or '–'}",
    ]

    def table(
        title: str, head: tuple[str, ...], rows: Iterable[tuple[str, ...]]
    ) -> None:
        rows = list(rows)
        if rows:
            out.extend(
                [
                    "",
                    f"### {title}",
                    "",
                    "| " + " | ".join(head) + " |",
                    "|" + "---|" * len(head),
                ]
            )
            out.extend("| " + " | ".join(row) + " |" for row in rows)

    table(
        "Devices",
        ("Device", "Time", "Share"),
        (
            (d["name"], hm(d["total_s"]), f"{d['share_pct']:.0f}%")
            for d in data["devices"]
        ),
    )
    table(
        "Days",
        ("Day", "Time", "Late night", "First", "Last"),
        (
            (
                weekday(d["day"]),
                hm(d["total_s"]),
                hm(d["late_s"]),
                clock(d["first_activity"], d["day"]),
                clock(d["last_activity"], d["day"]),
            )
            for d in data["daily"]
        ),
    )
    table(
        "Top apps",
        ("App", "Category", "Time"),
        ((a["name"], a["category"], hm(a["total_s"])) for a in data["top_apps"]),
    )
    table(
        "Categories",
        ("Category", "Time", "Share"),
        (
            (c["category"], hm(c["total_s"]), f"{c['share_pct']:.0f}%")
            for c in data["categories"]
        ),
    )
    return "\n".join(out) + "\n"


def write_rows(
    rows: Iterable[dict[str, Any]], fields: tuple[str, ...], fmt: str, out: IO[str]
) -> int:
    """Stream rows as CSV or as a JSON array (one object per line)."""
    count = 0
    if fmt == "csv":
        writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for count, row in enumerate(rows, 1):
            writer.writerow(row)
        return count
    out.write("[")
    for count, row in enumerate(rows, 1):
        out.write(
            ("," if count > 1 else "") + "\n" + json.dumps(row, ensure_ascii=False)
        )
    out.write("\n]\n")
    return count


def _summary_range(args: argparse.Namespace, today: date) -> tuple[date, date, str]:
    if args.day:
        day = {"today": today, "yesterday": today - timedelta(days=1)}.get(
            args.day
        ) or date.fromisoformat(args.day)
        return day, day, day.strftime("%A")
    if args.from_date or args.to_date:
        start, end = queries.resolve_range(
            today, start=args.from_date, end=args.to_date
        )
        return start, end, "custom range"
    week = args.week or "this"
    if week in ("this", "last"):
        anchor = today - timedelta(days=7 if week == "last" else 0)
    elif "W" in week.upper():
        year, number = week.upper().split("-W")
        anchor = date.fromisocalendar(int(year), int(number), 1)
    else:
        anchor = date.fromisoformat(week)
    monday = anchor - timedelta(days=anchor.weekday())
    iso = monday.isocalendar()
    return monday, monday + timedelta(days=6), f"ISO week {iso.year}-W{iso.week:02d}"


def add_commands(sub: Any) -> None:
    summary = sub.add_parser(
        "summary",
        help="usage report for a week, a day or a date range",
        description="Report screen time. Default: the current ISO week (Monday to Sunday).",
    )
    when = summary.add_mutually_exclusive_group()
    when.add_argument(
        "--week",
        nargs="?",
        const="this",
        metavar="WEEK",
        help="this (default), last, YYYY-Www or any date in the week",
    )
    when.add_argument(
        "--day",
        nargs="?",
        const="today",
        metavar="DAY",
        help="today (default), yesterday or YYYY-MM-DD",
    )
    when.add_argument(
        "--from", dest="from_date", type=date.fromisoformat, metavar="YYYY-MM-DD"
    )
    summary.add_argument(
        "--to",
        dest="to_date",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="end of --from range (default today)",
    )
    summary.add_argument("--device", help="device name or id (default: all devices)")
    summary.add_argument(
        "--top", type=int, default=10, help="number of top apps (default 10)"
    )
    summary.add_argument(
        "--format", choices=("text", "markdown", "json"), default="text"
    )

    dump = sub.add_parser(
        "dump",
        help="write sessions or daily totals as CSV/JSON",
        description="Write raw sessions (default) or per-day app totals (--daily) to stdout or a file.",
    )
    dump.add_argument(
        "--daily",
        action="store_true",
        help="per day, device and app instead of sessions",
    )
    dump.add_argument("--format", choices=("csv", "json"), default="csv")
    dump.add_argument(
        "--from", dest="from_date", type=date.fromisoformat, metavar="YYYY-MM-DD"
    )
    dump.add_argument(
        "--to", dest="to_date", type=date.fromisoformat, metavar="YYYY-MM-DD"
    )
    dump.add_argument("--device", help="device name or id (default: all devices)")
    dump.add_argument(
        "--output", "-o", metavar="FILE", help="write here instead of stdout"
    )

    mcp = sub.add_parser(
        "mcp", help="run the MCP server (stdio; needs the [mcp] extra)"
    )
    mcp.add_argument(
        "--http", action="store_true", help="serve streamable HTTP instead of stdio"
    )
    mcp.add_argument(
        "--host", default="127.0.0.1", help="HTTP bind address (default 127.0.0.1)"
    )
    mcp.add_argument("--port", type=int, default=8765, help="HTTP port (default 8765)")


def run(args: argparse.Namespace, settings: Settings) -> int:
    if args.command == "mcp":
        try:
            from .mcp_server import serve
        except ModuleNotFoundError as exc:
            if (exc.name or "").split(".")[0] not in (
                "mcp",
                "pydantic",
                "typing_extensions",
            ):
                raise
            print(
                "screentime mcp needs the optional MCP dependency. Install it with\n"
                '  uv tool install --force "screentime-exporter[mcp] @ git+https://github.com/nichtlegacy/screentime"\n'
                'or: pip install "screentime-exporter[mcp]"',
                file=sys.stderr,
            )
            return 1
        serve(settings, http=args.http, host=args.host, port=args.port)
        return 0
    tz = settings.timezone
    try:
        conn = queries.open_db(settings.db_path)
    except (FileNotFoundError, sqlite3.Error) as exc:
        print(f"screentime: {exc}", file=sys.stderr)
        return 1
    try:
        if args.command == "summary":
            today = datetime.now(tz).date()
            start, end, label = _summary_range(args, today)
            data = queries.summary(
                conn, tz, start, end, args.device, today=today, top=args.top
            )
            if args.format == "json":
                print(json.dumps(data, indent=2, ensure_ascii=False))
            else:
                print(
                    (render_markdown if args.format == "markdown" else render_text)(
                        data, label
                    ),
                    end="",
                )
            return 0
        if args.daily:
            rows = queries.iter_daily_apps(
                conn, args.from_date, args.to_date, args.device
            )
            fields = queries.DAILY_FIELDS
        else:
            rows = queries.iter_sessions(
                conn, tz, args.from_date, args.to_date, args.device
            )
            fields = queries.SESSION_FIELDS
        if args.output:
            with open(args.output, "w", encoding="utf-8", newline="") as out:
                count = write_rows(rows, fields, args.format, out)
            print(f"wrote {count} rows to {args.output}", file=sys.stderr)
        else:
            write_rows(rows, fields, args.format, sys.stdout)
        return 0
    except (LookupError, ValueError) as exc:
        print(f"screentime: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:  # `screentime dump | head`
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    finally:
        conn.close()
