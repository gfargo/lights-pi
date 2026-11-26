# Control server tests

This directory holds both **unit tests** (pure helpers, no I/O) and
**integration tests** (Flask routes, MCP tool dispatch, cue playback).

## Running

```bash
cd control-server
pip install -r requirements-dev.txt
pytest -v
```

All tests run together. CI runs the same suite on Python 3.11, 3.12 and 3.13
(3.13 is what the Pi runs).

---

## Unit tests (this directory)

Pure-function unit tests for the helpers that have grown out of the v2.3 →
v2.11 buildout. Anything that can be tested without standing up Flask, the
QLC+ WebSocket, or external AI providers lives here.

| File | What it tests |
|------|---------------|
| `test_time_parser.py` | `_parse_time_ms` and `_format_time_ms` — cue list timestamp parsing (`"0:32.500"`, `"32s"`, `"1:23:45"`, int ms) |
| `test_cct.py` | `_cct_to_rgb` Tanner Helland algorithm + `_wwa_mix` warm/cool/amber proportional blend |
| `test_strobe.py` | `_strobe_dmx_value` rate → DMX 0..255 mapping, off forms, clamping |
| `test_palette.py` | `_normalize_palette_value` — all six accepted shapes per group |
| `test_cue_normalizer.py` | `_normalize_cue` — scene / chase / action cue shapes |
| `test_misc_helpers.py` | Direction + run-order enums, fixture ID coercion, `_parse_level` (0-255 / `'75%'` / `'+30'`) |

---

## Integration tests (`integration/`)

These exercise the real system surfaces without hardware: Flask routes, MCP
tool dispatch and cue playback.

### The sandbox (`conftest.py`)

The `patched_app` fixture (and `flask_client` / `mcp_flask_client` built on
it) gives every test:

- `_qlc_send_commands` replaced by a recorder that collects the
  `CH|<channel>|<value>` frames that would have gone to QLC+;
- `_qlc_run` running coroutines on a fresh local loop (no background thread);
- a tmp_path copy of `integration/fixtures/test_workspace.qxw` (a 1-channel
  dimmer, ID 1, and a 3-channel RGB, ID 2), so tests may write to it;
- every `~/.qlcplus` data file (groups, cue lists, stage layout, OSC, MIDI,
  RF, last look, audio chases) and the chat database moved to tmp_path.

Nothing a test does reaches the developer's real rig data or QLC+.

### Flask

| File | What it proves |
|------|---------------|
| `test_route_smoke.py` | **Every** route in the URL map answers without a 5xx when called with unknown IDs and an empty body. New routes are picked up automatically. Routes that can't run here (the SSE stream, AI, microphone, Wi-Fi scan, direct-WebSocket DMX test) are listed in `SKIPPED` with the reason. |
| `test_lifecycles.py` | The happy path: scenes saved, described, renamed, duplicated, snapshotted and deleted; groups built and used as a target; cue lists edited; stage layout, conversations and OSC settings stored and read back. |
| `test_flask_routes.py` | The QLC+ frames sent by `/api/action`, `/api/blackout`, `/api/batch` and `/api/channel`, plus their 400/404 paths. |

### MCP

| File | What it proves |
|------|---------------|
| `test_mcp_routes.py` | For **every** registered tool: it makes at least one request, each request's method and path match a Flask route, and every POST/PATCH body key is read by the Flask handler (or a helper it calls). Tools are discovered from the registry, so a new tool is checked with no edits here. Requests go to a recording transport, never to Flask. |
| `test_mcp_compliance.py` | The tool count (`EXPECTED_TOOL_COUNT`, a deliberate drift detector: update it when you add a tool), unique names, descriptions, and a sample of tools called through to the real Flask app. |
| `test_mcp_auth.py` | Bearer-token middleware on the streamable HTTP transport. |

### Cue playback (`test_cue_playback.py`)

Calls `_run_cue_list_async` with injected `now` / `sleep` callables, so a
fake clock advances instantly. Covers ordering by `at_ms`, timing, a failing
cue not aborting the rest, cancellation and registry cleanup. The engine has
no pause / resume / seek, so there is nothing to test there yet.

Also here: `test_mock_chase.py`, `test_tempo_edge_cases.py` and
`test_workspace_concurrency.py`.

---

## Adding new integration tests

- **A new Flask route** is smoke-tested automatically. If it can't run in the
  sandbox (it streams, calls the AI provider, opens a device), add it to
  `SKIPPED` in `test_route_smoke.py` with the reason. If it reports a service
  being down with a 503, add it to `UNAVAILABLE_OK`. Then add a happy-path
  test to `test_lifecycles.py` or a new file:
  ```python
  def test_my_route(flask_client):
      client, recorded = flask_client
      r = client.post("/api/foo", json={...})
      assert r.status_code == 200
      assert "CH|2|255" in recorded
  ```
- **A new MCP tool** is checked by `test_mcp_routes.py` automatically. Bump
  `EXPECTED_TOOL_COUNT` in `test_mcp_compliance.py`. To call it through the
  real Flask app, use `mcp_flask_client`:
  ```python
  def test_my_tool(mcp_flask_client):
      mcp_module, _, recorded = mcp_flask_client
      assert mcp_module.my_tool(arg="value")["success"] is True
  ```
- **Cue playback:** use `_make_fake_clock()` from `test_cue_playback.py` and
  call `_run_cue_list_async` with the injected callables.

## What's NOT covered

- **AI provider calls:** `/api/chat` and `/api/command` are skipped; the
  provider is never called.
- **The QLC+ WebSocket itself:** replaced by the recorder. Code that writes
  to the socket directly (the DMX test pattern) needs the real link.
- **A real QLC+ instance:** that's end-to-end, a separate effort.
- **Cue list pause / resume / seek:** not implemented in the engine.
