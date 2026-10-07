"""The launchd agent that runs ``screentime run`` every 15 minutes."""

from __future__ import annotations

import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

from .config import config_path

# SCREENTIME_AGENT_LABEL lets a test agent run next to the real one.
DEFAULT_LABEL = "io.github.nichtlegacy.screentime"
LABEL = os.getenv("SCREENTIME_AGENT_LABEL") or DEFAULT_LABEL
INTERVAL_S = 900
LOG_MAX_BYTES = 5 * 1024 * 1024


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def log_dir() -> Path:
    # A test agent logs (and `--purge` deletes) apart from the real one.
    name = "screentime" if LABEL == DEFAULT_LABEL else LABEL
    return Path.home() / "Library" / "Logs" / name


def rotate_logs() -> None:
    """Move a log past ``LOG_MAX_BYTES`` to ``*.1``, keeping one old generation.

    launchd opened this run's log already, so this run still writes to the
    moved file; the next run starts a new one.
    """
    for path in log_dir().glob("*.log"):
        try:
            if path.stat().st_size > LOG_MAX_BYTES:
                path.replace(path.with_name(path.name + ".1"))
        except OSError:
            pass  # a log we cannot move is no reason to skip the sync


def program() -> Path:
    """The installed ``screentime`` script of the running environment.

    For ``uv tool install`` / ``pipx`` this is the tool's own venv, which
    survives upgrades; unlike ``uvx``'s temporary cache environments.
    """
    script = Path(sys.executable).parent / "screentime"
    return script if script.exists() else Path(shutil.which("screentime") or script)


def fda_binary() -> str:
    """The file that needs Full Disk Access when launchd runs the agent.

    launchd makes the agent its own "responsible process", and the script's
    shebang execs the venv Python, a symlink chain ending at the real
    interpreter binary (e.g. ``~/.local/share/uv/python/cpython-3.13.11-…/
    bin/python3.13``). macOS checks that resolved file.
    """
    return os.path.realpath(sys.executable)


def plist(executable: Path) -> dict[str, object]:
    return {
        "Label": LABEL,
        "ProgramArguments": [str(executable), "run"],
        # The label tells `run` which log directory to rotate.
        "EnvironmentVariables": {
            "SCREENTIME_CONFIG": str(config_path()),
            "SCREENTIME_AGENT_LABEL": LABEL,
        },
        "StartInterval": INTERVAL_S,
        "RunAtLoad": True,
        # One JSON line per run; `run` rotates them (rotate_logs).
        "StandardOutPath": str(log_dir() / "screentime.log"),
        "StandardErrorPath": str(log_dir() / "screentime.err.log"),
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *args], capture_output=True, text=True, check=False
    )


def _domain() -> str:
    return f"gui/{os.getuid()}"


def loaded() -> bool:
    return _launchctl("print", f"{_domain()}/{LABEL}").returncode == 0


def install(executable: Path) -> Path:
    """Write the plist and (re)load the agent; it runs once right away."""
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    log_dir().mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(plist(executable)))
    if loaded():
        _launchctl("bootout", f"{_domain()}/{LABEL}")
    result = _launchctl("bootstrap", _domain(), str(path))
    if result.returncode:
        raise RuntimeError(f"launchctl bootstrap failed: {result.stderr.strip()}")
    return path


def uninstall() -> bool:
    """Unload and delete the agent; False if there was nothing to remove."""
    was_loaded = loaded()
    if was_loaded:
        _launchctl("bootout", f"{_domain()}/{LABEL}")
    path = plist_path()
    if path.exists():
        path.unlink()
        return True
    return was_loaded
