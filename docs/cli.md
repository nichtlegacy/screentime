# Reports and data export

`screentime summary` and `screentime dump` read the local SQLite database
read-only (`mode=ro`), so they work while the 15-minute `screentime run` is
active. Days are local calendar dates in the configured `timezone`; date
ranges are inclusive. `--device` takes a device name or id (case-insensitive,
see `screentime status`); without it all devices are combined.

## `screentime summary`

```bash
screentime summary                         # current ISO week (Monday to Sunday)
screentime summary --week last             # previous ISO week
screentime summary --week 2026-W13         # a specific ISO week (or any date in it)
screentime summary --day                   # today; also --day yesterday / --day 2026-03-25
screentime summary --from 2026-03-01 --to 2026-03-31
screentime summary --device "Alex's iPhone" --top 5 --format markdown
```

| Option | Meaning |
|---|---|
| `--week [this\|last\|YYYY-Www\|YYYY-MM-DD]` | ISO week, Monday to Sunday (default when no range is given) |
| `--day [today\|yesterday\|YYYY-MM-DD]` | a single day |
| `--from YYYY-MM-DD [--to YYYY-MM-DD]` | custom range; `--to` defaults to today |
| `--device NAME` | one device only |
| `--top N` | number of top apps (default 10) |
| `--format text\|markdown\|json` | terminal report (default), Markdown for notes, or JSON |

The report shows the total and average per elapsed day, the change against
the previous period, the per-device split, daily bars with first and last
activity, top apps, categories, late night (00:00–06:00) and deep night
(03:00–06:00) with the worst night, and an hour-of-day sparkline.

**Comparison.** The previous period has the same length, shifted back, and is
cut to the days already elapsed: on a Wednesday, the current week is compared
with Monday to Wednesday of the week before. The average divides by elapsed
days, not by the full period.

**Late night** counts towards the day it falls on: the night from Friday to
Saturday is Saturday.

### JSON

`--format json` prints one object. Durations are whole seconds (`*_s`),
timestamps ISO 8601 with offset in the configured timezone, keys are stable
(`daily`, `devices`, `top_apps` and `categories` shortened):

```json
{
  "period": {"from": "2026-03-23", "to": "2026-03-29", "days": 7, "elapsed_days": 7, "timezone": "Europe/Berlin"},
  "device": null,
  "total_s": 14400,
  "avg_per_day_s": 2057,
  "active_days": 3,
  "first_activity": "2026-03-24T23:30:00+01:00",
  "last_activity": "2026-03-29T03:30:00+02:00",
  "previous": {"from": "2026-03-16", "to": "2026-03-22", "total_s": 5400, "change_pct": 166.7},
  "devices": [{"device_id": "phone-1", "name": "Phone", "platform": "iphone", "total_s": 10800, "share_pct": 75.0}],
  "daily": [{"day": "2026-03-23", "total_s": 0, "late_s": 0, "deep_night_s": 0, "first_activity": null, "last_activity": null, "devices": {}}],
  "top_apps": [{"bundle_id": "com.burbn.instagram", "name": "Instagram", "category": "Social", "total_s": 10800, "sessions": 3, "share_pct": 75.0}],
  "categories": [{"category": "Social", "total_s": 10800, "share_pct": 75.0}],
  "late_night": {"late_s": 9000, "deep_night_s": 1800, "nights_with_late_use": 2, "worst_night": {"day": "2026-03-25", "late_s": 5400, "deep_night_s": 0}, "nights": [{"day": "2026-03-25", "late_s": 5400, "deep_night_s": 0}, {"day": "2026-03-29", "late_s": 3600, "deep_night_s": 1800}]},
  "hourly_s": [3600, 3600, 0, 1800, 0, 0, 0, 0, 0, 0, 3600, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1800]
}
```

- `previous` is `null` for periods entirely in the future; `change_pct` is
  `null` when the previous period has no usage.
- `daily` has one entry per day in the range (zero-filled); `devices` maps
  device names to seconds.
- `sessions` counts usage intervals per day, so one crossing midnight counts
  on both days.
- `hourly_s` has 24 entries, local hour of day 0–23.

## `screentime dump`

Writes data as CSV (default) or a JSON array to stdout or `--output FILE`.
Rows are streamed, so large ranges don't need much memory. (`screentime
export` is the different command that pushes to InfluxDB and Home Assistant.)

```bash
screentime dump > sessions.csv                                  # every session
screentime dump --from 2026-03-01 --to 2026-03-31 --format json -o march.json
screentime dump --daily --device Laptop                         # per day and app
```

| Option | Meaning |
|---|---|
| `--daily` | per local day, device and app instead of sessions |
| `--format csv\|json` | default `csv` |
| `--from` / `--to YYYY-MM-DD` | local days, inclusive; default: everything |
| `--device NAME` | one device only |
| `--output FILE`, `-o` | write to a file instead of stdout |

Session columns: `device_id, device, bundle_id, title, category, start, end,
duration_s`. `start`/`end` are local ISO timestamps; a session belongs to the
day it starts on.

Daily columns: `day, device_id, device, bundle_id, title, category,
duration_s, sessions`. Sessions crossing midnight are split between days.
