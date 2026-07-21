# Control Server Architecture

The Flask app at `control-server/app.py` runs as `lighting-control.service` on
the Pi and serves both the live UI and a JSON API at port `5000`. It sits
between user input (browser, voice, AI chat) and QLC+'s WebSocket on port
`9999`.

```
Browser/voice          Flask routes              persistent WebSocket
  ─────────► /api/command ─────► interpret_command ──┐
             /api/scenes/<id>/activate   │           │
             /api/groups/...             ▼           ▼
             /api/channel              execute_lighting_action
             /api/channel_values         │           │
                                         ▼           │
                                 set_channel_values ─┤
                                 fade_brightness    ─┤
                                 apply_color_live   ─┤
                                                     ▼
                                          ┌─────────────────────┐
                                          │  _qlc_run(coro)     │
                                          │  (run on dedicated  │
                                          │   asyncio loop      │
                                          │   in worker thread) │
                                          └──────────┬──────────┘
                                                     ▼
                                          single persistent WS
                                          ws://localhost:9999/qlcplusWS
                                                     ▼
                                                  QLC+ engine
                                                     ▼
                                                ENTTEC USB
                                                     ▼
                                               DMX fixtures
```

## Why a Single Persistent WebSocket

QLC+ 4.14.x has a hard cap (~50) on simultaneous WebSocket clients. Earlier
versions of this server opened a new connection for every send and called
`transport.abort()` to close it. That works for a few requests, then breaks.

The reason: `transport.abort()` discards the connection without sending a
TCP `FIN`. When QLC+ closes its end of the connection, the local socket sits
in `CLOSE_WAIT` until Python's garbage collector eventually runs the socket's
`__del__`. Under load, sockets accumulate in `CLOSE_WAIT` faster than they
clean up, and once we hit 50 the QLC+ accept queue fills, every new handshake
silently times out, and "Failed to apply scene via WebSocket" errors begin.

The current architecture avoids this entirely: **one WebSocket, opened once,
held for the lifetime of the process**. Reconnection happens lazily only when
the existing connection is verifiably dead (the reader task exited).

### Implementation Details

- A daemon thread runs `_qlc_loop`, a dedicated `asyncio` event loop. The
  WebSocket lives on this loop because `websockets.WebSocketClientProtocol`
  is bound to whichever loop created it.
- `_qlc_ws` is the global connection reference, guarded by `_qlc_ws_lock`.
- `_ensure_qlc_ws()` opens the connection if missing or closed. Before
  opening a fresh one it explicitly `await ws.close()`s the old one (with a
  1s timeout) so the underlying TCP socket is properly torn down.
- `_qlc_reader(ws)` continuously drains incoming messages. When QLC+ sends an
  unsolicited `getChannelsValues` reply, the reader matches it against any
  pending request waiting via `_qlc_pending_responses`. When the reader exits
  (connection drop, error), it explicitly closes the socket and clears
  `_qlc_ws` so the next caller reconnects.
- Flask request handlers call `_qlc_run(coro, timeout=N)` to dispatch
  coroutines onto the QLC+ loop. Internally that uses
  `asyncio.run_coroutine_threadsafe`.
- `set_channel_values()`, `_fade_brightness_async()`, and
  `_fetch_channel_values()` all share the same persistent connection,
  serialized by the lock.

### Health Reporting

`GET /api/status` reports `qlc_ws.ok = True` only when the persistent
WebSocket is currently open. It does **not** open a fresh TCP probe — under
load QLC+ may not accept new TCP connections within a tight timeout even
though the existing WebSocket is functioning fine.

## Observability

### Structured Logging

The server uses [`structlog`](https://www.structlog.org/) for structured
logging. Two environment variables control output:

| Variable | Default | Values |
|---|---|---|
| `LOG_FORMAT` | `json` | `json` — JSON lines for journald/prod; `console` — human-readable for local dev |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

Standard fields on log events:

- `event` — the log event name (e.g. `scene_activated`, `chase_started`, `qlc_ws_connected`)
- `source` — origin of the action: `web` (HTTP routes), `cue` (cue-list playback)
- `scene` / `chase` / `chase_id` — for scene/chase events
- `timestamp` — ISO-8601, added automatically by structlog

In production, `journalctl -u lighting-control -f` emits JSON lines parseable
by any log aggregator. Set `LOG_FORMAT=console` in `.env` for dev.

Per-frame DMX sends are logged at `DEBUG` level (`event: dmx_frame`). Enable
with `LOG_LEVEL=DEBUG` — expect high volume on any scene with animation.

### Deep Health Endpoint

`GET /healthz` checks every critical subsystem and returns JSON:

```json
{
  "flask": true,
  "qlc_ws": true,
  "dmx_device": "/dev/ttyUSB0",
  "last_dmx_write_age_s": 3.2,
  "workspace_loaded": true
}
```

HTTP 200 if all critical checks pass; **503** if any are red.

| Field | Green condition |
|---|---|
| `flask` | Always `true` — if the response arrives, Flask is up |
| `qlc_ws` | Persistent WebSocket to QLC+ is open and not closed |
| `dmx_device` | A `/dev/ttyUSB*` or `/dev/ttyACM*` device is present **and readable** |
| `last_dmx_write_age_s` | Seconds since the last DMX frame; `null` if no writes yet |
| `workspace_loaded` | Workspace `.qxw` file exists and parses as valid XML |

`dmx_device` and `last_dmx_write_age_s` are informational only — they do not
affect the HTTP status code. `qlc_ws` and `workspace_loaded` are the critical
checks that drive the 200/503 response.

Use with external monitors (Uptime Kuma, Healthchecks.io):

```
curl -sf http://lights.local:5000/healthz | jq
```

## Fixture Definition Parsing

`control-server/fixture_definitions.py` reads `.qxf` files from
`/usr/share/qlcplus/fixtures/` and `~/.qlcplus/fixtures/` and resolves a
**semantic role** for each channel. The role lookup precedence is:

1. `<Channel Preset="...">` attribute (e.g. `IntensityRed`, `IntensityAmber`,
   `IntensityMasterDimmer`, `ColourMacro`, `ShutterStrobeSlowFast`).
2. `<Colour>` subtag (e.g. `White` is split into `warm` / `cool` based on
   the channel name; `Amber` → `amber`; `UV` → `uv`).
3. Exact channel name match (e.g. `Master Dimmer`, `Warm White`).
4. Group-based classification (`Intensity` → `dimmer`, `Shutter` → `strobe`,
   `Colour` → `macro`).
5. Fuzzy substring match — only applied for `Intensity`-group channels and
   channels with no explicit group, to avoid falsely matching configuration
   knobs (Speed, Maintenance, Effect groups always return `None`).

The result is a list of `FixtureChannel` objects per `FixtureMode`, each with
`offset`, `name`, `preset`, `group`, `colour`, and `role`.

### How the Control Server Uses Roles

- `_fixture_roles(fixture)` maps role names to channel offsets:
  `{"dimmer": 0, "warm": 1, "cool": 2, "amber": 3, "brightness": [0]}`.
- `apply_color_live()` writes color preset values for matching color roles
  and explicitly zeros every other channel that isn't a color/dimmer/motion
  channel — so leftover macro/strobe/program state from a previous scene
  never bleeds into the new one.
- `fade_brightness_live()` operates only on the offsets in
  `roles["brightness"]` (the dedicated dimmer when present, otherwise all
  RGB-ish channels).

### How AI Scene Generation Uses Roles

`scripts/lib/extract_fixtures.py` invokes the same parser to emit enriched
JSON for the AI prompt. Each fixture in the prompt now includes its full
`channel_info` array — name, role, preset, group, colour, and absolute DMX
channel number. The system prompt in `scripts/lib/ai_scene.sh` instructs the
LLM to:

- Pick channels by **role**, not by guessing offsets
- Never write to channels with `role: null` (configuration knobs)
- Mix warm/cool/amber on fixtures that lack RGB (e.g. SlimPAR Pro W)
- Set `macro` and `strobe` to 0 for static colored scenes

This is what stopped the AI from accidentally triggering the strobe channel
on the SlimPAR Pro W when asked for "soft warm light".

## API Surface

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Live control UI |
| POST | `/api/command` | AI natural-language command (interprets, then dispatches) |
| POST | `/api/action` | Structured action dispatch — skips the AI interpreter (used by the MCP server) |
| GET | `/api/status` | Multi-service health JSON |
| GET | `/api/templates` | List built-in scene templates |
| GET | `/api/scenes` | List Engine scenes from workspace |
| GET | `/api/scenes/<id>` | Describe a scene — per-fixture channel breakdown |
| POST | `/api/scenes/<id>/activate` | Apply existing workspace scene live |
| POST | `/api/scenes/save` | Save a scene (XML or snapshot) into the workspace |
| POST | `/api/scenes/snapshot` | Snapshot the current live state as a new scene |
| POST | `/api/scenes/<id>/duplicate` | Clone a scene under a new name |
| PATCH | `/api/scenes/<id>` | Rename a scene and/or move its folder Path |
| DELETE | `/api/scenes/<id>` | Delete a scene from the workspace |
| GET | `/api/groups` | List fixture groups |
| POST | `/api/groups` | Create a fixture group |
| PATCH | `/api/groups/<name>` | Rename / re-describe / replace fixture list |
| DELETE | `/api/groups/<name>` | Delete a fixture group |
| POST | `/api/groups/<name>/fixtures` | Append fixtures to a group |
| DELETE | `/api/groups/<name>/fixtures` | Remove fixtures from a group |
| POST | `/api/groups/<name>/template` | Apply template to a group |
| GET | `/api/fixtures` | List fixtures with `channel_info` |
| GET | `/api/fixture_channels/<id>` | Per-fixture channel breakdown |
| POST | `/api/fixtures/<id>/identify` | Pulse a fixture so the operator can locate it |
| POST | `/api/fixture_definitions/reload` | Rebuild `.qxf` cache |
| POST | `/api/channel` | Set a single fixture channel value |
| GET | `/api/channel_values` | Live DMX channel values from QLC+ |
| POST | `/api/blackout` | Instantly zero every channel on targeted fixtures |
| POST | `/api/batch` | Execute an ordered list of actions in one request |
| POST | `/api/diagnostics/test_dmx` | Run R→G→B→restore sweep across fixtures |
| GET | `/api/diagnostics/logs/<service>` | Tail systemd journal for an allowlisted service |
| GET | `/api/diagnostics/system` | Pi-level health JSON (temp, load, memory, disk, uptime, USB) |
| GET | `/api/chases` | List chases in the workspace |
| GET | `/api/chases/<id>` | Describe a chase (full step list, scene names, timing) |
| POST | `/api/chases` | Create a new chase from name + ordered step list |
| DELETE | `/api/chases/<id>` | Remove a chase from the workspace |
| POST | `/api/chases/<id>/start` | Start chase playback (QLC+API setFunctionStatus 1) |
| POST | `/api/chases/<id>/stop` | Stop chase playback (QLC+API setFunctionStatus 0) |
| GET | `/api/cue_lists` | List saved cue lists + runtime status |
| GET | `/api/cue_lists/active` | Currently-playing cue lists, with elapsed time |
| GET | `/api/cue_lists/<id>` | Describe a cue list (full cue array, timing) |
| POST | `/api/cue_lists` | Create a new cue list |
| PATCH | `/api/cue_lists/<id>` | Rename / re-describe / replace cues array |
| DELETE | `/api/cue_lists/<id>` | Remove (stops playback first if running) |
| POST | `/api/cue_lists/<id>/go` | GO — start cue-list playback from the top |
| POST | `/api/cue_lists/<id>/stop` | Halt running cue list |
| POST | `/api/chat` | Agentic chat — server-side tool-use loop over Anthropic/OpenAI with access to ~39 tools |

### `/api/chat` — agentic conversation

The web UI's Chat tab posts the full message history here, the server runs
an Anthropic `tool_use` or OpenAI function-calling loop (provider selected
by `AI_PROVIDER`), and returns the updated history plus a trace of which
tools were dispatched. Stateless — the client owns the conversation.

Tools internally dispatch via Flask's test client back into our own
endpoints (`POST /api/blackout`, `POST /api/cue_lists/<id>/go`, etc.), so
the chat agent behaves exactly the same as the MCP server (which is also
a thin wrapper over the same endpoints). 39-tool curated registry built
in `_build_chat_tools()`; mirrors the MCP catalog with a few power-user
tools omitted (`set_channel`, `batch_action`).

Provider support:

- `anthropic` — native `tool_use` blocks, recommended
- `openai`    — native function-calling, supported
- `ollama`    — not supported (tool-calling support varies by model)

Body:
```json
{
  "messages": [
    { "role": "user", "content": "Set the key lights to 3200K." }
  ]
}
```

Response includes the full updated `messages` array (assistant text +
tool_use blocks + tool_result blocks), a flat `tool_calls` summary for
telemetry, and `stop_reason` (`end_turn` / `max_iters` / `error`).
Bounded `max_iters=10` per call to prevent runaway loops.

### `/api/action` vs `/api/command`

Both routes ultimately call `execute_lighting_action`. The difference is the
front end:

- `/api/command` accepts free-form text, runs it through the AI interpreter
  (OpenAI / Anthropic / Ollama), and dispatches the resulting action. Used by
  the web UI's chat input.
- `/api/action` accepts a structured `{action, parameters, groups}` payload
  and dispatches directly. Used by the **MCP server** at `:5001/mcp`, where an
  LLM agent is already on the other end of the connection — calling
  `/api/command` would double-LLM.

Schema for `POST /api/action`:

```json
{
  "action": "adjust_color",
  "parameters": { "color": "warm", "intensity": "75%" },
  "groups": ["key-lights"]
}
```

Valid actions match `execute_lighting_action`'s dispatcher: `apply_template`,
`generate_scene`, `adjust_brightness`, `adjust_color`, `color_temperature`,
`palette`, `strobe`, `fade`, `activate_scene`.

> **Note**: `palette` ignores the top-level `groups` argument — its
> assignments dict's keys *are* the targets.

## Failure Modes & Recovery

- **QLC+ wedged after a previous bad version leaked sockets**: restart QLC+
  with `./lightsctl.sh restart`. The control server will reconnect on the
  next request.
- **Control server can't open the initial WebSocket**: it prints
  `✗ QLC+ WebSocket connect failed: ...` and continues. The next API call
  triggers a lazy retry.
- **Connection drops mid-session**: the reader task exits, explicitly closes
  the socket, and clears `_qlc_ws`. The next request reopens.
- **AI prompt times out**: OpenAI/Anthropic latency dominates total request
  time (1-20s typical). `execute_ms` in the `/api/command` response shows
  pure DMX time; subtract from `total_ms` for AI inference time.
