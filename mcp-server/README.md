# Lights MCP Server

Model Context Protocol (MCP) server that exposes the QLC+ lighting control
system to LLM agents (Claude Desktop, ChatGPT, Cursor, custom clients).

Runs as a thin wrapper over the control-server Flask REST API — the Flask app
owns the persistent QLC+ WebSocket, so this process stays stateless and crash-
safe to restart.

## Transport

- **Streamable HTTP** at `http://lights.local:5001/mcp` (default)
- LAN-only by default; bind via `MCP_HOST` / `MCP_PORT` env vars
- Bearer token gate available via `MCP_BEARER_TOKEN` (scaffolded, not yet enforced)

## Architecture

```
LLM Client  ──HTTP/MCP──▶  lighting-mcp.service  ──HTTP/REST──▶  lighting-control.service  ──WS──▶  QLC+
   (Claude Desktop,         (this server, :5001)                  (Flask app, :5000)               (:9999)
    ChatGPT, etc.)
```

## Tools

**Discovery (read-only):**
- `get_status` — overall health (AI provider, QLC+, workspace, WebSocket)
- `list_fixtures` — every fixture with channel metadata
- `get_fixture_channels(fixture_id)` — per-channel role/colour info for one fixture
- `list_groups` — fixture groups
- `list_scenes` — saved scenes in workspace
- `list_templates` — built-in templates
- `get_channel_values` — live DMX channel snapshot

**Actions (write):**
- `activate_scene(scene)` — apply existing saved scene by name or id
- `apply_template(template, groups?)` — apply built-in template
- `adjust_brightness(value, groups?)` — set/nudge brightness (0-255, '%', '+/-')
- `adjust_color(color, intensity?, groups?)` — set color preset
- `fade(target, duration, groups?)` — fade to target over seconds
- `generate_scene(description, groups?)` — AI-synthesized scene, applied live
- `set_channel(fixture_id, channel, value)` — direct DMX channel write
- `save_scene(name, scene_xml?, snapshot?, path?)` — persist a scene
- `snapshot_scene(name, path?)` — save current live state as a new scene

**Resources:**
- `lights://workspace` — one-shot dump of fixtures + groups + scenes + templates + status

## Local development

```bash
cd mcp-server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Point at a running control-server (default: http://localhost:5000)
export CONTROL_URL=http://localhost:5000

python3 server.py
# → listening on http://0.0.0.0:5001/mcp
```

Then connect a client:
```bash
# Inspect the server with the MCP CLI
mcp dev server.py

# Or wire it into Claude Desktop / Cursor config:
#   "qlc-lights": { "url": "http://lights.local:5001/mcp" }
```

## Installation on the Pi

```bash
./lightsctl.sh mcp-install     # creates systemd unit, installs deps, starts service
./lightsctl.sh mcp-status      # systemctl status
./lightsctl.sh mcp-logs        # journalctl -u lighting-mcp.service
./lightsctl.sh mcp-restart     # restart after config changes
./lightsctl.sh mcp-uninstall   # remove service + firewall rule
```

## Configuration (env vars)

| Variable           | Default                 | Notes                                       |
|--------------------|-------------------------|---------------------------------------------|
| `CONTROL_URL`      | `http://localhost:5000` | URL of the control-server Flask app         |
| `MCP_HOST`         | `0.0.0.0`               | Bind address                                |
| `MCP_PORT`         | `5001`                  | Listen port                                 |
| `MCP_PATH`         | `/mcp`                  | URL path mounted by Streamable HTTP         |
| `MCP_BEARER_TOKEN` | _(unset)_               | Reserved for auth — not yet enforced        |
| `MCP_HTTP_TIMEOUT` | `30`                    | Seconds for upstream Flask calls            |
