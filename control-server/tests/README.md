# Control server tests

This directory holds both **unit tests** (pure helpers, no I/O) and
**integration tests** (Flask routes, MCP tool dispatch, cue playback).

## Running

```bash
cd control-server
pip install -r requirements-dev.txt
pytest -v
```

All tests run together. CI runs the same suite on Python 3.11 and 3.12.

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

Three suites that exercise real system surfaces without hardware.

### Suite 1 — Flask routes (`test_flask_routes.py`)

Uses `app.test_client()` with two patches:
- `app._qlc_send_commands` → async recording mock (collects `CH|<ch>|<val>` strings)
- `app._qlc_run` → `asyncio.run()` on a fresh local loop (no background thread)

`WORKSPACE_PATH` is redirected to `integration/fixtures/test_workspace.qxw`
(two fixtures: a 1-channel dimmer and a 3-channel RGB). `GROUPS_FILE` and
`CUE_LISTS_FILE` are isolated to a `tmp_path`.

Covers: `/api/action`, `/api/blackout`, `/api/batch`, `/api/channel`,
`/api/cue_lists`. Request-validation paths (400 / 404) are also exercised.

**Not covered here (by design):** `/api/command` (needs AI provider);
`/api/channel_values` (needs live QLC+ WebSocket reply).

### Suite 2 — MCP compliance (`test_mcp_compliance.py`)

Imports `mcp-server/server.py` and replaces its global `_client` with an
`httpx.Client(transport=httpx.WSGITransport(app=flask_app))`. Tool functions
then call real Flask routes through the WSGI layer with no network.

The **48-tool count assertion** is intentionally brittle — it is the drift
detector: if a tool is added or removed, this number must be updated.

Covers: tool registry count + uniqueness, discovery tools (`_get` paths),
action tools (`_post` paths), cue list tools, group management.

### Suite 3 — Cue playback (`test_cue_playback.py`)

Calls `_run_cue_list_async` directly with injectable `now` / `sleep`
callables so the fake clock advances instantly. No background loop, no
real timers.

Covers: out-of-order input sorted by `at_ms`; timing accuracy via fake
clock; fault tolerance (bad cue doesn't abort remaining); `CancelledError`
propagation (stop_cue_list path); registry cleanup on completion and
cancellation.

**Out of scope:** pause / resume / seek — the engine has no such feature.

---

## Adding new integration tests

1. Drop a new `test_*.py` in `integration/`.
2. Use the `flask_client` fixture for Flask route tests:
   ```python
   def test_my_route(flask_client):
       client, recorded = flask_client
       r = client.post("/api/foo", json={...})
       assert r.status_code == 200
       assert any(cmd.startswith("CH|") for cmd in recorded)
   ```
3. Use the `mcp_flask_client` fixture for MCP tool tests:
   ```python
   def test_my_tool(mcp_flask_client):
       mcp_module, _, recorded = mcp_flask_client
       result = mcp_module.my_tool(arg="value")
       assert isinstance(result, dict)
   ```
4. For cue playback tests, use `_make_fake_clock()` from `test_cue_playback.py`
   and call `_run_cue_list_async` directly with the injected callables.

## What's NOT covered (yet)

- **AI provider calls** — mocked at `app.interpret_command` level; never hits a real API.
- **QLC+ WebSocket** — replaced entirely by the recording mock.
- **Real `.qxw` file mutations** — test workspace is read-only; write tests need a tmp copy.
- **Hitting a real QLC+ instance** — that's e2e, a separate effort.
- **Cue list pause / resume / seek** — engine doesn't implement these yet.
