---
inclusion: auto
---

# Technology Stack

## Core Technologies

- **OS**: Raspberry Pi OS (Bookworm, 32-bit or 64-bit, headless)
- **Lighting Engine**: QLC+ 4.14.x (open source DMX control)
- **Hardware Interface**: ENTTEC DMX USB Pro
- **Service Management**: systemd
- **Network**: NetworkManager (Bookworm default), Avahi for mDNS
- **Web Server**: nginx (optional landing page + reverse proxy for HTTPS)
- **Security**: ufw (firewall), stunnel4 or nginx for TLS
- **Control Server**: Python 3.11+, Flask, Flask-SocketIO, `websockets`, `requests`
- **MCP Server**: Python 3.11+, `mcp[cli]` (FastMCP, Streamable HTTP transport), `httpx`
- **AI**: OpenAI / Anthropic / Ollama (configurable via `.env`)

## Build System

Bash-based provisioning and management via `lightsctl.sh`. Python helpers for
fixture-definition parsing, workspace XML manipulation, and the control server
itself. No compilation step.

## Dependencies on the Pi

The control server runs in `/home/<user>/control-server-venv/` (a Python venv)
and is installed via `./lightsctl.sh control-install`. Required packages:

- `flask`, `flask-cors`, `flask-socketio`
- `websockets` (used for the persistent QLC+ connection)
- `requests` (used for AI provider calls)

The MCP server runs in its own `/home/<user>/mcp-server-venv/` venv and is
installed via `./lightsctl.sh mcp-install`. Required packages:

- `mcp[cli]>=1.2.0` (FastMCP + Streamable HTTP server)
- `httpx>=0.27.0` (calls into the Flask control server on `localhost:5000`)

QLC+ provides the actual DMX output and the `.qxf` fixture definition files
that the control server reads for authoritative channel metadata.

## Common Commands

All commands use `./lightsctl.sh` or `make` shortcuts:

### Provisioning

```bash
./lightsctl.sh setup-full          # Full setup: base + hardening
./lightsctl.sh setup               # Base install only
./lightsctl.sh harden              # Security hardening
./lightsctl.sh control-install     # Deploy the control server (port 5000)
./lightsctl.sh mcp-install         # Deploy the MCP server (port 5001)
./lightsctl.sh env-sync            # Push local .env to the Pi
```

### Service Management

```bash
./lightsctl.sh status              # qlcplus-web.service status
./lightsctl.sh restart             # restart QLC+
./lightsctl.sh logs                # last 80 lines from QLC+ journal
./lightsctl.sh control-status      # lighting-control.service status
./lightsctl.sh control-logs        # last 80 lines from control server
./lightsctl.sh control-restart     # restart the control server
./lightsctl.sh mcp-status          # lighting-mcp.service status
./lightsctl.sh mcp-logs            # last 50 lines from MCP server
./lightsctl.sh mcp-restart         # restart the MCP server
./lightsctl.sh health              # full system health check
```

### Workspace + Fixture Definitions

```bash
./lightsctl.sh deploy-workspace workspaces/studio.qxw
./lightsctl.sh pull-workspace      # download current workspace
./lightsctl.sh list-fixtures       # show installed .qxf fixture defs
./lightsctl.sh install-fixture <file.qxf>
```

### AI Scene Generation

```bash
./lightsctl.sh generate-scene "warm sunset" --add-to-workspace
./lightsctl.sh generate-from-template youtube-studio --add-to-workspace
./lightsctl.sh list-templates
```

### Fixture Groups

```bash
./lightsctl.sh group-list
./lightsctl.sh group-create "front" "0,3,4" "Front wash"
./lightsctl.sh group-scene front "warm and dim" --add-to-workspace
./lightsctl.sh group-template front youtube-studio --add-to-workspace
```

### Diagnostics

```bash
./lightsctl.sh diagnose            # full diagnostic dump
./lightsctl.sh doctor              # health check with recommendations
./lightsctl.sh wifi-status         # WiFi connection info
./lightsctl.sh test-dmx            # ENTTEC + DMX output verification
```

### System

```bash
./lightsctl.sh backup              # backup QLC+ config
./lightsctl.sh update              # apt update + upgrade
./lightsctl.sh ssh                 # interactive shell on the Pi
```

## Configuration

Environment variables in `.env` (copy from `.env.example`):

- `PI_HOST` - Pi hostname or IP (default: `lights.local`)
- `PI_USER` - SSH username (default: `pi`; `riversway` on this rig)
- `PI_HOSTNAME` - mDNS hostname (default: `lights`)
  - **Use `PI_HOSTNAME`, not `HOSTNAME` — the latter is a macOS shell builtin.**
- `QLC_PORT` - QLC+ web UI port (default: `9999`)
- `SSH_KEY` - path to SSH private key (optional)
- `BACKUP_STORAGE` - local backup directory (default: `./backups`)
- `WIFI1_SSID` / `WIFI1_PSK` ... - WiFi networks for provisioning
- `AI_PROVIDER` - `openai` | `anthropic` | `ollama`
- `AI_API_KEY` - API key for the chosen provider
- `AI_MODEL` - e.g. `gpt-4.1`, `claude-3-5-sonnet-20241022`, or local Ollama tag
- `AI_SCENE_STYLE` - default scene style (`complete` | `modular` | `timeline` |
  `reactive`)
- `MCP_PORT` - MCP server listen port (default: `5001`)
- `MCP_HOST` - MCP server bind address (default: `0.0.0.0`)
- `MCP_PATH` - MCP Streamable HTTP mount path (default: `/mcp`)
- `MCP_BEARER_TOKEN` - reserved for optional bearer-token auth on the MCP
  endpoint (scaffolded; not yet enforced)
- `CONTROL_URL` - URL the MCP server uses to reach the Flask control server
  (default: `http://localhost:5000`)

## Control Server Architecture

The Flask control server (`control-server/app.py`) maintains exactly **one**
persistent WebSocket to QLC+ on a dedicated background asyncio loop. All HTTP
requests dispatch sends to that loop via `asyncio.run_coroutine_threadsafe`.
This avoids the CLOSE_WAIT socket leak that occurs when each request opens its
own short-lived connection. See `docs/CONTROL_SERVER_ARCHITECTURE.md` for full
details.

Channel role inference is sourced from the QLC+ `.qxf` fixture definitions on
disk. The parser (`control-server/fixture_definitions.py`) reads each fixture's
`<Channel Preset="...">`, `<Group>`, `<Colour>`, and channel name to assign a
semantic role (`dimmer`, `red`, `warm`, `strobe`, `macro`, etc.). This metadata
is exposed via:

- `GET /api/fixtures` - includes `channel_info` per fixture
- `GET /api/fixture_channels/<id>` - per-fixture breakdown
- `POST /api/fixture_definitions/reload` - force cache rebuild

The same parser is invoked from `scripts/lib/extract_fixtures.py` so
`./lightsctl.sh generate-scene` enriches the AI prompt with authoritative
channel metadata too.

## MCP Server Architecture

The MCP server (`mcp-server/server.py`) uses FastMCP with the Streamable HTTP
transport and runs as a sibling Python process. It does **not** open its own
QLC+ connection — instead it makes HTTP calls into the Flask control server
at `http://localhost:5000`, preserving the "single writer" invariant for the
persistent QLC+ WebSocket.

The Flask app exposes `POST /api/action` as the structured-dispatch path the
MCP server uses, bypassing the AI interpreter (since the LLM is already on
the other end of the MCP connection). Read paths reuse the existing
`GET /api/fixtures`, `/api/groups`, `/api/scenes`, `/api/templates`,
`/api/channel_values`, and `/api/status` endpoints.

See `docs/MCP_SERVER.md` for the full tool/resource catalog and client
wiring (Claude Desktop, ChatGPT, Cursor, custom).

## Testing

No automated test suite. Manual verification via:

- `./lightsctl.sh health` - service, web UI, USB, system resources
- `./lightsctl.sh check` - connectivity pre-flight
- `./lightsctl.sh control-status` + `control-logs` - control server health
- `./lightsctl.sh mcp-status` + `mcp-logs` - MCP server health
- `curl http://lights.local:5000/api/status` - JSON service health summary
- Browser access to `http://lights.local:5000` (custom UI) and
  `http://lights.local:9999` (QLC+ web UI)
- `npx @modelcontextprotocol/inspector http://lights.local:5001/mcp` -
  interactive MCP inspector for tool/resource verification
