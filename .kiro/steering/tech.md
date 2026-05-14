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

## Testing

No automated test suite. Manual verification via:

- `./lightsctl.sh health` - service, web UI, USB, system resources
- `./lightsctl.sh check` - connectivity pre-flight
- `./lightsctl.sh control-status` + `control-logs` - control server health
- `curl http://lights.local:5000/api/status` - JSON service health summary
- Browser access to `http://lights.local:5000` (custom UI) and
  `http://lights.local:9999` (QLC+ web UI)
