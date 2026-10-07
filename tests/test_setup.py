from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import plistlib
import subprocess
import tomllib
from typing import Any

import pytest

from screentime import cli, health, launchd, wizard
from screentime.config import load_settings
from screentime.sources import Device, UsageEvent

PHONE_A = Device(
    "aaaaaaaa-0000", "iPhone 17 Pro (aaaaaaaa)", "iphone", "biome", "iPhone18,1"
)
PHONE_B = Device(
    "bbbbbbbb-0000", "iPhone 17 Pro (bbbbbbbb)", "iphone", "biome", "iPhone18,1"
)
MAC = Device("mac:1", "Test Mac", "mac", "knowledgec")
START = datetime.now(UTC) - timedelta(hours=1)


class FakeSource:
    name = "fake"

    def devices(self) -> list[Device]:
        return [PHONE_A, PHONE_B, MAC]

    def read(self, state: Any) -> tuple[list[UsageEvent], dict[str, Any]]:
        event = UsageEvent(
            PHONE_A.id, "com.example.app", START, START + timedelta(minutes=5)
        )
        return [event], {}


@pytest.fixture
def mac(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """A fake macOS: HOME in tmp, fake sources, launchctl recorded."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SCREENTIME_DB_PATH", str(tmp_path / "st.db"))
    monkeypatch.setattr(wizard.sys, "platform", "darwin")
    monkeypatch.setattr(wizard, "denied_sources", lambda settings: [])
    monkeypatch.setattr(wizard, "enabled_sources", lambda settings: [FakeSource()])
    monkeypatch.setattr(
        "screentime.cli.enabled_sources", lambda settings: [FakeSource()]
    )
    program = tmp_path / "tools" / "bin" / "screentime"
    program.parent.mkdir(parents=True)
    program.touch()
    monkeypatch.setattr(launchd, "program", lambda: program)
    calls: list[list[str]] = []

    def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        missing = cmd[1] == "print" and not launchd.plist_path().exists()
        return subprocess.CompletedProcess(cmd, 113 if missing else 0, "", "")

    monkeypatch.setattr(launchd.subprocess, "run", run)
    return calls


def script(
    monkeypatch: pytest.MonkeyPatch, answers: list[str], secrets: list[str]
) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt="": answers.pop(0))
    monkeypatch.setattr(wizard.getpass, "getpass", lambda prompt="": secrets.pop(0))


def config(tmp_path: Path) -> dict[str, Any]:
    return tomllib.loads((tmp_path / "missing.toml").read_text())


def test_plist() -> None:
    data = plistlib.loads(plistlib.dumps(launchd.plist(Path("/opt/st/bin/screentime"))))
    assert data["Label"] == "io.github.nichtlegacy.screentime"
    assert data["ProgramArguments"] == ["/opt/st/bin/screentime", "run"]
    assert (data["StartInterval"], data["RunAtLoad"]) == (900, True)
    assert data["StandardOutPath"].endswith("Library/Logs/screentime/screentime.log")
    assert data["EnvironmentVariables"]["SCREENTIME_CONFIG"].endswith("missing.toml")
    assert data["EnvironmentVariables"]["SCREENTIME_AGENT_LABEL"] == launchd.LABEL


def test_run_rotates_large_logs(tmp_path, monkeypatch, mac) -> None:
    logs = launchd.log_dir()
    logs.mkdir(parents=True)
    (logs / "screentime.log").write_bytes(b"x" * (launchd.LOG_MAX_BYTES + 1))
    (logs / "screentime.log.1").write_text("oldest")
    (logs / "screentime.err.log").write_text("small")
    assert cli.main(["run"]) == 0
    assert (logs / "screentime.log.1").stat().st_size == launchd.LOG_MAX_BYTES + 1
    assert not (logs / "screentime.log").exists()
    assert (logs / "screentime.err.log").read_text() == "small"


def test_test_agent_logs_apart(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launchd, "LABEL", "org.example.screentime.test")
    assert launchd.log_dir().name == "org.example.screentime.test"


def test_interactive_wizard(tmp_path, monkeypatch, mac, capsys) -> None:
    (tmp_path / "missing.toml").write_text(
        '[custom]\nkeep = 1\n[devices]\n"mac:1" = false\n'
    )
    monkeypatch.setattr(wizard, "check_influx", lambda s: None)
    monkeypatch.setattr(wizard, "check_ha", lambda s: "token rejected (HTTP 401)")
    script(
        monkeypatch,
        # devices: keep, rename, keep ignored; influx yes + url/org/bucket;
        # HA yes, url, bad prefix, good prefix, don't save; install agent.
        [
            "",
            "Kid's iPhone",
            "",
            "y",
            "http://influx.test:8086/",
            "home",
            "",
            "y",
            "http://ha.test:8123",
            "Bad-Prefix",
            "st_test",
            "n",
            "",
        ],
        ["influx-token", "ha-token"],
    )
    args = cli.build_parser().parse_args(["setup"])
    assert wizard.run_setup(args) == 0
    raw = config(tmp_path)
    assert raw["custom"] == {"keep": 1}
    assert raw["devices"] == {
        PHONE_A.id: "iPhone 17 Pro",
        PHONE_B.id: "Kid's iPhone",
        "mac:1": False,
    }
    assert raw["influx"] == {
        "url": "http://influx.test:8086",
        "token": "influx-token",
        "org": "home",
        "bucket": "screentime",
    }
    assert "home_assistant" not in raw
    assert [
        "launchctl",
        "bootstrap",
        f"gui/{launchd.os.getuid()}",
        str(launchd.plist_path()),
    ] in mac
    assert launchd.plist_path().exists()
    assert "Local Network" in capsys.readouterr().out  # Influx is configured


def test_unnamed_twins_get_numbered(tmp_path, monkeypatch, mac, capsys) -> None:
    args = cli.build_parser().parse_args(
        [
            "setup",
            "--yes",
            "--no-agent",
            "--ha-url",
            "http://ha.test",
            "--ha-token",
            "t",
        ]
    )
    monkeypatch.setattr(wizard, "check_ha", lambda s: None)
    assert wizard.run_setup(args) == 0
    raw = config(tmp_path)
    assert list(raw["devices"].values()) == [
        "iPhone 17 Pro",
        "iPhone 17 Pro 2",
        "Test Mac",
    ]
    assert raw["home_assistant"]["entity_prefix"] == "screentime"
    assert "id bbbbbbbb…): iPhone 17 Pro 2" in capsys.readouterr().out
    assert "influx" not in raw
    assert not launchd.plist_path().exists()
    # The first sync ran and imported the fake event.
    assert (tmp_path / "st.db").exists()


def test_missing_full_disk_access_stops(tmp_path, monkeypatch, mac) -> None:
    monkeypatch.setattr(wizard, "denied_sources", lambda s: [Path("/x/knowledgeC.db")])
    opened: list[list[str]] = []
    monkeypatch.setattr(wizard.subprocess, "run", lambda cmd, **kw: opened.append(cmd))
    script(monkeypatch, ["y", ""], [])
    assert wizard.run_setup(cli.build_parser().parse_args(["setup"])) == 1
    assert opened == [["open", wizard.FDA_PANE]]
    assert not (tmp_path / "missing.toml").exists()


def test_uninstall_and_purge(tmp_path, monkeypatch, mac) -> None:
    launchd.install(Path("/opt/st/bin/screentime"))
    (tmp_path / "st.db").write_text("")
    (tmp_path / "missing.toml").write_text("")
    assert cli.main(["setup", "--uninstall"]) == 0
    assert ["launchctl", "bootout", f"gui/{launchd.os.getuid()}/{launchd.LABEL}"] in mac
    assert not launchd.plist_path().exists()
    assert (tmp_path / "st.db").exists()
    assert cli.main(["setup", "--uninstall", "--purge"]) == 0
    assert not (tmp_path / "st.db").exists()
    assert not (tmp_path / "missing.toml").exists()
    assert not launchd.log_dir().exists()


def test_doctor_reports_setup_problems(tmp_path, monkeypatch, mac) -> None:
    monkeypatch.setattr(
        "screentime.cli.denied_sources", lambda s: [Path("/x/knowledgeC.db")]
    )
    (tmp_path / "missing.toml").write_text(
        '[influx]\nurl = "http://influx.test"\n'
        '[home_assistant]\nurl = "http://ha.test"\ntoken = "t"\nentity_prefix = "Bad"\n'
    )
    assert cli.main(["sync"]) == 0  # partial
    launchd.install(launchd.program())
    monkeypatch.setattr(health.sys, "platform", "darwin")
    monkeypatch.setattr(health, "denied_sources", lambda s: [])
    monkeypatch.setattr(health, "check_ha", lambda s: "token rejected (HTTP 401)")
    checks = health.doctor(load_settings(), online=True)["checks"]
    found = {(c["name"], c["status"]) for c in checks}
    assert {
        ("influx", "warn"),
        ("home_assistant", "failed"),
        ("agent", "ok"),
        ("full_disk_access", "ok"),
        ("last_run", "warn"),
    } <= found
    assert f'"{PHONE_B.id}" = false' in str(checks)  # never reported usage
    last_run = next(c["detail"] for c in checks if c["name"] == "last_run")
    assert "The agent lacks Full Disk Access" in last_run
    assert "token rejected" in str(checks)
    launchd.plist_path().unlink()
    assert ("agent", "warn") in {
        (c["name"], c["status"]) for c in health.doctor(load_settings())["checks"]
    }


def test_invalid_config_is_reported(tmp_path) -> None:
    (tmp_path / "missing.toml").write_text("timezone = ")
    assert cli.main(["doctor"]) == 1


def test_denied_means_permission_error_not_missing(tmp_path, monkeypatch) -> None:
    store = tmp_path / "knowledgeC.db"
    assert not health._denied(store)
    store.touch()
    assert not health._denied(store)

    # chmod would not stop root (CI containers), so simulate macOS refusing.
    def refuse(self: Path, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError(1, "Operation not permitted", str(self))

    monkeypatch.setattr(Path, "open", refuse)
    assert health._denied(store)


def test_doctor_explains_local_network_block() -> None:
    found: list[tuple[str, str, str]] = []
    run = {
        "status": "partial",
        "started_at": datetime.now(UTC).isoformat(),
        "error": "InfluxDB: … [Errno 65] No route to host",
    }
    health._last_run_check(lambda *check: found.append(check), run)
    assert "Privacy & Security → Local Network" in found[0][2]
