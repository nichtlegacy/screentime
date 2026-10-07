# Upgrading from v1

v2 is a rewrite. It reads iPhone and iPad usage straight from Biome instead of
going through ActivityWatch's `aw-import-screentime`, keeps everything in a
local SQLite database instead of a CSV file, and is configured through
`~/.config/screentime/config.toml` instead of `.env`.

## What changes

| | v1 | v2 |
|---|---|---|
| Install | `git clone` + `pip3 install` + `aw-import-screentime` checkout | `uv tool install git+https://github.com/nichtlegacy/screentime` |
| Run | `python3 run.py` | `screentime run`, or the launchd agent every 15 minutes |
| iPhone / iPad | `aw-import-screentime` per `DEVICE_ID` / `DEVICES` | every device found in Biome, named in `[devices]` |
| Storage | `screentime.csv` | SQLite in `~/Library/Application Support/screentime/` |
| Config | `.env` | `config.toml`; `INFLUX_*`, `HA_URL`, `HA_TOKEN` still work as environment variables |
| launchd | `examples/launchd.plist`, edited by hand | installed by `screentime setup` (`io.github.nichtlegacy.screentime`) |
| InfluxDB | `screentime`, field `duration`, tags `source`, `app`, `title`, `category` | four measurements, tags `device`, `device_id`, `platform` ([schema](architecture.md#influxdb-schema)) |
| Home Assistant | 5 sensors | v1 sensors plus week, last sync and per-device late/deep night ([details](home-assistant.md#compatibility-with-v1)) |

## Steps

1. Stop the v1 agent and remove its plist:

   ```bash
   launchctl bootout gui/$(id -u)/com.apple-screentime-exporter
   rm ~/Library/LaunchAgents/com.apple-screentime-exporter.plist
   ```

2. Install v2 and run the wizard. It takes InfluxDB and Home Assistant
   defaults from the environment, so your old `.env` can be loaded first:

   ```bash
   uv tool install git+https://github.com/nichtlegacy/screentime
   set -a; source /path/to/v1/.env; set +a
   screentime setup
   ```

3. In the wizard, give devices the names they had in v1 (`iPhone 15 Pro`,
   `Mac`, …) if Home Assistant automations or Grafana queries use them.

4. Grant Full Disk Access to the Python interpreter that `screentime setup`
   prints ([why](setup.md#full-disk-access)). The v1 grant for Terminal or
   `python3` does not carry over.

5. Import the new Grafana dashboard (`grafana/dashboards/screentime.json`).
   The v1 dashboard does not work with the v2 schema.

Expected: `screentime doctor` reports every check as OK, and the first sync
lists your devices.

## What is not migrated

- **The v1 CSV.** v2 does not import it. The first v2 sync reads whatever
  Apple still keeps (a few weeks; Biome holds about four), and older v1 data
  stays in the CSV and in InfluxDB.
- **Old InfluxDB points.** v1 points stay in the bucket under the field
  `duration`. The v2 dashboard reads only the v2 fields, so they do no harm.
  Use a new bucket if you want a clean start.
