---
name: lights-pi
description: Operate, set up, brand and troubleshoot a Lights Pi rig (Raspberry Pi + QLC+ + ENTTEC DMX studio lighting controller, github.com/gfargo/lights-pi). Use this whenever the user talks about their lights, the rig, a scene, a chase, a cue list, DMX, QLC+, fixtures, groups, colour temperature or blackout, or has the Lights Pi MCP server (port 5001, /mcp) connected, or is working in the lights-pi repo (lightsctl.sh, scripts/deploy.sh, control-server/, mcp-server/). Also use it for requests like "make it warmer", "set up three-point lighting", "the lights stopped responding", "deploy to the Pi" or "rebrand the app", even when Lights Pi is not named.
---

# Lights Pi

Lights Pi turns a Raspberry Pi into a studio lighting controller:

```
agent --MCP--> lighting-mcp :5001/mcp --HTTP--> lighting-control :5000 (Flask) --WS--> QLC+ :9999 --USB--> ENTTEC --DMX--> fixtures
```

- **QLC+** (`qlcplus-web.service`, port 9999) drives DMX through an ENTTEC DMX USB Pro.
- **Control server** (`lighting-control.service`, port 5000) is the phone UI and the
  *single writer* to QLC+. Everything goes through it.
- **MCP server** (`lighting-mcp.service`, port 5001, Streamable HTTP at `/mcp`) is a thin
  wrapper over the control server's HTTP API. It holds no state; restarting it is safe.
- **Workstation ops**: `./lightsctl.sh <command>` (SSH helper) and `bash scripts/deploy.sh`
  (code deploy), both run from a checkout of the repo.

## Pick the job, then read one reference

| The user wants to... | Read |
|---|---|
| Change the lights: scenes, colour, Kelvin, brightness, chases, cue lists, groups, workspaces, blackout | [references/operating.md](references/operating.md) |
| Provision a Pi, deploy code, install the MCP server, edit secrets, reach the Pi remotely | [references/setup.md](references/setup.md) |
| Rename the app, change its accent colour, logo or CSS | [references/branding.md](references/branding.md) |
| Fix something: lights not responding, scene changes time out, Wi-Fi drops, a UI won't load | [references/troubleshooting.md](references/troubleshooting.md) |

Read only the reference you need. If the task spans two (for example "deploy, then check it
works"), read both.

## Which interface you have

1. **MCP tools are connected** (tools such as `list_fixtures`, `activate_scene`, `blackout`):
   operate the lights with them. This is the normal path for lighting work.
2. **You are in the repo with a shell**: use `./lightsctl.sh` and `scripts/deploy.sh` for
   ops, and `curl http://<pi-host>:5000/healthz` for a health check.
3. **Neither**: tell the user what to run, or help them connect the MCP server
   (setup.md, "Connect an MCP client").

Never talk to QLC+ on port 9999 directly to change lights. The control server owns the one
QLC+ WebSocket; a second writer causes conflicts.

## Rules that always apply

- **Discover before you act.** Start a lighting session with `get_status`, then
  `list_groups` and `list_fixtures` (or read the `lights://workspace` resource, which bundles
  status, fixtures, groups, scenes and templates). Use the group and scene names that
  actually exist; do not guess names like "key" or "front".
- **`blackout` is the safe stop.** If something looks wrong, a strobe is running, or the user
  says "stop" or "kill it", call `blackout` first and investigate second. Also stop anything
  playing: `get_active_cue_lists` then `stop_cue_list`, and `stop_chase`.
- **Do not strobe people without warning.** Before `strobe` or a fast chase, warn that
  flashing light is coming (photosensitivity) and get a yes. Keep rates modest and turn it
  off afterwards (`strobe(rate="off")`).
- **Confirm destructive changes.** `delete_scene`, `delete_chase`, `delete_cue_list`,
  `delete_group`, `delete_workspace`, `load_workspace` (switches the whole rig) and
  `update_group` with a replacement fixture list get a one-line confirmation unless the
  user asked for exactly that.
- **Save looks the user likes.** After building a look, offer `snapshot_scene` so it can be
  recalled later with `activate_scene`.
- **Secrets live only on the Pi.** `~/control-server/.env` on the Pi is the only copy of
  production secrets (`AI_API_KEY`, `LIGHTS_PASSWORD`, ...). `scripts/deploy.sh` does not sync
  `.env`. Never echo secret values back to the user.
- **Two kinds of "update".** `bash scripts/deploy.sh` deploys lights-pi code.
  `./lightsctl.sh update` only runs `apt update && apt upgrade` on the Pi.
- **After a deploy, hard-refresh the browser** (Cmd+Shift+R / Ctrl+Shift+R).
- **Use placeholders** like `<pi-host>` and `<pi-user>` in anything you write unless the user
  gave you real values. The default LAN name is `lights.local`.

## Quick recipes

- "Warmer / cooler": `color_temperature(kelvin=3200)` for tungsten, `5600` for daylight;
  optional `groups` and `intensity`.
- "Brighter / dimmer": `adjust_brightness(value="+30")`, `"-20"`, `"75%"`, or `0`-`255`.
- "Fade out over 5 seconds": `fade(target="0", duration="5")`.
- "Party mode": `apply_template(template="party")`; `list_templates` shows the rest.
- "Something like a sunset": `generate_scene(description="warm sunset")` (needs an AI
  provider configured on the Pi), then offer `snapshot_scene`.
- "Which light is fixture 3?": `identify_fixture(fixture_id=3)`.
- "Is DMX getting out at all?": `test_dmx()`, then `get_system_info()`.

The full tool catalog, arguments and lighting craft are in
[references/operating.md](references/operating.md).
