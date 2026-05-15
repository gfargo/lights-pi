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

import os
import sys
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

CONTROL_URL = os.getenv("CONTROL_URL", "http://localhost:5000").rstrip("/")
MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("MCP_PORT", "5001"))
MCP_PATH = os.getenv("MCP_PATH", "/mcp")

# Bearer token gate — disabled when unset (LAN-only deployments).
MCP_BEARER_TOKEN = os.getenv("MCP_BEARER_TOKEN", "").strip() or None

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


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    if MCP_BEARER_TOKEN:
        # Reserved for future use — wire bearer-token auth here when needed.
        # FastMCP supports an OAuth/auth provider; a simple bearer-check ASGI
        # middleware can be attached to mcp.streamable_http_app() instead.
        print(f"[mcp] bearer token configured (length={len(MCP_BEARER_TOKEN)}) — auth enforcement not yet wired", file=sys.stderr)

    print(f"[mcp] backend: {CONTROL_URL}", file=sys.stderr)
    print(f"[mcp] listening: http://{MCP_HOST}:{MCP_PORT}{MCP_PATH}", file=sys.stderr)
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
