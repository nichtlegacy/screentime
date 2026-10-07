# Installation and setup

## Requirements

- A Mac signed in to the same Apple Account as your
  iPhone/iPad, with **Screen Time → Share Across Devices** turned on on every
  device. iPhone/iPad usage reaches the Mac through iCloud.
- [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`).
  uv brings its own Python 3.11+; the Python that ships with macOS is too old.

## Install

```bash
uv tool install git+https://github.com/nichtlegacy/screentime
screentime setup
```

`uv tool install` puts the package into its own environment
(`~/.local/share/uv/tools/screentime-exporter/`) and links `screentime` into
`~/.local/bin/`. That environment stays at the same path across upgrades, so
the launchd agent keeps working:

```bash
uv tool upgrade screentime-exporter
```

From a local checkout use `uv tool install .` instead. `pipx install
git+https://github.com/nichtlegacy/screentime` (or `pip install` into a venv of
your own) works too, provided it runs on Python 3.11 or later. Do not set up the agent from `uvx`: its environments are
temporary, and `screentime setup` refuses to install the agent from one.

## `screentime setup`

The wizard walks through:

1. **Full Disk Access** for the current process (see below). Without it the
   wizard explains what to enable and can open the settings page.
2. **Devices.** It lists every iPhone, iPad and this Mac it can see and
   proposes a name (unnamed iPhones and iPads get their model name, e.g.
   `iPhone 17 Pro`). Type a new name, press Enter to keep the proposal, or
   type `-` to ignore a device. Names end up in `[devices]`; they are the
   `device` tag in InfluxDB and part of the Home Assistant entity ids. The
   list can include iPhones you no longer use; the first sync shows them with
   0 events, and `-` hides them.
3. **InfluxDB** (URL, token, org, bucket) and **Home Assistant** (URL,
   long-lived token, entity id prefix). Each connection is tested before it
   is saved; the InfluxDB test is an empty write, so the token needs write
   access to the bucket and nothing else.
4. Writes `~/.config/screentime/config.toml` with mode `0600`. Re-running
   setup keeps every other key in that file, but not its comments.
5. Runs a first sync and prints what it found.
6. Installs and starts the launchd agent.

Re-run it at any time to rename devices or change sinks.

### Non-interactive

`--yes` accepts every default without prompting. Defaults come from the
existing config, the environment (`INFLUX_URL`, `INFLUX_TOKEN`, `INFLUX_ORG`,
`INFLUX_BUCKET`, `HA_URL`, `HA_TOKEN`) and these flags:

| Flag | Meaning |
|---|---|
| `--influx-url`, `--influx-token`, `--influx-org`, `--influx-bucket` | InfluxDB; giving a URL enables it |
| `--ha-url`, `--ha-token`, `--ha-prefix` | Home Assistant; giving a URL enables it |
| `--no-agent` | write the config and sync, but do not install the agent |
| `--uninstall` | stop and remove the agent; data, logs and config stay |
| `--uninstall --purge` | also delete the database, logs and config |

Prefer the environment variables for tokens; flags end up in your shell
history.

```bash
INFLUX_TOKEN=… screentime setup --yes --influx-url http://nas.local:8086 --influx-org home
```

## Full Disk Access

Apple's Screen Time stores (`~/Library/Biome/…` and `knowledgeC.db`) are
protected. Two programs need access in **System Settings → Privacy & Security
→ Full Disk Access**:

- **Your terminal app** (Terminal, iTerm, …) for `screentime setup` and manual
  runs. Quit and reopen it after enabling the switch.
- **The agent's Python interpreter** for the background runs. launchd makes
  the agent its own "responsible process", so the terminal's permission does
  not apply. The `screentime` script starts the tool environment's `python`,
  which is a chain of symlinks; macOS checks the real file at its end, for
  example

  ```
  ~/.local/share/uv/python/cpython-3.13.11-macos-aarch64-none/bin/python3.13
  ```

  `screentime setup` prints the exact path. Click **+**, press
  **Cmd+Shift+G**, paste the path and confirm.

That path contains the Python patch version. After uv upgrades the
interpreter (`uv python upgrade`, or reinstalling the tool on a newer Python)
the grant has to be repeated for the new path; `screentime doctor` reports it
(`Operation not permitted` in the last run) together with the path to add.

A wrapper with a fixed path would not avoid this: a shell script wrapper makes
the shell the responsible process (and granting Full Disk Access to `/bin/sh`
grants it to every script), and a compiled wrapper would need Xcode's command
line tools on every Mac.

An agent installed from an SSH session can read the stores without this grant
while it stays loaded, because it inherits the SSH server's access. Grant it
anyway; after a restart launchd loads the agent on its own.

## Local Network

Since macOS 15 a launchd agent needs permission to reach hosts on your local
network, such as InfluxDB on a NAS or Home Assistant. Without it every export
fails with `No route to host` while `screentime doctor --online` from the
terminal succeeds; the terminal and SSH are exempt. On the agent's first
connection macOS asks whether the interpreter (`python3.13` in the example
above) may find devices on the local network: allow it. Change it later in
**System Settings → Privacy & Security → Local Network**. `screentime doctor`
points there when the last run failed this way. Servers on the internet are
not affected.

## The launchd agent

| | |
|---|---|
| Label | `io.github.nichtlegacy.screentime` |
| Plist | `~/Library/LaunchAgents/io.github.nichtlegacy.screentime.plist` |
| Runs | `<tool environment>/bin/screentime run` at load and every 15 minutes |
| Config | `SCREENTIME_CONFIG` is set to the config path used during setup |
| Logs | `~/Library/Logs/screentime/screentime.log` (one JSON summary per run) and `screentime.err.log`; past 5 MB each moves to `*.1` |

```bash
launchctl print gui/$(id -u)/io.github.nichtlegacy.screentime     # state, last exit code
launchctl kickstart gui/$(id -u)/io.github.nichtlegacy.screentime  # run now
screentime setup --uninstall                                       # stop and remove
```

`SCREENTIME_AGENT_LABEL` overrides the label, so a test agent can run next to
the real one (`setup`, `setup --uninstall` and `doctor` all honour it). Such an
agent logs to `~/Library/Logs/<label>/`, so `--uninstall --purge` leaves the
real agent's logs alone. Give it its own `SCREENTIME_CONFIG` with a separate
`db_path` as well.

## `screentime doctor`

Checks, each with a fix in its message:

- config file present and valid, Home Assistant `entity_prefix` valid,
  InfluxDB / Home Assistant settings complete
- Full Disk Access of the current process
- agent installed, loaded, and its program still present
- database present; per device the last event (warns after 3 days without
  new usage, which usually means Screen Time sharing is off on that device,
  and for devices that never reported usage, usually old iPhones to ignore)
- last run: status, age (warns if the agent has not run for 45 minutes),
  errors, and the interpreter to add when the agent lacks Full Disk Access
- with `--online`: an empty write to InfluxDB and an authenticated request to
  Home Assistant

It exits with 1 if any check failed. `--json` prints the checks as JSON.
