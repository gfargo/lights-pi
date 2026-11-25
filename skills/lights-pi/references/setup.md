# Setting up and maintaining an install

All commands run on the workstation from the repo root unless marked "on the Pi".
`./lightsctl.sh help` lists every command. `PI_HOST` and `PI_USER` come from the
workstation's `.env`, or pass them inline: `PI_HOST=<pi-host> PI_USER=<pi-user> ./lightsctl.sh status`.

## Contents

1. Provisioning order (new Pi)
2. Deploying code vs updating the OS
3. Secrets and `.env`
4. The MCP server
5. Connect an MCP client
6. Remote access (Tailscale)
7. Workspaces, fixtures and backups

## 1. Provisioning order (new Pi)

1. **SD card:** Raspberry Pi Imager, Raspberry Pi OS Lite, hostname `lights` (must match
   `PI_HOSTNAME` in `.env`), SSH enabled, a user name, Wi-Fi.
2. **Workstation config:** `cp .env.example .env`, then set at least `PI_HOST`, `PI_USER`,
   `PI_HOSTNAME`.
3. **Base install and hardening:**
   ```bash
   WIFI1_SSID="<setup-ssid>" WIFI1_PSK="<pass>" \
   WIFI2_SSID="<studio-ssid>" WIFI2_PSK="<pass>" \
   ./lightsctl.sh setup-full        # = setup, then harden; add PI_MODEL=3 or 4 to skip the prompt
   ```
   `harden` adds the firewall, hardware watchdog, unattended upgrades and the udev rule
   that gives the ENTTEC a stable `/dev/dmx0`.
4. **Verify:** `./lightsctl.sh doctor` and `./lightsctl.sh test-dmx`.
5. **Control server (port 5000):** `./lightsctl.sh control-install`.
6. **MCP server (port 5001):** `./lightsctl.sh mcp-install`. It is not part of provisioning;
   until it runs, Pi health reports `lighting-mcp: not_installed`.
7. **Optional:** `./lightsctl.sh setup-ssl` (local HTTPS), `./lightsctl.sh landing-setup`
   (landing page), `./lightsctl.sh wifi-watchdog-install` (Wi-Fi auto-recovery, recommended),
   `./lightsctl.sh tailscale-https install` (see section 6).
8. **Load a workspace:** `./lightsctl.sh deploy-workspace <file>.qxw`, or design it in the
   QLC+ web UI at `http://<pi-host>:9999`.

## 2. Deploying code vs updating the OS

| Command | What it does |
|---|---|
| `bash scripts/deploy.sh` | Deploys lights-pi code: rsyncs `control-server/`, `scripts/` and `lightsctl.sh` to the Pi, restarts `lighting-control.service`, then polls `/healthz` and fails loudly if it never answers. |
| `./lightsctl.sh update` | OS packages only (`apt update && apt upgrade`). Does not touch lights-pi code. |
| `./lightsctl.sh update-qlc` | Updates QLC+ itself. |

- `deploy.sh` honours an explicit host: `PI_HOST=<pi-host> PI_USER=<pi-user> bash scripts/deploy.sh`.
  Pass both in a worktree or fresh checkout that has no `.env`.
- `deploy.sh` does not reinstall systemd units and does not sync `mcp-server/`. To pick up
  MCP server code changes, re-run `./lightsctl.sh mcp-install` (then `mcp-restart`). If a
  release changes the control server's unit file, re-run `./lightsctl.sh control-install`
  and then `sudo systemctl restart lighting-control` on the Pi (install uses
  `systemctl start`, which is a no-op on a running unit).
- `deploy.sh` excludes `.env`, `static/logo.*` and `static/theme.css`, so a deploy never
  clobbers secrets or branding.
- **After every deploy, hard-refresh the browser** (Cmd+Shift+R / Ctrl+Shift+R), or the old
  CSS/JS stays cached.
- Before committing changes in the repo, run `cd control-server && python -m pytest -q`.

## 3. Secrets and `.env`

- `~/control-server/.env` on the Pi is the **only** copy of production secrets
  (`AI_PROVIDER`, `AI_API_KEY`, `AI_MODEL`, `LIGHTS_PASSWORD`, `FLASK_SECRET_KEY`, `BRAND_*`).
  Worktrees and fresh checkouts have no `.env`.
- Edit it on the Pi: `./lightsctl.sh ssh`, `nano ~/control-server/.env`, then
  `sudo systemctl restart lighting-control`.
- **Careful:** `./lightsctl.sh env-sync`, `control-install` and `mcp-install` copy the
  *workstation's* `.env` onto the Pi (`~/`, `~/control-server/`, `~/mcp-server/`). If the
  workstation file lacks the Pi's secrets, that overwrites them. Back up the Pi's copy first
  (on the Pi: `cp ~/control-server/.env ~/control-server/.env.bak`) or confirm the
  workstation copy is complete.
- `LIGHTS_PASSWORD` turns on the shared login for the web UI and Bearer auth on `/mcp`.
  Set `FLASK_SECRET_KEY` alongside it so sessions survive restarts. Unset means open mode.
- The MCP server reads `~/mcp-server/.env`: `CONTROL_URL` (default `http://localhost:5000`),
  `MCP_HOST`, `MCP_PORT` (5001), `MCP_PATH` (`/mcp`), `LIGHTS_PASSWORD`, `MCP_BEARER_TOKEN`
  (legacy fallback), `MCP_HTTP_TIMEOUT` (30 s).
- Never print secret values. Refer to them by variable name.

## 4. The MCP server

```bash
./lightsctl.sh mcp-install     # venv, code, systemd unit, start
./lightsctl.sh mcp-status
./lightsctl.sh mcp-logs
./lightsctl.sh mcp-restart     # after .env or code changes
./lightsctl.sh mcp-uninstall
```

It calls the control server over localhost and has no state, so restarting it is always
safe. The control server has matching `control-status`, `control-logs`, `control-restart`.

## 5. Connect an MCP client

Endpoint: `http://<pi-host>:5001/mcp` (Streamable HTTP). Claude Desktop / Cursor config:

```json
{ "mcpServers": { "lights": { "url": "http://lights.local:5001/mcp" } } }
```

If `LIGHTS_PASSWORD` is set on the Pi, the client must send
`Authorization: Bearer <LIGHTS_PASSWORD>`. Claude Code:
`claude mcp add --transport http lights http://<pi-host>:5001/mcp` (add
`--header "Authorization: Bearer <password>"` when auth is on). For a quick test:
`npx @modelcontextprotocol/inspector http://<pi-host>:5001/mcp`.

## 6. Remote access (Tailscale)

- `lights.local` (mDNS) only works on the studio LAN and can be flaky. Off-site, or when
  `lightsctl.sh` hangs on connect, use the Pi's Tailscale MagicDNS name:
  `PI_HOST=<pi-name>.<tailnet>.ts.net ./lightsctl.sh status`.
- Install on the Pi: `curl -fsSL https://tailscale.com/install.sh | sh`, then
  `sudo tailscale up` and authorise the device. Details in `docs/TAILSCALE.md`.
- Ports over the tailnet are the same as on the LAN (5000, 5001, 9999).
- `./lightsctl.sh tailscale-https install` (also `status`, `remove`) serves trusted HTTPS:
  `:5443` control server, `:9443` QLC+, `:443` landing page.
- Tailscale Funnel makes the rig public. Only with `LIGHTS_PASSWORD` set.

## 7. Workspaces, fixtures and backups

```bash
./lightsctl.sh deploy-workspace workspaces/<file>.qxw
./lightsctl.sh pull-workspace workspaces/<file>.qxw     # after editing in the QLC+ web UI
./lightsctl.sh set-default-workspace <file>.qxw
./lightsctl.sh list-fixtures
./lightsctl.sh install-fixture path/to/fixture.qxf
./lightsctl.sh backup                                   # QLC+ config to ./backups
./lightsctl.sh restore <backup.tar.gz>
./lightsctl.sh backup-timer-install                     # scheduled backups on the Pi
```

The Pi runs QLC+ 4.14.x. Workspaces saved by QLC+ 5.x need converting (see
`docs/CUSTOMIZATION.md`, "QLC+ Workspace").
