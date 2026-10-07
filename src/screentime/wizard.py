"""``screentime setup``: Full Disk Access, devices, sinks, config and agent."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import getpass
import io
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib
from typing import Any

from . import launchd
from .config import Settings, config_path, load_settings, write_config
from .health import (
    FDA_PANE,
    check_ha,
    check_influx,
    status,
    denied_sources,
)
from .importer import enabled_sources
from .locking import db_lock_path
from .sources import model_name
from .taxonomy import apply_overrides


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--yes", action="store_true", help="accept all defaults, never prompt"
    )
    parser.add_argument("--influx-url")
    parser.add_argument("--influx-token")
    parser.add_argument("--influx-org")
    parser.add_argument("--influx-bucket")
    parser.add_argument("--ha-url")
    parser.add_argument("--ha-token")
    parser.add_argument("--ha-prefix", help="entity id prefix (default: screentime)")
    parser.add_argument(
        "--no-agent", action="store_true", help="do not install the launchd agent"
    )
    parser.add_argument(
        "--uninstall", action="store_true", help="remove the launchd agent"
    )
    parser.add_argument(
        "--purge",
        action="store_true",
        help="with --uninstall: also delete the database, logs and config",
    )


class _Prompt:
    def __init__(self, yes: bool) -> None:
        self.yes = yes

    def ask(self, question: str, default: str = "", *, secret: bool = False) -> str:
        shown = ("****" if secret else default) if default else ""
        if self.yes:
            print(f"{question}: {shown}")  # show what --yes chose
            return default
        text = f"{question} [{shown}]: " if shown else f"{question}: "
        answer = (getpass.getpass(text) if secret else input(text)).strip()
        return answer or default

    def confirm(self, question: str, default: bool) -> bool:
        if self.yes:
            return default
        answer = input(f"{question} [{'Y/n' if default else 'y/N'}] ").strip().lower()
        return answer.startswith("y") if answer else default


def run_setup(args: argparse.Namespace) -> int:
    if args.uninstall:
        return _uninstall(purge=args.purge)
    if sys.platform != "darwin":
        print("screentime setup needs macOS.", file=sys.stderr)
        return 1
    prompt = _Prompt(args.yes)
    settings = load_settings()
    path = config_path()
    raw: dict[str, Any] = (
        tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    )

    print("Screen Time Exporter setup\n")
    if not _full_disk_access(settings, prompt):
        return 1
    raw["devices"] = _devices(settings, prompt)
    _influx(raw, settings, args, prompt)
    _home_assistant(raw, settings, args, prompt)
    write_config(raw, path)
    print(f"\nSaved {path} (readable only by you).")

    settings = load_settings()
    _first_sync(settings)
    if args.no_agent or not prompt.confirm(
        "\nInstall the background agent (runs every 15 minutes)?", True
    ):
        print("Skipped the agent. Run `screentime run` yourself or re-run setup.")
        return 0
    return _install_agent(network=bool(settings.influx_url or settings.ha_url))


def _full_disk_access(settings: Settings, prompt: _Prompt) -> bool:
    missing = denied_sources(settings)
    if not missing:
        print("Full Disk Access: ok")
        return True
    print("This process cannot read Apple's Screen Time stores:")
    for path in missing:
        print(f"  {path}")
    print(
        "\nOpen System Settings → Privacy & Security → Full Disk Access, enable\n"
        "your terminal app (Terminal, iTerm, …), quit it completely and run\n"
        "`screentime setup` again."
    )
    if prompt.confirm("Open that settings page now?", not prompt.yes):
        subprocess.run(["open", FDA_PANE], check=False)
    return prompt.confirm("Continue without access?", False)


def _devices(settings: Settings, prompt: _Prompt) -> dict[str, str | bool]:
    """Ask for a name per discovered device; ``False`` ignores a device."""
    found = []
    for source in enabled_sources(settings):
        try:
            found += source.devices()
        except Exception as exc:  # one broken source must not stop setup
            print(f"{source.name}: cannot list devices ({type(exc).__name__}: {exc})")
    configured: dict[str, str | bool] = {
        **settings.devices,
        **dict.fromkeys(settings.ignored_devices, False),
    }
    if not found:
        print("\nNo devices found yet.")
        return configured
    print(
        f"\nFound {len(found)} device(s). Enter a display name, or `-` to ignore one."
    )
    taken: set[str] = set()
    result = dict(configured)
    for device in found:
        current = configured.get(device.id)
        if current is False:
            default = "-"
        elif current:
            default = str(current)
        else:
            # Drop the " (1a2b3c4d)" the source adds to unnamed devices.
            base = device.name.removesuffix(f" ({device.id[:8]})")
            default, n = base, 1
            while default in taken:
                n += 1
                default = f"{base} {n}"
        model = f"{model_name(device.model)}, " if device.model else ""
        name = prompt.ask(
            f"  {device.platform} ({model}{device.source}, id {device.id[:8]}…)",
            default,
        )
        result[device.id] = False if name == "-" else name
        taken.add(name)
    return result


def _test(label: str, error: str | None, prompt: _Prompt) -> bool:
    """Print a connection result; True if the settings should be kept."""
    if error is None:
        print(f"  {label}: connection ok")
        return True
    print(f"  {label}: {error}")
    return prompt.confirm("  Save anyway?", prompt.yes)


def _influx(
    raw: dict[str, Any],
    settings: Settings,
    args: argparse.Namespace,
    prompt: _Prompt,
) -> None:
    url = args.influx_url or settings.influx_url
    if not prompt.confirm("\nExport to InfluxDB?", bool(url)):
        raw.pop("influx", None)
        return
    section = raw.setdefault("influx", {})
    section["url"] = prompt.ask("  URL", url or "http://localhost:8086").rstrip("/")
    section["token"] = prompt.ask(
        "  Token", args.influx_token or settings.influx_token or "", secret=True
    )
    section["org"] = prompt.ask("  Org", args.influx_org or settings.influx_org or "")
    section["bucket"] = prompt.ask(
        "  Bucket", args.influx_bucket or settings.influx_bucket
    )
    candidate = Settings(
        settings.db_path,
        settings.timezone,
        influx_url=section["url"],
        influx_token=section["token"],
        influx_org=section["org"],
        influx_bucket=section["bucket"],
    )
    if not _test("InfluxDB", check_influx(candidate), prompt):
        raw.pop("influx")


def _home_assistant(
    raw: dict[str, Any],
    settings: Settings,
    args: argparse.Namespace,
    prompt: _Prompt,
) -> None:
    url = args.ha_url or settings.ha_url
    if not prompt.confirm("\nExport to Home Assistant?", bool(url)):
        raw.pop("home_assistant", None)
        return
    section = raw.setdefault("home_assistant", {})
    section["url"] = prompt.ask(
        "  URL", url or "http://homeassistant.local:8123"
    ).rstrip("/")
    section["token"] = prompt.ask(
        "  Long-lived access token",
        args.ha_token or settings.ha_token or "",
        secret=True,
    )
    while True:
        prefix = prompt.ask(
            "  Entity id prefix (sensor.<prefix>_total)",
            args.ha_prefix or settings.ha_entity_prefix,
        )
        if re.fullmatch(r"[a-z0-9_]+", prefix):
            break
        print("  Use lowercase letters, digits and _ only.")
        if prompt.yes:
            raise SystemExit(f"invalid --ha-prefix {prefix!r}")
    section["entity_prefix"] = prefix
    candidate = Settings(
        settings.db_path,
        settings.timezone,
        ha_url=section["url"],
        ha_token=section["token"],
    )
    if not _test("Home Assistant", check_ha(candidate), prompt):
        raw.pop("home_assistant")


def _first_sync(settings: Settings) -> None:
    from .cli import _locked, _pipeline  # cli imports this module

    print("\nFirst sync…")
    apply_overrides(settings.apps)
    with redirect_stdout(io.StringIO()):  # the JSON report; summarised below
        _locked(settings, lambda conn: _pipeline(conn, settings, export=True))
    facts = status(settings)
    run = facts.get("last_run") or {}
    print(f"  {run.get('status', 'not run')}: {run.get('error') or 'no errors'}")
    for device in facts["devices"]:
        if device["device_id"] in settings.ignored_devices:
            continue
        print(
            f"  {device['name']:<24} {device['events']:>7} events, "
            f"last {device['last_event_at'] or 'never'}"
        )


def _install_agent(*, network: bool) -> int:
    executable = launchd.program()
    if not executable.exists() or "/archive-v" in str(executable):
        print(
            f"\n{executable} is not a permanent install (uvx?). Install with\n"
            "`uv tool install git+https://github.com/nichtlegacy/screentime`\n"
            "and run `screentime setup` again."
        )
        return 1
    path = launchd.install(executable)
    print(
        f"\nInstalled {path}\n"
        f"  runs:  {executable} run, every 15 minutes\n"
        f"  logs:  {launchd.log_dir()}/\n"
        "\nThe agent needs its own Full Disk Access. In System Settings →\n"
        "Privacy & Security → Full Disk Access click +, press Cmd+Shift+G and\n"
        "add this file:\n"
        f"\n  {launchd.fda_binary()}\n"
    )
    if network:
        print(
            "If InfluxDB or Home Assistant is on your local network, macOS asks\n"
            f"once whether {Path(launchd.fda_binary()).name} may find devices on "
            "it. Allow it\n(System Settings → Privacy & Security → Local Network).\n"
        )
    print("Then check with `screentime doctor` after the next run.")
    return 0


def _uninstall(*, purge: bool) -> int:
    removed = launchd.uninstall()
    print(f"Removed agent {launchd.LABEL}." if removed else "No agent installed.")
    if purge:
        settings = load_settings()
        db = settings.db_path
        for path in (
            db,
            db.with_name(db.name + "-wal"),
            db.with_name(db.name + "-shm"),
            db_lock_path(db),
            config_path(),
        ):
            if path.exists():
                path.unlink()
                print(f"Deleted {path}")
        if launchd.log_dir().exists():
            shutil.rmtree(launchd.log_dir())
            print(f"Deleted {launchd.log_dir()}")
    else:
        print("Data, logs and config were kept (`--purge` deletes them).")
    return 0
