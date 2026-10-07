# Contributing

Issues and pull requests are welcome. Run the checks before opening a PR:

```bash
uv sync && uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q
```

Tests use synthetic data only. Never commit real Screen Time databases,
device ids or usage numbers.

## Adding apps

`src/screentime/data/apps.json` maps bundle ids to a display name and a
category:

```json
"com.spotify.client": ["Spotify", "Media"]
```

- Find candidates with `screentime apps unknown`: it lists your most-used
  bundle ids that only have a guessed name.
- Add widely used, publicly available apps only. Personal, in-house or
  employer-specific apps belong in your own `config.toml`
  (`[apps."<bundle id>"]` with `name` / `category`).
- iPhone and Mac versions often have different bundle ids; add both.
- Use the short name people know ("Slack", not "Slack for Desktop").
- Pick a category from the list below. Keep the file sorted by bundle id (case-insensitive).

## Categories

| Category | For |
|---|---|
| Social | Social networks and personal messengers (Instagram, WhatsApp, Discord) |
| Communication | Mail, phone, contacts, work chat and meetings (Mail, Slack, Teams, Zoom) |
| Browser | Web browsers and home-screen web apps |
| Productivity | Office, notes, tasks, files, design, learning |
| Coding | Editors, IDEs, terminals, developer tools |
| AI | AI assistants and chatbots |
| Media | Video, music, podcasts, books, news, photos |
| Gaming | Games and game launchers |
| Health & Fitness | Health, workouts, nutrition, sleep |
| Shopping | Stores, marketplaces, food delivery |
| Finance | Banking, payments, investing |
| Utilities | Settings, maps, travel, weather, smart home, VPNs, password managers |
| System | iOS/macOS system screens (lock screen, share sheet, sign-in prompts) |
| Other | Not mapped yet |

`genres` in the same file maps App Store genres to these categories for the
optional lookup of unknown apps.
