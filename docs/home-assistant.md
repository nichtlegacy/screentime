# Home Assistant

Every `screentime run` (every 15 minutes) posts today's numbers to Home
Assistant through the REST API (`POST /api/states/<entity_id>`). No custom
integration is needed.

## Setup

1. In Home Assistant open your profile → **Security** → **Long-lived access
   tokens** → **Create token**.
2. Add it to `~/.config/screentime/config.toml`:

   ```toml
   [home_assistant]
   url = "http://homeassistant.local:8123"
   token = "eyJ..."
   # entity_prefix = "screentime"
   ```

   `HA_URL` and `HA_TOKEN` in the environment override the file.
   `entity_prefix` (default `screentime`) is the first part of every entity
   id. Give each Mac its own prefix if several Macs report to one Home
   Assistant, and use a throwaway prefix for tests. It must be a valid entity
   id part: lowercase letters, digits and `_`.

## Sensors

"Today" is the local calendar day in the configured `timezone`; "this week"
starts on Monday. All durations are minutes with one decimal. `<prefix>` is
`screentime` unless configured otherwise.

| Entity | State | Key attributes |
|---|---|---|
| `sensor.<prefix>_total` | Minutes today, all devices | `late_night_minutes`, `deep_night_minutes`, `first_activity`, `last_activity` |
| `sensor.<prefix>_<device>` | Minutes today on one device | `platform`, `first_activity`, `last_activity` |
| `sensor.<prefix>_<device>_late_night` | Minutes today between 00:00 and 06:00 | |
| `sensor.<prefix>_<device>_deep_night` | Minutes today between 03:00 and 06:00 | |
| `sensor.<prefix>_week` | Minutes this week, all devices | `average_minutes` (per day so far, today included), `week_start` |
| `sensor.<prefix>_top_app` | Name of today's most used app (`none` before first use) | `minutes`, `bundle_id`, `category`, `apps` (top 10: `name`, `bundle_id`, `category`, `minutes`) |
| `sensor.<prefix>_top_apps` | Number of apps listed (≤ 10) | one attribute per app name: minutes |
| `sensor.<prefix>_by_category` | Name of today's top category | one `category_<Name>` attribute per category: minutes |
| `sensor.<prefix>_last_sync` | Time of the last successful export | `device_class: timestamp` |

The duration sensors carry `unit_of_measurement: min`, `device_class:
duration`, `state_class: total` and `last_reset` (local midnight, or Monday
midnight for the week sensor), so long-term statistics know exactly when the
counter restarts. `first_activity`/`last_activity` are local ISO timestamps of
the first and last usage today. Every sensor has a `friendly_name` and an
`icon`.

A device gets sensors once it has usage in the last seven days; on a day
without usage it reports `0`. Sensors of a device that stays idle longer keep
their last value until Home Assistant restarts.

### Device ids

`<device>` is the device name as a slug: lowercased, accents and apostrophes
removed, everything else that is not a letter or digit becomes `_`.
`Alex’s iPhone 15 Pro` → `alexs_iphone_15_pro`, `Büro iPad` → `buro_ipad`. A
name that clashes with a fixed sensor or another device gets `_2`, `_3`, …
appended. Rename a device in the config to get a stable, readable id:

```toml
[devices]
"00000000-0000-0000-0000-000000000000" = "iPhone"
```

`screentime status` lists the device ids.

## Compatibility with v1

The v1 entity ids still exist: `sensor.screentime_total`,
`sensor.screentime_top_app`, `sensor.screentime_top_apps`,
`sensor.screentime_by_category` and `sensor.screentime_<device>`.
Differences:

- **Device names** now come from Apple's stores (for example the Mac's
  computer name) instead of the v1 `DEVICES` variable. To keep an old id such
  as `sensor.screentime_iphone_15_pro` or `sensor.screentime_mac`, set the
  device name to `iPhone 15 Pro` or `Mac` under `[devices]`.
- **Duration sensors** use `state_class: total` with `last_reset` instead of
  `measurement`. If Home Assistant reports a statistics issue for an old
  sensor under *Developer tools → Statistics*, apply the fix it offers.
- `sensor.screentime_total` no longer has `session_count`.
- `sensor.screentime_by_category` reports the top category name instead of
  the minutes spent on Social; the `category_<Name>` attributes are unchanged.
- New: `_week`, `_last_sync` and the per-device `_late_night` and
  `_deep_night` sensors.

## Limitations of REST states

States created through the REST API are not backed by an integration:

- They have no `unique_id`, so they cannot be renamed, assigned to an area or
  edited in the UI. Use `homeassistant: customize:` in YAML to change a
  friendly name or icon.
- They disappear when Home Assistant restarts and come back with the next
  sync, at most 15 minutes later. Automations should tolerate `unknown`.
- History is not lost: the recorder stores them like any other entity, and
  because they carry a `state_class` they also get long-term statistics.

If you need a `unique_id` or a state that survives restarts, wrap a sensor in
a trigger-based template sensor; those restore their last state:

```yaml
template:
  - triggers:
      - trigger: state
        entity_id: sensor.screentime_total
        not_to: [unknown, unavailable]
    sensor:
      - name: Screen time today
        unique_id: screentime_total_wrapped
        state: "{{ trigger.to_state.state }}"
        unit_of_measurement: min
        device_class: duration
        state_class: total_increasing
```

## Example automations

The examples assume the default prefix and a device named `iPhone`.

Notify when late-night usage on a phone passes 30 minutes:

```yaml
automation:
  - alias: Late-night screen time
    triggers:
      - trigger: numeric_state
        entity_id: sensor.screentime_iphone_late_night
        above: 30
    actions:
      - action: notify.notify
        data:
          message: >-
            {{ states('sensor.screentime_iphone_late_night') }} min on the
            iPhone after midnight.
```

Notify when TikTok passes an hour today (works while it is among the top 10
apps):

```yaml
automation:
  - alias: TikTok over an hour
    triggers:
      - trigger: template
        value_template: >-
          {{ state_attr('sensor.screentime_top_apps', 'TikTok') | float(0) > 60 }}
    actions:
      - action: notify.notify
        data:
          message: >-
            TikTok today: {{ state_attr('sensor.screentime_top_apps', 'TikTok') }} min
```

Warn when the exporter stops syncing:

```yaml
automation:
  - alias: Screen time exporter stale
    triggers:
      - trigger: template
        value_template: >-
          {{ now() - states('sensor.screentime_last_sync') | as_datetime(now())
             > timedelta(hours=1) }}
    actions:
      - action: notify.notify
        data:
          message: Screen Time has not synced for an hour.
```

## Example dashboard

```yaml
type: vertical-stack
cards:
  - type: entities
    title: Screen Time
    entities:
      - entity: sensor.screentime_total
      - entity: sensor.screentime_iphone
      - entity: sensor.screentime_iphone_late_night
      - entity: sensor.screentime_top_app
      - entity: sensor.screentime_week
      - entity: sensor.screentime_last_sync
  - type: statistics-graph
    title: Daily screen time
    entities:
      - sensor.screentime_total
    stat_types: [change]
    period: day
    days_to_show: 14
    chart_type: bar
  - type: markdown
    title: Top apps today
    content: |
      {% for app in state_attr('sensor.screentime_top_app', 'apps') or [] %}
      {{ loop.index }}. **{{ app.name }}** – {{ app.minutes }} min
      {% endfor %}
```
