from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import date
import json
import sqlite3
import sys
from typing import Any

import requests

from .aggregates import rebuild_all
from .config import Settings, load_settings, system_country
from .db import connect, get_state, init_db, now_utc_iso, set_state, transaction
from .exporter import ExportResult, export_all
from .health import denied_sources, doctor, status
from .importer import enabled_sources, reclassify, sync_sources
from .locking import LockBusyError, db_lock_path, process_lock
from . import launchd, report, wizard
from .taxonomy import (
    apply_overrides,
    classify,
    is_known,
    itunes_lookup,
    load_cache,
    local_lookup,
)


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True, default=str))


def _reapply_overrides(conn: sqlite3.Connection, settings: Settings) -> None:
    """Rename stored sessions when ``[apps]`` changed since the last run."""
    digest = json.dumps(settings.apps, sort_keys=True)
    if get_state(conn, "app_overrides") == digest:
        return
    load_cache(conn)
    with transaction(conn):
        if reclassify(conn):
            rebuild_all(conn, settings.timezone, manage_transaction=False)
        set_state(conn, "app_overrides", digest)


def _pipeline(conn: sqlite3.Connection, settings: Settings, *, export: bool) -> int:
    """Sync all enabled sources, optionally export, and record the run."""
    _reapply_overrides(conn, settings)
    cursor = conn.execute(
        "INSERT INTO runs(run_type, status, started_at) VALUES (?, 'running', ?)",
        ("run" if export else "sync", now_utc_iso()),
    )
    conn.commit()
    try:
        imports = sync_sources(
            conn,
            enabled_sources(settings),
            settings.timezone,
            device_names=settings.devices,
            ignored_devices=settings.ignored_devices,
            lookup_country=(settings.lookup_country or system_country())
            if settings.lookup_enabled
            else None,
        )
        exports = export_all(conn, settings) if export else ExportResult()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        conn.execute(
            "UPDATE runs SET status='failed', finished_at=?, error=? WHERE id=?",
            (now_utc_iso(), error, cursor.lastrowid),
        )
        conn.commit()
        print(f"screentime: {error}", file=sys.stderr)
        return 1
    # Without Full Disk Access the sources see no data rather than failing.
    errors = [
        *(f"{path}: Operation not permitted" for path in denied_sources(settings)),
        *imports.errors,
        *exports.errors,
    ]
    if imports.sources_failed and not imports.sources_ok:
        run_status = "failed"
    else:
        run_status = "partial" if errors else "success"
    details = {
        "sources_ok": imports.sources_ok,
        "sources_failed": imports.sources_failed,
        "events_unchanged": imports.events_unchanged,
        "events_invalid": imports.events_invalid,
        "days_rebuilt": imports.days_rebuilt,
        "influx_status": exports.influx_status,
        "ha_status": exports.ha_status,
    }
    conn.execute(
        """UPDATE runs SET status=?, finished_at=?, events_read=?, events_inserted=?,
        events_updated=?, points_written=?, details_json=?, error=? WHERE id=?""",
        (
            run_status,
            now_utc_iso(),
            imports.events_read,
            imports.events_inserted,
            imports.events_updated,
            exports.points_written,
            json.dumps(details, sort_keys=True),
            "; ".join(errors) or None,
            cursor.lastrowid,
        ),
    )
    conn.commit()
    _print(
        {
            "status": run_status,
            "events_read": imports.events_read,
            "events_inserted": imports.events_inserted,
            "events_updated": imports.events_updated,
            "points_written": exports.points_written,
            **details,
            "errors": errors,
        }
    )
    return 1 if run_status == "failed" else 0


def _export(
    conn: sqlite3.Connection, settings: Settings, from_date: date | None
) -> int:
    result = export_all(conn, settings, from_date=from_date)
    _print(result.__dict__)
    return 1 if result.errors else 0


def _remap(conn: sqlite3.Connection, settings: Settings) -> int:
    """Reapply the taxonomy to stored events and rebuild all days."""
    load_cache(conn)
    with transaction(conn):
        changed = reclassify(conn)
        rebuild_all(conn, settings.timezone, manage_transaction=False)
    _print({"events_remapped": len(changed)})
    return 0


def _apps_unknown(conn: sqlite3.Connection, limit: int) -> int:
    """List the most-used bundle ids that only have a guessed name/category."""
    load_cache(conn)
    rows = conn.execute(
        """SELECT bundle_id, SUM(duration_s) AS seconds FROM screen_events
        GROUP BY bundle_id ORDER BY seconds DESC"""
    ).fetchall()
    unknown = [row for row in rows if not is_known(row["bundle_id"])][:limit]
    if not unknown:
        print("Every app has a name and category.")
        return 0
    print(f"{'hours':>7}  {'bundle id':<44} guessed name (category)")
    for row in unknown:
        title, category = classify(row["bundle_id"])
        hours = row["seconds"] / 3600
        print(f"{hours:7.1f}  {row['bundle_id']:<44} {title} ({category})")
    print(
        '\nName them in config.toml ([apps."<bundle id>"] name/category) or '
        "contribute them to apps.json, see CONTRIBUTING.md."
    )
    return 0


def _locked(settings: Settings, action: Callable[[sqlite3.Connection], int]) -> int:
    try:
        with process_lock(db_lock_path(settings.db_path)):
            conn = connect(settings.db_path)
            try:
                init_db(conn)
                return action(conn)
            finally:
                conn.close()
    except LockBusyError:
        _print({"status": "skipped", "reason": "another screentime run is active"})
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="screentime", description="Export Apple Screen Time."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="sync all sources, then export to configured sinks")
    sub.add_parser("sync", help="read all sources into the database")
    export = sub.add_parser("export", help="export to InfluxDB / Home Assistant")
    export.add_argument(
        "--from",
        dest="from_date",
        type=date.fromisoformat,
        help="re-send InfluxDB data from this local date (YYYY-MM-DD)",
    )
    sub.add_parser("remap", help="reapply app names and categories")
    apps = sub.add_parser("apps", help="inspect app names and categories")
    apps_sub = apps.add_subparsers(dest="apps_command", required=True)
    unknown = apps_sub.add_parser("unknown", help="most-used apps without a mapping")
    unknown.add_argument("--limit", type=int, default=25)
    lookup = apps_sub.add_parser(
        "lookup", help="look up one bundle id in the App Store"
    )
    lookup.add_argument("bundle_id")
    sub.add_parser("status", help="show devices, last run and sinks as JSON")
    doctor_parser = sub.add_parser("doctor", help="check config and data access")
    doctor_parser.add_argument("--json", action="store_true")
    doctor_parser.add_argument(
        "--online", action="store_true", help="also test InfluxDB / Home Assistant"
    )
    wizard.add_arguments(
        sub.add_parser("setup", help="interactive setup and launchd agent")
    )
    report.add_commands(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "setup":
        return wizard.run_setup(args)
    if args.command == "run":
        launchd.rotate_logs()
    try:
        settings = load_settings()
    except (ValueError, KeyError) as exc:  # bad TOML, unknown timezone, …
        print(f"screentime: invalid config: {exc}", file=sys.stderr)
        return 1
    apply_overrides(settings.apps)
    if args.command == "status":
        _print(status(settings))
        return 0
    if args.command in ("summary", "dump", "mcp"):
        return report.run(args, settings)
    if args.command == "apps" and args.apps_command == "lookup":
        try:
            found = local_lookup([args.bundle_id]) or itunes_lookup(
                [args.bundle_id], settings.lookup_country or system_country()
            )
        except (requests.RequestException, ValueError) as exc:
            print(f"screentime: lookup failed: {exc}", file=sys.stderr)
            return 1
        name, category, genre = found.get(args.bundle_id.lower(), (None, None, None))
        _print(
            {
                "bundle_id": args.bundle_id,
                "name": name,
                "category": category,
                "genre": genre,
            }
        )
        return 0 if name else 1
    if args.command == "doctor":
        result = doctor(settings, online=args.online)
        if args.json:
            _print(result)
        else:
            for check in result["checks"]:
                print(f"{check['status']:<7} {check['name']}: {check['detail']}")
        return 1 if result["status"] == "failed" else 0
    actions: dict[str, Callable[[sqlite3.Connection], int]] = {
        "run": lambda conn: _pipeline(conn, settings, export=True),
        "sync": lambda conn: _pipeline(conn, settings, export=False),
        "export": lambda conn: _export(conn, settings, args.from_date),
        "remap": lambda conn: _remap(conn, settings),
        "apps": lambda conn: _apps_unknown(conn, args.limit),
    }
    return _locked(settings, actions[args.command])


if __name__ == "__main__":
    raise SystemExit(main())
