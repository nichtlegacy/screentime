# App names and categories

Apple's stores only record bundle ids such as `com.burbn.instagram`. Every
session gets a display name and one of 14 categories (Social, Communication,
Browser, Productivity, Coding, AI, Media, Gaming, Health & Fitness, Shopping,
Finance, Utilities, System, Other; described in
[CONTRIBUTING.md](../CONTRIBUTING.md#categories)).

## Where a name comes from

For each bundle id, name and category are taken field by field from the first
source that has one:

1. **Your config**: `[apps."<bundle id>"]` in `config.toml`.
2. **The shipped list**: `src/screentime/data/apps.json` (270+ apps).
3. **The app itself**, if it is installed on this Mac, or **the App Store**:
   both are looked up once and cached in the database (see below).
4. **A guess** from the bundle id: `com.example.myApp` becomes "My App" in
   "Other".

## Looking names up

After each sync, up to 200 of the most-used bundle ids that would otherwise be
guessed are looked up:

1. **Installed Mac apps.** Spotlight finds the app on this Mac by its bundle
   id. Its display name (or its file name, as Finder shows it) becomes the
   name, and its App Store category (`LSApplicationCategoryType`, set by the
   developer) maps to one of ours; without one it is "Other". This covers Mac
   apps from outside the App Store and needs no network.
2. **The App Store**, for the rest. The App Store name (shortened at ":" or
   " - ") becomes the name, and the App Store genre maps to a category.

- **Store**: the storefront of your Mac's region (*System Settings → General →
  Language & Region*), so regional apps are found. Anything missing there is
  looked up in the US store as well. Set `[lookup] country = "gb"` to use a
  different one.
- **Cache**: hits are kept; misses are retried after seven days. Network
  errors cache nothing and never fail a run.
- **Privacy**: only bundle ids and the country code are sent to Apple. Turn
  the lookup off with `[lookup] enabled = false`.

What is left keeps its guessed name until you name it: iPhone and iPad apps
outside the App Store (TestFlight, your own builds), web apps, and Mac apps
that have since been deleted.

## Find apps without a proper name

```bash
screentime apps unknown              # most-used apps that still have a guessed name
screentime apps lookup <bundle id>   # look one bundle id up: installed app, then App Store
```

`apps unknown` lists the bundle id, the hours used and the guessed name, most
used first.

## Name them yourself

Add a section per bundle id to `~/.config/screentime/config.toml`. Both keys
are optional; a missing one keeps the name or category from the next source.

```toml
[apps."com.example.internal-tool"]
name = "Deploy Tool"
category = "Coding"

[apps."org.example.emulator"]
name = "Emulator"
category = "Gaming"

[apps."com.example.shop"]
category = "Shopping"   # keep the App Store name, change the category
```

For many apps the one-line form under a single `[apps]` table is shorter and
means the same:

```toml
[apps]
"com.example.internal-tool" = { name = "Deploy Tool", category = "Coding" }
"com.example.shop" = { category = "Shopping" }
```

Use one of the 14 categories; any other name works too and shows up as a
category of its own (without a fixed colour in Grafana).

The next `screentime run` (the agent runs every 15 minutes) notices the
change, renames every stored session of those apps, rebuilds the affected
days and re-sends them to InfluxDB and Home Assistant. Nothing else is
needed; `screentime remap` does the same on demand.

To name a whole family of internal apps at once, there is no pattern syntax:
list each bundle id.

## Share them

If an app is publicly available and others are likely to use it, add it to
`src/screentime/data/apps.json` in a pull request instead, so everybody gets
the name: [CONTRIBUTING.md](../CONTRIBUTING.md#adding-apps). Personal,
in-house or employer apps belong in your own config.
