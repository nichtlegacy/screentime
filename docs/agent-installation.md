# Let an AI agent install it

A coding agent with terminal access (Claude Code, Codex, Cursor's agent) can
do the whole setup. Paste the prompt below into the agent on the Mac that
should run the exporter. Two steps stay with you: the Full Disk Access
switches in System Settings, and entering tokens.

```text
Install Screen Time Exporter (https://github.com/nichtlegacy/screentime) on this Mac.

1. Read the README and docs/setup.md in that repository first.
2. Make sure uv is installed (https://docs.astral.sh/uv/). Do not use the
   system Python.
3. Install the tool with the MCP extra:
   uv tool install --force "screentime-exporter[mcp] @ git+https://github.com/nichtlegacy/screentime"
4. Run `screentime doctor`. If Full Disk Access is missing, stop and tell me
   exactly which app to add under System Settings → Privacy & Security →
   Full Disk Access, then wait until I confirm.
5. Ask me whether I use InfluxDB and/or Home Assistant. Ask for URLs, org,
   bucket and the entity prefix. Never ask me to paste tokens into the chat:
   tell me to export INFLUX_TOKEN / HA_TOKEN in the terminal myself, or run
   `screentime setup` interactively in my terminal.
6. Run `screentime setup` (with --yes and the flags from docs/setup.md if I
   want it non-interactive). Show me the device list and the names it proposes.
7. After setup, show me the Python interpreter path that setup printed and
   ask me to add it to Full Disk Access for the background agent.
8. Run `screentime doctor --online` and `screentime summary --day` and show
   me the results.
9. Register the MCP server for this agent as described in docs/mcp.md.

Do not edit files under ~/Library other than through `screentime setup`, and
do not touch any other launchd agents.
```

Expected: `screentime doctor` reports every check as OK, the launchd agent
`io.github.nichtlegacy.screentime` is loaded, and the agent can answer
"How much screen time did I have today?" through the MCP server.
