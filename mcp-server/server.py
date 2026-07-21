#!/usr/bin/env python3
"""
QLC+ Lighting MCP Server

Exposes the Flask control-server REST API as MCP tools so LLM agents
(Claude Desktop, ChatGPT, Cursor, custom clients) can drive the studio
lights via the Model Context Protocol.

Transport: Streamable HTTP (default at http://0.0.0.0:5001/mcp)
Backend:   http://localhost:5000  (the control-server Flask app)

Tools are thin wrappers around REST endpoints — the Flask app owns the
persistent QLC+ WebSocket and remains the single writer.
"""

import hmac
import os
import sys
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

CONTROL_URL = os.getenv("CONTROL_URL", "http://localhost:5000").rstrip("/")
MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("MCP_PORT", "5001"))
MCP_PATH = os.getenv("MCP_PATH", "/mcp")

# Bearer token gate — disabled when unset (LAN-only deployments).
# LIGHTS_PASSWORD is the primary source (same shared secret as the control
# server's web login, issue #25); MCP_BEARER_TOKEN is kept as a fallback so
# existing MCP-only installs that never set LIGHTS_PASSWORD keep working.
MCP_BEARER_TOKEN = (
    os.getenv("LIGHTS_PASSWORD", "").strip()
    or os.getenv("MCP_BEARER_TOKEN", "").strip()
    or None
)

HTTP_TIMEOUT = float(os.getenv("MCP_HTTP_TIMEOUT", "30"))

mcp = FastMCP(
    name="qlc-lights",
    instructions=(
        "Control the Riversway studio lights via QLC+. Use list_fixtures, "
        "list_groups, and list_scenes to discover what's available before "
        "issuing commands. Prefer activate_scene or apply_template for known "
        "looks; use adjust_color / adjust_brightness for fine-tuning; use "
        "generate_scene only when no existing scene fits the request."
    ),
    host=MCP_HOST,
    port=MCP_PORT,
    streamable_http_path=MCP_PATH,
)

_client: httpx.Client | None = None


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(base_url=CONTROL_URL, timeout=HTTP_TIMEOUT)
    return _client


def _get(path: str) -> dict[str, Any]:
    r = _http().get(path)
    r.raise_for_status()
    return r.json()


def _post(path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    r = _http().post(path, json=payload or {})
    # Surface backend error bodies as MCP tool results rather than raising,
    # so the LLM sees the explanation instead of a generic 500.
    if r.status_code >= 400:
        try:
            return {"success": False, "status_code": r.status_code, **r.json()}
        except Exception:
            return {"success": False, "status_code": r.status_code, "error": r.text}
    return r.json()


def _delete(path: str) -> dict[str, Any]:
    r = _http().delete(path)
    if r.status_code >= 400:
        try:
            return {"success": False, "status_code": r.status_code, **r.json()}
        except Exception:
            return {"success": False, "status_code": r.status_code, "error": r.text}
    return r.json()


# ---------------------------------------------------------------------------
# Discovery tools — read-only, safe to call freely
# ---------------------------------------------------------------------------

@mcp.tool()
def get_status() -> dict:
    """Return overall system health: AI provider, QLC+ service, workspace, WebSocket."""
    return _get("/api/status")


@mcp.tool()
def list_fixtures() -> dict:
    """List every fixture in the loaded workspace with channel metadata.

    Each fixture has: id, name, manufacturer, model, mode, universe, address,
    channels, and channel_info (per-channel role/preset/colour from .qxf).
    """
    return _get("/api/fixtures")


@mcp.tool()
def get_fixture_channels(fixture_id: int) -> dict:
    """Return resolved per-channel info (offset, name, role, colour) for a single fixture."""
    return _get(f"/api/fixture_channels/{int(fixture_id)}")


@mcp.tool()
def list_groups() -> dict:
    """List fixture groups (named subsets of fixtures used to target a subset of lights)."""
    return _get("/api/groups")


@mcp.tool()
def list_scenes() -> dict:
    """List saved scene functions in the workspace (id, name, path, fixture_values count)."""
    return _get("/api/scenes")


@mcp.tool()
def list_templates() -> dict:
    """List built-in lighting templates (party, ambient, youtube-studio, spotlight, etc)."""
    return _get("/api/templates")


@mcp.tool()
def get_channel_values() -> dict:
    """Return the current live DMX channel values from QLC+ as a {channel: value} map."""
    return _get("/api/channel_values")


# ---------------------------------------------------------------------------
# Workspace tools — list, switch, create, and delete .qxw workspaces
# ---------------------------------------------------------------------------

@mcp.tool()
def list_workspaces() -> dict:
    """List all .qxw workspace files available on the Pi, with the active one flagged."""
    return _get("/api/workspaces")


@mcp.tool()
def get_current_workspace() -> dict:
    """Return the name and path of the currently active workspace."""
    return _get("/api/workspaces/current")


@mcp.tool()
def load_workspace(name: str) -> dict:
    """Switch the active workspace.

    Copies the named .qxw to default.qxw, updates the pointer, busts the
    scene-swatch cache, and restarts QLC+ (or returns needs_manual_restart
    when the sudoers config is absent).

    Args:
        name: Workspace stem or filename (e.g. 'venue-a' or 'venue-a.qxw').
    """
    safe = name if name.endswith(".qxw") else name + ".qxw"
    return _post(f"/api/workspaces/{safe}/load")


@mcp.tool()
def create_workspace(name: str, copy_from: str | None = None) -> dict:
    """Create a new workspace (empty skeleton or copied from an existing one).

    Args:
        name:      New workspace name (stem or .qxw filename).
        copy_from: Optional existing workspace to copy from.
    """
    payload: dict[str, Any] = {"name": name}
    if copy_from:
        payload["copy_from"] = copy_from
    return _post("/api/workspaces", payload)


@mcp.tool()
def delete_workspace(name: str) -> dict:
    """Delete a workspace. Refuses to delete the currently active workspace.

    Args:
        name: Workspace stem or filename (e.g. 'old-venue' or 'old-venue.qxw').
    """
    safe = name if name.endswith(".qxw") else name + ".qxw"
    return _delete(f"/api/workspaces/{safe}")


# ---------------------------------------------------------------------------
# Action tools — write operations that change the lights
# ---------------------------------------------------------------------------

@mcp.tool()
def activate_scene(scene: str) -> dict:
    """Apply an existing saved scene immediately. Accepts scene name or numeric ID."""
    return _post(f"/api/scenes/{scene}/activate")


@mcp.tool()
def apply_template(template: str, groups: list[str] | None = None) -> dict:
    """Apply a built-in template (e.g. 'party', 'ambient', 'youtube-studio').

    Args:
        template: Name from list_templates().
        groups:   Optional list of group names to target. Omit to apply to all fixtures.
    """
    return _post("/api/action", {
        "action": "apply_template",
        "parameters": {"template": template},
        "groups": groups,
    })


@mcp.tool()
def adjust_brightness(value: str, groups: list[str] | None = None) -> dict:
    """Set or nudge overall brightness on the master/dimmer/intensity channels.

    Args:
        value:  Absolute 0-255, percentage like '75%', or relative like '+30' / '-20'.
        groups: Optional list of group names to target. Omit for all fixtures.
    """
    return _post("/api/action", {
        "action": "adjust_brightness",
        "parameters": {"value": value},
        "groups": groups,
    })


@mcp.tool()
def adjust_color(
    color: str,
    intensity: str | None = None,
    groups: list[str] | None = None,
) -> dict:
    """Set the color of the lights.

    Args:
        color:     One of red, green, blue, purple, magenta, cyan, white, cool,
                   warm, amber. Maps to per-fixture RGBA/WWA channels via presets.
        intensity: Optional absolute 0-255, percentage like '75%', or relative
                   like '+30' / '-20'. Defaults to full intensity.
        groups:    Optional list of group names to target. Omit for all fixtures.
    """
    return _post("/api/action", {
        "action": "adjust_color",
        "parameters": {"color": color, "intensity": intensity or "255"},
        "groups": groups,
    })


# ---------------------------------------------------------------------------
# Cue lists — audio-synced show programming (issue #8)
# ---------------------------------------------------------------------------
#
# A cue list is the QLab / ETC Ion "cue stack" model: an ordered list of
# cues, each with an absolute timestamp relative to GO. Press GO and the
# server fires each cue at its time. Sync-mode only — the user runs their
# audio in OBS / Logic / etc. and presses GO at the same moment.

@mcp.tool()
def list_cue_lists() -> dict:
    """List every saved cue list with runtime status (whether it's currently playing)."""
    return _get("/api/cue_lists")


@mcp.tool()
def describe_cue_list(cue_list: str) -> dict:
    """Return a single cue list's full definition plus runtime status.

    Includes each cue's timestamp (in human-readable form like "0:32.500"
    and as raw at_ms), its action, parameters, and target groups.

    Accepts the cue list's numeric ID or case-insensitive name.
    """
    return _get(f"/api/cue_lists/{cue_list}")


@mcp.tool()
def get_active_cue_lists() -> dict:
    """List only cue lists currently playing, with elapsed time and cues
    fired so far. Empty list if nothing is running."""
    return _get("/api/cue_lists/active")


@mcp.tool()
def create_cue_list(
    name: str,
    cues: list[dict],
    description: str | None = None,
) -> dict:
    """Create a new cue list.

    Each cue is a dict with a timestamp and an action specification:

      Timestamp (use one):
        "at_ms":  32500
        "at":     "0:32.500"  or  "32s"  or  "32500ms"  or  "1:23:45"

      Action (use one):
        "scene":  "Chorus"                      # → activate_scene
        "chase":  "Sunset"                      # → start_chase
        "action": "strobe",  "parameters": {...}    # any execute_lighting_action
        "action": "blackout"                    # no parameters

      Optional:
        "groups": ["key-lights"]                # restrict to these groups

    Example — 30-second YouTube intro:

      create_cue_list(
        name="YouTube Intro",
        description="30s series intro",
        cues=[
          {"at": "0:00",     "scene": "Daylight"},
          {"at": "0:08",     "chase": "Sunset"},
          {"at": "0:15.500", "scene": "Warm"},
          {"at": "0:22",     "action": "strobe",   "parameters": {"rate": 8}},
          {"at": "0:24",     "action": "strobe",   "parameters": {"rate": "off"}},
          {"at": "0:28",     "action": "fade",     "parameters": {"target": "0", "duration": "2"}},
          {"at": "0:30",     "action": "blackout"},
        ],
      )

    Scene and chase references are validated against the workspace at
    creation time — broken refs cause the whole create to fail with a
    structured list of which cues are bad.
    """
    return _post("/api/cue_lists", {
        "name": name,
        "description": description or "",
        "cues": cues,
    })


@mcp.tool()
def update_cue_list(
    cue_list: str,
    new_name: str | None = None,
    description: str | None = None,
    cues: list[dict] | None = None,
) -> dict:
    """Update a cue list — rename, change description, or replace cues entirely.

    The cues array, if provided, REPLACES the existing one. To add or
    remove individual cues, read the current cues via describe_cue_list,
    modify the list, then pass the modified array here.

    Args:
        cue_list:    Current name or numeric ID.
        new_name:    Optional new name.
        description: Optional new description.
        cues:        Optional replacement cue array.
    """
    payload: dict[str, Any] = {}
    if new_name is not None:
        payload["name"] = new_name
    if description is not None:
        payload["description"] = description
    if cues is not None:
        payload["cues"] = cues
    r = _http().patch(f"/api/cue_lists/{cue_list}", json=payload)
    try:
        return r.json()
    except Exception:
        return {"success": r.status_code < 400, "status_code": r.status_code}


@mcp.tool()
def delete_cue_list(cue_list: str) -> dict:
    """Delete a cue list permanently. Stops playback first if it's running."""
    r = _http().delete(f"/api/cue_lists/{cue_list}")
    try:
        return r.json()
    except Exception:
        return {"success": r.status_code < 400, "status_code": r.status_code}


@mcp.tool()
def go_cue_list(cue_list: str) -> dict:
    """GO — start the cue list playing from the top.

    The first cue at "at: 0:00" fires immediately; subsequent cues fire
    at their at_ms relative to this GO moment. The server doesn't play
    audio itself — sync your audio source (OBS, Logic, etc.) to fire
    GO at the same moment as the track starts.

    If the cue list is already running, the old run is cancelled and a
    fresh run starts (matches "press GO twice = restart").
    """
    return _post(f"/api/cue_lists/{cue_list}/go", {})


@mcp.tool()
def stop_cue_list(cue_list: str) -> dict:
    """Stop a running cue list.

    Fixtures hold whatever state the last fired cue left them in —
    follow with blackout() or activate_scene() if you want a
    deterministic finish state.
    """
    return _post(f"/api/cue_lists/{cue_list}/stop", {})


# ---------------------------------------------------------------------------
# Chase management (issue #4) — time-based programming axis
# ---------------------------------------------------------------------------

@mcp.tool()
def list_chases() -> dict:
    """List every chase (Chaser function) in the loaded workspace.

    Returns each chase with id, name, step count, direction, run_order, and
    its function-level speed (fade_in_ms / hold_ms / fade_out_ms).
    """
    return _get("/api/chases")


@mcp.tool()
def describe_chase(chase: str) -> dict:
    """Return the full definition of a chase including each step's scene
    reference (with friendly name lookup) and per-step timing overrides.

    Useful before starting a chase or duplicating its structure into a
    new one. Accepts the chase's numeric ID or its name (case-insensitive).
    """
    return _get(f"/api/chases/{chase}")


@mcp.tool()
def create_chase(
    name: str,
    steps: list,
    fade_in_ms: int = 500,
    hold_ms: int = 2000,
    fade_out_ms: int = 500,
    direction: str = "Forward",
    run_order: str = "Loop",
    path: str = "AI Generated",
) -> dict:
    """Create a new chase referencing existing scenes.

    Each step can be:
      - a scene name string: "Warm Wash"
      - a scene numeric ID: 42
      - a dict with per-step overrides:
            {"scene": "Amber", "hold_ms": 4000, "fade_in_ms": 1000}

    Steps reference scenes by ID or case-insensitive name; the server
    resolves them and rejects the request if any step points at a
    non-existent scene.

    Args:
        name:        Display name for the chase (must be unique).
        steps:       Ordered list of steps. See above for accepted shapes.
        fade_in_ms:  Default fade-in time per step (ms). Per-step overrides
                     in the step dict take precedence.
        hold_ms:     Default hold time per step (ms).
        fade_out_ms: Default fade-out time per step (ms).
        direction:   "Forward" or "Backward".
        run_order:   "Loop", "SingleShot", "PingPong", or "Random".
        path:        Folder path within QLC+ for organization.

    To start the chase after creating it, call start_chase(name) or
    start_chase(id) with the returned chase ID.

    Example:
        create_chase(
            name="Sunset",
            steps=[
                {"scene": "Daylight", "hold_ms": 3000},
                {"scene": "Warm Wash", "hold_ms": 5000},
                {"scene": "Deep Amber", "hold_ms": 8000},
                {"scene": "Off",        "hold_ms": 2000},
            ],
            fade_in_ms=1500,
            fade_out_ms=1500,
            run_order="SingleShot",
        )
    """
    return _post("/api/chases", {
        "name": name,
        "steps": steps,
        "fade_in_ms":  int(fade_in_ms),
        "hold_ms":     int(hold_ms),
        "fade_out_ms": int(fade_out_ms),
        "direction":   direction,
        "run_order":   run_order,
        "path":        path,
    })


@mcp.tool()
def delete_chase(chase: str) -> dict:
    """Delete a chase from the workspace permanently.

    Accepts the chase's name or numeric ID. Idempotent — returns 404
    cleanly if the chase doesn't exist.
    """
    r = _http().delete(f"/api/chases/{chase}")
    try:
        return r.json()
    except Exception:
        return {"success": r.status_code < 400, "status_code": r.status_code}


@mcp.tool()
def start_chase(chase: str) -> dict:
    """Start chase playback. Accepts name or numeric ID.

    QLC+ runs the chase according to its direction + run_order — it will
    loop forever for run_order: "Loop", play once for "SingleShot", etc.
    Use stop_chase() to stop a running loop.
    """
    return _post(f"/api/chases/{chase}/start", {})


@mcp.tool()
def stop_chase(chase: str) -> dict:
    """Stop chase playback. Accepts name or numeric ID.

    The fixtures stay in whatever state the last step left them in —
    follow with blackout() or activate_scene() to set a new state.
    """
    return _post(f"/api/chases/{chase}/stop", {})


@mcp.tool()
def strobe(
    rate: str | float | int,
    intensity: str | int | None = None,
    groups: list[str] | None = None,
) -> dict:
    """Strobe the targeted fixtures at the given rate.

    First-class abstraction over each fixture's dedicated strobe channel —
    no need to know per-fixture channel offsets or DMX value ranges. The
    server maps rate → DMX value via the .qxf role parser.

    Args:
        rate:      Frequency in Hz (0-20). Use "off" / 0 / "0Hz" to stop.
                   Rates above 20Hz clamp to 20Hz (typically the fastest
                   stage fixtures will reliably strobe).
        intensity: Optional brightness applied to the fixture's dimmer
                   channel so the strobe is visible (0-255 / "75%" /
                   "+30" / "-20"). If omitted, brightness is left alone —
                   strobe happens at whatever level the fixture is
                   currently at.
        groups:    Optional list of group names to limit the strobe.
                   Omit to strobe every fixture with a strobe channel.

    Common rates:
        1-3 Hz   — slow heartbeat / breathing
        5-8 Hz   — typical party strobe
        12-15 Hz — aggressive accent
        18-20 Hz — pulse-machine territory

    Fixtures without a dedicated strobe channel are listed in the
    response under skipped — use blackout() and adjust_color() via
    batch_action for brightness-cycled "strobe" effects on those.
    """
    return _post("/api/action", {
        "action": "strobe",
        "parameters": {
            "rate": rate,
            "intensity": intensity,
        },
        "groups": groups,
    })


@mcp.tool()
def palette(assignments: dict) -> dict:
    """Assign different colors / Kelvin values to different groups in one
    round trip — the "set the room" primitive.

    Each entry in `assignments` maps a group name to a value. Value shapes:

      "warm"                                  → color preset name
      3200                                    → Kelvin number
      "5600K"                                 → Kelvin (with "K" suffix)
      {"color": "warm", "intensity": "70%"}   → explicit color
      {"kelvin": 3200, "intensity": "50%"}    → explicit Kelvin

    Numbers in the 1000–40000 range are interpreted as Kelvin; everything
    else as a color preset name (red, green, blue, warm, cool, amber,
    magenta, cyan, white, …).

    Use this for moves like three-point lighting:
        palette({
            "key-lights":  3200,         # tungsten
            "fill-lights": 5600,         # daylight
            "back-lights": "magenta",    # color accent
        })

    Returns per-group results so the agent can see what was applied to
    each group and detect partial failures.

    Args:
        assignments: dict mapping group name → value. Non-empty.
    """
    return _post("/api/action", {
        "action": "palette",
        "parameters": {"assignments": assignments},
    })


@mcp.tool()
def color_temperature(
    kelvin: float,
    intensity: str | int | None = None,
    groups: list[str] | None = None,
) -> dict:
    """Set the targeted fixtures to a Kelvin white balance.

    This is the right tool when an operator thinks in white-balance terms:
    "set the key lights to 5600K daylight", "drop the wash to 3200K tungsten",
    "candlelit mood at 1900K". The control server picks the right per-fixture
    strategy based on which color channels each fixture exposes — WWA fixtures
    use the warm + cool + amber mix, RGB fixtures use a CCT-to-RGB
    approximation, RGBW additionally drives the white channel.

    Args:
        kelvin:    Target color temperature in Kelvin. Clamped to 1800–10000.
                   Useful reference points:
                     - 1800: candle / firelight
                     - 2700: incandescent warm-white bulb
                     - 3200: tungsten / studio key
                     - 4000: cool-white fluorescent
                     - 5600: daylight
                     - 6500: pure white
                     - 7500: overcast / north-window
        intensity: Optional 0-255, percentage like "75%", or relative
                   "+30" / "-20". Defaults to full intensity.
        groups:    Optional list of group names to target. Omit for all
                   fixtures in the workspace.
    """
    return _post("/api/action", {
        "action": "color_temperature",
        "parameters": {
            "kelvin": float(kelvin),
            "intensity": intensity if intensity is not None else "255",
        },
        "groups": groups,
    })


@mcp.tool()
def fade(
    target: str = "0",
    duration: str = "3",
    groups: list[str] | None = None,
) -> dict:
    """Fade brightness to a target level over a duration in seconds.

    Args:
        target:   Absolute 0-255 (defaults to 0 = black).
        duration: Seconds for the fade (defaults to 3).
        groups:   Optional list of group names to target. Omit for all fixtures.
    """
    return _post("/api/action", {
        "action": "fade",
        "parameters": {"target": target, "duration": duration},
        "groups": groups,
    })


@mcp.tool()
def generate_scene(description: str, groups: list[str] | None = None) -> dict:
    """Generate a new scene from a natural-language description and apply it live.

    Uses the control-server's AI provider to synthesize fixture values from the
    description. The scene is applied immediately but NOT saved — call save_scene
    afterwards with the returned scene_xml if you want to persist it.

    Args:
        description: Free-form text, e.g. 'warm sunset', 'horror movie red glow'.
        groups:      Optional list of group names to target.
    """
    return _post("/api/action", {
        "action": "generate_scene",
        "parameters": {"description": description},
        "groups": groups,
    })


@mcp.tool()
def set_channel(fixture_id: int, channel: int, value: int) -> dict:
    """Set a single DMX channel on a fixture directly. Power-user escape hatch.

    Args:
        fixture_id: Fixture ID from list_fixtures().
        channel:    0-based channel offset within the fixture.
        value:      0-255.
    """
    return _post("/api/channel", {
        "fixture_id": int(fixture_id),
        "channel": int(channel),
        "value": max(0, min(255, int(value))),
    })


@mcp.tool()
def save_scene(
    name: str,
    scene_xml: str | None = None,
    snapshot: bool = False,
    path: str = "AI Generated",
) -> dict:
    """Save a scene to the workspace permanently.

    Provide either scene_xml (e.g. the value returned by generate_scene) OR
    set snapshot=True to capture the current live channel state.

    Args:
        name:      Display name for the saved scene.
        scene_xml: QLC+ Scene XML to inject. Mutually exclusive with snapshot.
        snapshot:  When True, capture current live state instead.
        path:      Folder path within QLC+ (defaults to 'AI Generated').
    """
    return _post("/api/scenes/save", {
        "name": name,
        "scene_xml": scene_xml or "",
        "snapshot": snapshot,
        "path": path,
    })


@mcp.tool()
def snapshot_scene(name: str, path: str = "AI Generated") -> dict:
    """Save the current live channel state as a new scene with the given name."""
    return _post("/api/scenes/snapshot", {"name": name, "path": path})


# ---------------------------------------------------------------------------
# Tier 1 — group CRUD
# ---------------------------------------------------------------------------

@mcp.tool()
def create_group(
    name: str,
    fixtures: list[int],
    description: str | None = None,
) -> dict:
    """Create a new fixture group (named subset of fixtures).

    Args:
        name:        Group name (must be unique). Used as the identifier.
        fixtures:    Fixture IDs to include. Use list_fixtures() to discover.
        description: Optional free-text description.
    """
    return _post("/api/groups", {
        "name": name,
        "fixtures": [int(f) for f in fixtures],
        "description": description or "",
    })


@mcp.tool()
def delete_group(name: str) -> dict:
    """Delete a fixture group. Returns 404 in the response if not found."""
    r = _http().delete(f"/api/groups/{name}")
    try:
        return r.json()
    except Exception:
        return {"success": r.status_code < 400, "status_code": r.status_code}


@mcp.tool()
def update_group(
    name: str,
    new_name: str | None = None,
    description: str | None = None,
    fixtures: list[int] | None = None,
) -> dict:
    """Rename a group, update its description, or replace its fixture list.

    Pass only the fields you want to change. The fixture list, if provided,
    REPLACES the existing one (use add_fixtures_to_group / remove_fixtures_from_group
    for incremental edits).
    """
    payload: dict[str, Any] = {}
    if new_name is not None:
        payload["name"] = new_name
    if description is not None:
        payload["description"] = description
    if fixtures is not None:
        payload["fixtures"] = [int(f) for f in fixtures]
    r = _http().patch(f"/api/groups/{name}", json=payload)
    try:
        return r.json()
    except Exception:
        return {"success": r.status_code < 400, "status_code": r.status_code}


@mcp.tool()
def add_fixtures_to_group(name: str, fixtures: list[int]) -> dict:
    """Append fixture IDs to an existing group (preserves current members)."""
    return _post(f"/api/groups/{name}/fixtures", {
        "fixtures": [int(f) for f in fixtures],
    })


@mcp.tool()
def remove_fixtures_from_group(name: str, fixtures: list[int]) -> dict:
    """Remove fixture IDs from a group. Missing IDs are ignored silently."""
    r = _http().request(
        "DELETE",
        f"/api/groups/{name}/fixtures",
        json={"fixtures": [int(f) for f in fixtures]},
    )
    try:
        return r.json()
    except Exception:
        return {"success": r.status_code < 400, "status_code": r.status_code}


# ---------------------------------------------------------------------------
# Tier 1 — scene management
# ---------------------------------------------------------------------------

@mcp.tool()
def describe_scene(scene: str) -> dict:
    """Return the contents of a saved scene: per-fixture channel values.

    Useful before activating a scene, or to reason about what to change.
    Returns the fixture name, each channel's offset/name/role, and its value.
    Accepts scene name or numeric ID.
    """
    return _get(f"/api/scenes/{scene}")


@mcp.tool()
def delete_scene(scene: str) -> dict:
    """Delete a saved scene from the workspace permanently. Accepts name or ID."""
    r = _http().delete(f"/api/scenes/{scene}")
    try:
        return r.json()
    except Exception:
        return {"success": r.status_code < 400, "status_code": r.status_code}


@mcp.tool()
def rename_scene(
    scene: str,
    new_name: str,
    path: str | None = None,
) -> dict:
    """Rename a saved scene. Accepts the current name or numeric ID.

    Args:
        scene:    Current name or ID of the scene.
        new_name: New name.
        path:     Optional new folder path within QLC+ (e.g. "AI Generated").
    """
    payload: dict[str, Any] = {"name": new_name}
    if path is not None:
        payload["path"] = path
    r = _http().patch(f"/api/scenes/{scene}", json=payload)
    try:
        return r.json()
    except Exception:
        return {"success": r.status_code < 400, "status_code": r.status_code}


@mcp.tool()
def duplicate_scene(scene: str, new_name: str) -> dict:
    """Copy an existing scene under a new name.

    Useful for "start from the warm scene but bluer" — duplicate, then
    use describe_scene + set_channel or save_scene to tweak the copy.
    """
    return _post(f"/api/scenes/{scene}/duplicate", {"name": new_name})


# ---------------------------------------------------------------------------
# Tier 1 — visual ping, safety, batch
# ---------------------------------------------------------------------------

@mcp.tool()
def identify_fixture(
    fixture_id: int,
    duration: float = 2.0,
    pulses: int = 4,
) -> dict:
    """Flash a single fixture so the operator can see which physical light it is.

    Pulses the brightness channels on-off-on-off for `duration` seconds
    (default 2s, max 10s), then restores the previous channel values.

    Useful during rig setup: "I'm flashing fixture 3, is that the front-left par?"

    Args:
        fixture_id: Fixture ID from list_fixtures().
        duration:   Total pattern length in seconds (0.5–10).
        pulses:     Number of on-off cycles within that duration (1–10).
    """
    return _post(f"/api/fixtures/{int(fixture_id)}/identify", {
        "duration": float(duration),
        "pulses": int(pulses),
    })


@mcp.tool()
def blackout(groups: list[str] | None = None) -> dict:
    """Instantly zero every channel on the targeted fixtures.

    Distinct from fade(target: "0"): blackout writes EVERY channel on the
    fixture, not just brightness-role channels, so any active strobe,
    macro, or color state is also cleared. Use for "kill it all" moments.

    Args:
        groups: Optional list of group names to target. Omit for the entire rig.
    """
    return _post("/api/blackout", {"groups": groups})


# ---------------------------------------------------------------------------
# Diagnostics (issue #9)
# ---------------------------------------------------------------------------

@mcp.tool()
def test_dmx(
    duration: float = 5.0,
    groups: list[str] | None = None,
) -> dict:
    """Run a known-good color sweep (red → green → blue → restore) across
    every targeted fixture's color channels.

    Used to verify DMX is actually reaching the rig — "if you don't see
    the sweep, the problem is somewhere between this server and the
    fixtures." Channel values are snapshotted before the test and
    restored at the end, so the rig returns to its pre-test look.

    Args:
        duration: Total seconds for the sweep (2.0–30.0, default 5.0).
        groups:   Optional list of group names to limit the test to.
                  Omit to sweep every fixture in the workspace.
    """
    return _post("/api/diagnostics/test_dmx", {
        "duration": float(duration),
        "groups": groups,
    })


@mcp.tool()
def get_logs(service: str, n: int = 50) -> dict:
    """Read the last N lines of a service's systemd journal.

    Useful when diagnosing a misbehaving rig — pull recent log lines
    instead of asking the user to SSH in.

    Args:
        service: One of "qlcplus-web", "lighting-control",
                 "lighting-mcp", "nginx". Other names are rejected.
        n:       Number of lines to return (1–500, default 50).
    """
    r = _http().get(f"/api/diagnostics/logs/{service}", params={"n": int(n)})
    try:
        return r.json()
    except Exception:
        return {"success": r.status_code < 400, "status_code": r.status_code}


@mcp.tool()
def get_system_info() -> dict:
    """Return Pi-level health: CPU temperature, load average, memory,
    disk usage, uptime, USB devices (ENTTEC filter), and systemd unit
    status for the lighting services.

    All fields are best-effort — anything not available on this platform
    (e.g. CPU temp on a non-Linux dev machine) is reported as null
    rather than failing the whole call.
    """
    return _get("/api/diagnostics/system")


@mcp.tool()
def batch_action(
    actions: list[dict],
    stop_on_error: bool = True,
) -> dict:
    """Execute an ordered list of actions in one HTTP round trip.

    Each item in `actions` is a dict with the same shape as a single
    /api/action call:
        { "action": "adjust_color", "parameters": {...}, "groups": [...] }

    Use this for compound moves like setting key/fill/back to different
    colors at once (3 actions → 1 round trip).

    Args:
        actions:       Ordered list of action specs.
        stop_on_error: If True (default), aborts at the first failure and
                       skips remaining steps. If False, continues through
                       errors and reports per-step results.
    """
    return _post("/api/batch", {
        "actions": actions,
        "stop_on_error": bool(stop_on_error),
    })


# ---------------------------------------------------------------------------
# Resources — discovery context bundled into one read
# ---------------------------------------------------------------------------

@mcp.resource("lights://workspace")
def workspace_resource() -> dict:
    """One-shot snapshot of fixtures, groups, scenes, templates, and status.

    Useful as a single context payload at the start of a session so the LLM
    knows what's available without making many tool calls.
    """
    return {
        "status": _safe_get("/api/status"),
        "fixtures": _safe_get("/api/fixtures").get("fixtures", []),
        "groups": _safe_get("/api/groups").get("groups", []),
        "scenes": _safe_get("/api/scenes").get("scenes", []),
        "templates": _safe_get("/api/templates").get("templates", []),
    }


def _safe_get(path: str) -> dict:
    try:
        return _get(path)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _bearer_ok(header: str | None, token: str) -> bool:
    """Constant-time check of an `Authorization: Bearer <token>` header."""
    if not header or not header.startswith("Bearer "):
        return False
    supplied = header[len("Bearer "):]
    return hmac.compare_digest(supplied, token)


class _BearerAuthMiddleware:
    """ASGI middleware rejecting requests without a valid bearer token."""

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        headers = dict(scope.get("headers") or [])
        auth_header = headers.get(b"authorization", b"").decode("latin-1") or None
        if not _bearer_ok(auth_header, self.token):
            response = JSONResponse({"error": "unauthorized"}, status_code=401)
            return await response(scope, receive, send)

        return await self.app(scope, receive, send)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"[mcp] backend: {CONTROL_URL}", file=sys.stderr)
    print(f"[mcp] listening: http://{MCP_HOST}:{MCP_PORT}{MCP_PATH}", file=sys.stderr)

    if MCP_BEARER_TOKEN:
        print("[mcp] bearer token auth enabled on /mcp", file=sys.stderr)
        import uvicorn

        http_app = mcp.streamable_http_app()
        http_app.add_middleware(_BearerAuthMiddleware, token=MCP_BEARER_TOKEN)
        uvicorn.run(http_app, host=MCP_HOST, port=MCP_PORT)
    else:
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
