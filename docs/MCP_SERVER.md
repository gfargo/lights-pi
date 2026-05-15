# MCP Server Architecture

The MCP server at `mcp-server/server.py` runs as `lighting-mcp.service` on the
Pi and exposes the lighting rig as a [Model Context Protocol](https://modelcontextprotocol.io)
endpoint at `http://lights.local:5001/mcp`. Any MCP-capable client — Claude
Desktop, ChatGPT, Cursor, custom agent — can connect, discover fixtures /
groups / scenes, and issue commands without hand-rolling REST integrations.

```
LLM agent ──MCP/HTTP──▶ lighting-mcp.service ──HTTP──▶ lighting-control.service ──WS──▶ QLC+
            (Claude Desktop,  :5001/mcp                 :5000 (Flask)                    :9999
             ChatGPT, custom)
```

## Design

**Thin wrapper, not a reimplementation.** The MCP server holds no QLC+
connection of its own. It makes HTTP calls into the Flask control server on
`localhost:5000`, preserving the "single writer" invariant on the persistent
QLC+ WebSocket (see [CONTROL_SERVER_ARCHITECTURE.md](CONTROL_SERVER_ARCHITECTURE.md#why-a-single-persistent-websocket)).
Restarting the MCP server is safe — it has no state to lose.

**Sibling systemd service.** `lighting-mcp.service` is ordered
`After=lighting-control.service` with `Wants=`, so the Flask backend is up
before the MCP wrapper starts. They live and die independently otherwise.

**Streamable HTTP transport.** Chosen over stdio because the agents are
remote (laptops, phones, cloud services) and the Pi is on the studio LAN.
Streamable HTTP also makes it trivial to put behind the existing
nginx/stunnel TLS reverse proxy for off-LAN access.

**No AI in this process.** When an LLM agent calls `adjust_color("warm")`,
the MCP server posts directly to `/api/action` on Flask, bypassing the
control server's natural-language interpreter. The LLM is already on the
other end of the MCP socket — interpreting its structured tool call as
English and re-running it through another LLM would be wasted latency.

## Process & Ports

| Service                  | Port   | Owner of QLC+ WebSocket? |
|--------------------------|--------|--------------------------|
| `qlcplus-web.service`    | 9999   | listens (server)         |
| `lighting-control.service` | 5000 | yes — single persistent  |
| `lighting-mcp.service`   | 5001   | no — calls Flask over HTTP |

## Tool Catalog

All tools are defined in [mcp-server/server.py](../mcp-server/server.py).

### Discovery (read-only)

| Tool                     | Returns                                       |
|--------------------------|-----------------------------------------------|
| `get_status`             | AI provider, QLC+ service, workspace, WS state |
| `list_fixtures`          | All fixtures with `channel_info` from `.qxf`   |
| `get_fixture_channels`   | Per-channel role/preset/colour for one fixture |
| `list_groups`            | Fixture groups (named subsets)                 |
| `list_scenes`            | Saved scene functions in workspace             |
| `list_templates`         | Built-in templates (party, ambient, …)         |
| `get_channel_values`     | Live DMX channel snapshot                      |

### Actions (write)

| Tool                | Effect                                                        |
|---------------------|---------------------------------------------------------------|
| `activate_scene`    | Apply existing saved scene by name or numeric ID              |
| `apply_template`    | Apply a built-in template, optionally to a list of groups     |
| `adjust_brightness` | Set/nudge master/dimmer (`0-255`, `'75%'`, `'+30'`, `'-20'`)  |
| `adjust_color`      | Set a color preset (red, warm, cool, …) with optional intensity |
| `fade`              | Fade brightness to target over N seconds                      |
| `generate_scene`    | AI-synthesize a scene from a description and apply live       |
| `set_channel`       | Direct DMX channel write (power-user escape hatch)            |
| `save_scene`        | Persist a scene XML (e.g. from `generate_scene`) to workspace |
| `snapshot_scene`    | Capture current live state as a new saved scene               |
| `blackout`          | Instantly zero every channel on targeted fixtures (kill-all)  |
| `batch_action`      | Execute an ordered list of actions in a single round trip     |
| `identify_fixture`  | Flash a single fixture on/off so the operator can locate it physically |

### Group management

| Tool                       | Effect                                              |
|----------------------------|-----------------------------------------------------|
| `create_group`             | New fixture group from a name + fixture IDs         |
| `delete_group`             | Remove a group                                      |
| `update_group`             | Rename, change description, or replace fixture list |
| `add_fixtures_to_group`    | Append fixtures to an existing group                |
| `remove_fixtures_from_group` | Remove fixtures from an existing group            |

### Scene management

| Tool               | Effect                                                        |
|--------------------|---------------------------------------------------------------|
| `describe_scene`   | Return per-fixture channel values for a saved scene           |
| `delete_scene`     | Remove a saved scene from the workspace                       |
| `rename_scene`     | Rename a scene (and/or move its folder Path)                  |
| `duplicate_scene`  | Copy a scene under a new name (basis for "start from X but…") |

### Resources

| URI                  | Payload                                                      |
|----------------------|--------------------------------------------------------------|
| `lights://workspace` | One-shot dump: status + fixtures + groups + scenes + templates |

Useful for an LLM to load at the start of a session and avoid many discovery
calls.

## Configuration

Env vars (set in `/home/<user>/mcp-server/.env`, loaded by the systemd unit):

| Variable           | Default                 | Notes                                         |
|--------------------|-------------------------|-----------------------------------------------|
| `CONTROL_URL`      | `http://localhost:5000` | Flask backend URL                             |
| `MCP_HOST`         | `0.0.0.0`               | Bind address                                  |
| `MCP_PORT`         | `5001`                  | Listen port                                   |
| `MCP_PATH`         | `/mcp`                  | Streamable HTTP mount path                    |
| `MCP_BEARER_TOKEN` | _(unset)_               | Reserved for auth — scaffolded, not enforced  |
| `MCP_HTTP_TIMEOUT` | `30`                    | Seconds for upstream Flask calls              |

## Client Wiring

### Claude Desktop / Cursor

```json
{
  "mcpServers": {
    "qlc-lights": {
      "url": "http://lights.local:5001/mcp"
    }
  }
}
```

### MCP Inspector

```bash
npx @modelcontextprotocol/inspector http://lights.local:5001/mcp
```

### Custom client (Python SDK)

```python
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession

async with streamablehttp_client("http://lights.local:5001/mcp") as (r, w, _):
    async with ClientSession(r, w) as s:
        await s.initialize()
        tools = await s.list_tools()
        result = await s.call_tool("adjust_color", {"color": "warm", "intensity": "70%"})
```

## Lifecycle

```bash
./lightsctl.sh mcp-install     # provision venv, copy code, install + start systemd
./lightsctl.sh mcp-status      # systemctl status lighting-mcp.service
./lightsctl.sh mcp-logs        # journalctl -u lighting-mcp.service -n 50
./lightsctl.sh mcp-restart     # restart after .env or code changes
./lightsctl.sh mcp-uninstall   # disable, remove unit, drop firewall rule
```

## Auth (Future Work)

`MCP_BEARER_TOKEN` is plumbed through the systemd unit and read at startup,
but not yet enforced. To enable, wrap `mcp.streamable_http_app()` with an
ASGI middleware that checks the `Authorization: Bearer …` header. FastMCP
also supports a full OAuth provider — overkill for a LAN rig, but the right
choice if the endpoint is ever exposed off-network through the nginx/stunnel
TLS proxy.

## Failure Modes

- **Flask backend down**: MCP tool calls return `{success: false, error: ...}`
  with the upstream status code surfaced. The systemd `Wants=` ordering means
  the MCP service may briefly start before Flask is ready on boot; calls
  retry implicitly on next invocation.
- **MCP server crash**: systemd restarts it (`Restart=always, RestartSec=10`).
  No state is lost — discovery refetches and live channel state lives in QLC+.
- **Client disconnects mid-stream**: Streamable HTTP cleanly terminates the
  session; the next initialize re-establishes.
