# MCP server

`screentime mcp` runs a read-only [Model Context Protocol](https://modelcontextprotocol.io)
server over the local database, so Claude (or any MCP client) can answer
questions about your screen time. It opens SQLite read-only per call and runs
fine next to the 15-minute sync. Nothing leaves the machine except what the
client sends to its model.

## Install

The MCP SDK is an optional extra:

```bash
uv tool install --force "screentime-exporter[mcp] @ git+https://github.com/nichtlegacy/screentime"
```

`--force` replaces an existing install without the extra; the launchd agent
keeps working because the tool environment keeps its path. From a checkout:
`uv tool install --force ".[mcp]"`.

Without it, `screentime mcp` prints these install instructions and exits with
status 1.

## Claude Code

```bash
claude mcp add screentime -- screentime mcp
```

Add `-s user` to make it available in every project. If `screentime` isn't on
the `PATH` Claude Code sees, use the absolute path (`which screentime`).

## Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "screentime": {
      "command": "/Users/you/.local/bin/screentime",
      "args": ["mcp"]
    }
  }
}
```

A non-default config file can be passed with
`"env": {"SCREENTIME_CONFIG": "/path/to/config.toml"}`.

There is no `.mcpb` desktop extension: a bundle would install a second copy
of the package next to the one the launchd agent runs, so this command-based
config is the supported path.

## Codex

`~/.codex/config.toml`:

```toml
[mcp_servers.screentime]
command = "/Users/you/.local/bin/screentime"
args = ["mcp"]
```

## Cursor

`~/.cursor/mcp.json` (or `.cursor/mcp.json` in a project):

```json
{
  "mcpServers": {
    "screentime": {
      "command": "/Users/you/.local/bin/screentime",
      "args": ["mcp"]
    }
  }
}
```

Any other MCP client works the same way: command `screentime`, argument
`mcp`, transport stdio.

## Tools

All tools are read-only. Dates are ISO strings (`YYYY-MM-DD`), local days in
the configured timezone, inclusive. Without dates a tool covers the last 7
days including today. `period` accepts `today`, `yesterday`, `this_week`,
`last_week` (ISO weeks, Monday to Sunday), `last_7_days` and `last_30_days`;
`start_date`/`end_date` override it. `device` takes a name or id from
`list_devices`; omitted means all devices.

| Tool | Arguments | Returns |
|---|---|---|
| `list_devices` | – | devices with platform, first/last day and stored total |
| `get_summary` | `period`, `start_date`, `end_date`, `device`, `top_n` | the full report, same shape as `screentime summary --format json` ([docs](cli.md#json)) |
| `get_daily_usage` | `period`, `start_date`, `end_date`, `device` | per-day totals, late/deep night, first/last activity, per-device split |
| `get_top_apps` | `period`, `start_date`, `end_date`, `device`, `limit` | apps by time with category, sessions and share |
| `get_categories` | `period`, `start_date`, `end_date`, `device` | time and share per category |
| `get_late_night` | `period`, `start_date`, `end_date`, `device` | 00:00–06:00 and 03:00–06:00 totals, worst night, every night with use |

Results are structured (with output schemas) plus a JSON text fallback.
Unknown devices and invalid dates come back as tool errors that list the
known devices. The resource `screentime://today` holds today's summary.

`screentime mcp --http [--host 127.0.0.1] [--port 8765]` serves streamable
HTTP instead of stdio. It has no authentication; keep it on localhost.

## Example questions

- How much screen time did I have last week, and how does it compare to the week before?
- Which apps took the most time on my iPhone this month?
- Was I on my phone after midnight this week? Which night was the worst?
- Compare my Mac and iPhone usage over the last 30 days.
- When did I first pick up my phone each morning this week?

## Testing

`tests/test_queries.py` calls every tool through the SDK's in-memory client
and spawns `screentime mcp` over stdio. To try it by hand with the MCP
Inspector:

```bash
npx @modelcontextprotocol/inspector screentime mcp
```
