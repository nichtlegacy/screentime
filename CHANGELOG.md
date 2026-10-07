# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- Without the App Store lookup, apps that Spotlight could not name were
  remembered for a week, so turning the lookup on later skipped them.

## [2.0.0] - 2026-10-07

v2 is a rewrite: one installed `screentime` command instead of scripts, a
local database instead of a CSV, and no ActivityWatch. Upgrade steps:
[docs/migration.md](docs/migration.md).

### Added

- Installable package `screentime-exporter` with one `screentime` command
  (`uv tool install git+https://github.com/nichtlegacy/screentime`).
- `screentime setup`: finds every iPhone, iPad and the Mac, proposes names
  (unnamed devices get their model, e.g. `iPhone 17 Pro`), tests InfluxDB and
  Home Assistant, writes `~/.config/screentime/config.toml`, runs a first sync
  and installs the launchd agent. `--yes` for scripted installs,
  `--uninstall [--purge]` to remove.
- `screentime doctor` checks config, Full Disk Access, Local Network access,
  the agent, data freshness and (with `--online`) the connections, each with
  a fix. `screentime status` prints devices, last run and sinks as JSON.
- Local SQLite database with every app session, plus daily, per-app,
  per-category and hourly aggregates.
- Late night (00:00–06:00) and deep night (03:00–06:00) per device, and each
  night's bedtime and wake-up.
- App names and 14 categories:
  - 272 apps ship with a name and category.
  - Unknown apps are named from the app installed on the Mac (Spotlight) or,
    failing that, the App Store of the Mac's region with the US store as a
    fallback; results are cached.
  - Your own names in `[apps]` apply to stored sessions on the next run.
  - `screentime apps unknown` lists what is still guessed,
    `screentime apps lookup` checks one bundle id, `screentime remap`
    reapplies everything.
- Ignore a device with `false` in `[devices]`.
- `screentime summary` (week, day or range; text, Markdown or JSON) and
  `screentime dump` (sessions or daily totals as CSV or JSON).
- Read-only MCP server `screentime mcp` (optional `[mcp]` extra) for Claude,
  Codex, Cursor and other clients, and a prompt that lets a coding agent do
  the whole installation ([docs/agent-installation.md](docs/agent-installation.md)).
- Home Assistant sensors `_week`, `_last_sync` and per-device `_late_night`
  and `_deep_night`; configurable entity prefix.
- New Grafana dashboard with device, category and app filters, and a Docker
  Compose file that starts InfluxDB and Grafana with it provisioned.
- `screentime export --from DATE` re-sends history to InfluxDB.
- `scripts/demo_data.py` fills a database and a bucket with three fake
  devices, for trying the dashboard.
- Agent logs are capped at 5 MB, keeping one old generation.
- Landing page at [screentime.nichtlegacy.com](https://screentime.nichtlegacy.com).

### Changed

- iPhone and iPad usage is read straight from Biome; every device that shares
  Screen Time with the Mac is found without device ids.
- Runs every 15 minutes as the launchd agent `io.github.nichtlegacy.screentime`
  (v1's example plist ran every 6 hours).
- Configuration moved from `.env` to `config.toml` (mode `0600`). `INFLUX_*`,
  `HA_URL` and `HA_TOKEN` still work as environment variables.
- Data lives in `~/Library/Application Support/screentime/`, logs in
  `~/Library/Logs/screentime/`.
- InfluxDB schema: four measurements tagged by `device`, `device_id` and
  `platform`; each changed day is rewritten, so exports are idempotent. The
  v1 dashboard does not work with it.
- Home Assistant duration sensors use `state_class: total` with `last_reset`;
  `sensor.screentime_by_category` reports the top category name. The v1
  entity ids stay.
- Requires Python 3.11 or later.

### Fixed

- InfluxDB points no longer carry `source=iPhone` for every device; each
  device has its own tags.

### Removed

- The ActivityWatch `aw-import-screentime` dependency.
- `screentime.csv`; v2 does not import it.
- `run.py`, `.env.example` and the hand-edited `examples/launchd.plist`.
- `session_count` on `sensor.screentime_total`.

## [1.0.0] - 2026-02-08

First release.

### Added

- Mac usage from `knowledgeC.db`; iPhone and iPad usage via iCloud through
  ActivityWatch's `aw-import-screentime`.
- Several iOS devices with custom names (`DEVICES=Name:UUID,…`).
- Deduplicated collection into `screentime.csv`.
- Home Assistant sensors: total, per device, top app, top 10 apps and by
  category.
- InfluxDB 2 export (measurement `screentime`) and a Grafana dashboard.
- Example launchd agent for scheduled runs.

[Unreleased]: https://github.com/nichtlegacy/screentime/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/nichtlegacy/screentime/releases/tag/v2.0.0
[1.0.0]: https://github.com/nichtlegacy/screentime/releases/tag/v1.0.0
