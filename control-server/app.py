#!/usr/bin/env python3
"""
Natural Language Lighting Control Server
Interprets natural language commands and adjusts QLC+ workspace in real-time
Also provides direct fixture/group controls with QLC+ WebSocket integration
"""

import os
import sys
import json
import socket
import subprocess
import xml.etree.ElementTree as ET
import asyncio
import math
import time
import tempfile
import websockets
from pathlib import Path
from typing import Dict
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Local QLC+ fixture definition parser (.qxf)
sys.path.insert(0, str(Path(__file__).parent))
import fixture_definitions

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Configuration
SCRIPT_DIR = Path(__file__).parent.parent
LIGHTSCTL = SCRIPT_DIR / "lightsctl.sh"
# Default to ~/.qlcplus/default.qxw, but can be overridden via env var
WORKSPACE_PATH = Path(os.getenv("QLC_WORKSPACE", str(Path.home() / ".qlcplus" / "default.qxw")))
GROUPS_FILE = Path.home() / ".qlcplus" / "fixture_groups.json"

# QLC+ WebSocket configuration
QLC_HOST = os.getenv("QLC_HOST", "localhost")
QLC_PORT = int(os.getenv("QLC_PORT", "9999"))
QLC_WS_URL = f"ws://{QLC_HOST}:{QLC_PORT}/qlcplusWS"

# AI Configuration from environment
AI_PROVIDER = os.getenv("AI_PROVIDER", "openai")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "gpt-4.1" if os.getenv("AI_PROVIDER", "openai") == "openai" else "claude-3-5-sonnet-20241022")

SERVICE_NAME = os.getenv("SERVICE", "qlcplus-web.service")


def _is_local():
    """Detect whether we're running on the same host as QLC+.

    Returns True when QLC_HOST resolves to a loopback address or to one of
    this machine's own IPs, meaning we can use local systemctl / file
    operations instead of SSH.
    """
    if QLC_HOST in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        target_ip = socket.gethostbyname(QLC_HOST)
    except socket.gaierror:
        return False
    if target_ip.startswith("127."):
        return True
    # Check if the resolved IP belongs to one of our own interfaces
    try:
        local_ip = socket.gethostbyname(socket.gethostname())
        if target_ip == local_ip:
            return True
    except socket.gaierror:
        pass
    # Also compare against the hostname set in PI_HOSTNAME / HOSTNAME
    try:
        pi_hostname = os.getenv("PI_HOSTNAME", "")
        if pi_hostname and socket.gethostname().lower().startswith(pi_hostname.lower()):
            return True
    except Exception:
        pass
    return False


IS_LOCAL = _is_local()

# ---------------------------------------------------------------------------
# QLC+ WebSocket — single persistent connection on a background event loop
# ---------------------------------------------------------------------------
# QLC+ 4.14.1 has a hard limit (~50) on concurrent WebSocket clients. Each
# call to websockets.connect() leaves the underlying TCP socket in CLOSE_WAIT
# when QLC+ closes its end (transport.abort() doesn't flush the FIN). Within
# minutes the limit is exhausted and new handshakes silently time out.
#
# The fix: maintain ONE long-lived WebSocket on a dedicated asyncio loop in a
# background thread. All Flask requests dispatch sends to that loop via a
# thread-safe call. The connection auto-reconnects if QLC+ drops it.

import threading
import concurrent.futures

_qlc_loop: asyncio.AbstractEventLoop = None  # type: ignore
_qlc_loop_thread: threading.Thread = None  # type: ignore
_qlc_ws = None  # the actual websocket connection (lives on _qlc_loop)
_qlc_ws_lock: asyncio.Lock = None  # type: ignore
_qlc_pending_responses = {}  # request_id -> Future for QLC+API replies


def _start_qlc_loop():
    """Start the dedicated background event loop used for QLC+ comms."""
    global _qlc_loop, _qlc_loop_thread, _qlc_ws_lock

    if _qlc_loop is not None and _qlc_loop.is_running():
        return

    ready = threading.Event()

    def _run_loop():
        global _qlc_loop, _qlc_ws_lock
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _qlc_loop = loop
        _qlc_ws_lock = asyncio.Lock()
        ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()

    _qlc_loop_thread = threading.Thread(target=_run_loop, daemon=True, name="qlc-ws-loop")
    _qlc_loop_thread.start()
    ready.wait(timeout=5)


def _qlc_run(coro, timeout=10):
    """Submit a coroutine to the QLC+ background loop and wait for the result."""
    if _qlc_loop is None:
        _start_qlc_loop()
    future = asyncio.run_coroutine_threadsafe(coro, _qlc_loop)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        future.cancel()
        raise


async def _ensure_qlc_ws():
    """Open the persistent WebSocket if needed. Lock-protected."""
    global _qlc_ws
    async with _qlc_ws_lock:
        # Treat both "missing" and "closed" cases as needing reconnect
        needs_connect = _qlc_ws is None
        if not needs_connect:
            try:
                # websockets >=10 exposes .closed; older uses .open
                if getattr(_qlc_ws, "closed", False):
                    needs_connect = True
            except Exception:
                needs_connect = True
        if needs_connect:
            # Make sure the previous connection (if any) is fully torn down
            # before opening a new one — otherwise the old TCP socket sits in
            # CLOSE_WAIT until garbage collection runs, eventually exhausting
            # QLC+'s connection slot pool.
            old_ws = _qlc_ws
            _qlc_ws = None
            if old_ws is not None:
                try:
                    await asyncio.wait_for(old_ws.close(), timeout=1.0)
                except Exception:
                    pass
            try:
                _qlc_ws = await websockets.connect(
                    QLC_WS_URL,
                    open_timeout=3,
                    close_timeout=1,
                    ping_interval=20,
                    ping_timeout=10,
                    max_size=2 ** 20,
                )
                # Start a background reader so QLC+ pushes don't fill the recv buffer
                asyncio.create_task(_qlc_reader(_qlc_ws))
                print(f"✓ QLC+ WebSocket connected at {QLC_WS_URL}")
            except Exception as e:
                _qlc_ws = None
                print(f"✗ QLC+ WebSocket connect failed: {type(e).__name__}: {e}")
                raise
        return _qlc_ws


async def _qlc_reader(ws):
    """Continuously drain incoming messages, dispatching API replies to waiters.

    Takes the websocket as an explicit argument so we don't race against
    reassignment of the global. When the connection drops, we explicitly close
    it to avoid leaking the underlying TCP socket into CLOSE_WAIT.
    """
    global _qlc_ws
    try:
        async for msg in ws:
            # Dispatch QLC+API responses to any pending request waiting on it
            for key, fut in list(_qlc_pending_responses.items()):
                if key in msg and not fut.done():
                    fut.set_result(msg)
                    _qlc_pending_responses.pop(key, None)
                    break
    except Exception as e:
        print(f"QLC+ WebSocket reader exited: {type(e).__name__}: {e}")
    finally:
        # Drop the global reference so the next caller reconnects, and
        # explicitly close the underlying connection so the OS frees the FD
        # and QLC+ reclaims the slot — without this, sockets pile up in
        # CLOSE_WAIT for ~minutes until Python GC runs the destructor.
        try:
            await asyncio.wait_for(ws.close(), timeout=1.0)
        except Exception:
            pass
        if _qlc_ws is ws:
            _qlc_ws = None


async def _qlc_send_commands(commands):
    """Send one or more raw QLC+ commands over the persistent WebSocket."""
    ws = await _ensure_qlc_ws()
    async with _qlc_ws_lock:
        for command in commands:
            await ws.send(command)


async def _qlc_request_reply(command, response_marker, timeout=2.0):
    """Send a command and wait for a response containing response_marker."""
    ws = await _ensure_qlc_ws()
    fut = asyncio.get_running_loop().create_future()
    _qlc_pending_responses[response_marker] = fut
    try:
        async with _qlc_ws_lock:
            await ws.send(command)
        return await asyncio.wait_for(fut, timeout=timeout)
    finally:
        _qlc_pending_responses.pop(response_marker, None)


# ---------------------------------------------------------------------------
# Public API used by the rest of app.py
# ---------------------------------------------------------------------------

# Global QLC+ WebSocket connection (legacy reference — no longer used directly,
# kept for any external code that imports it)
qlc_websocket = None


async def connect_to_qlc():
    """Legacy stub kept for compatibility — uses the persistent connection."""
    try:
        await _ensure_qlc_ws()
        return True
    except Exception:
        return False


async def send_qlc_command(command):
    """Send a single command to QLC+ via the persistent WebSocket."""
    try:
        await _qlc_send_commands([command])
        return True
    except Exception as e:
        print(f"send_qlc_command error: {e}")
        return False


def set_channel_value(universe, address, value):
    """Set a single DMX channel via QLC+."""
    absolute_address = (universe * 512) + address
    return set_channel_values([(absolute_address, value)])


def _run_async(coro):
    """Compatibility shim. New code should use _qlc_run() for QLC+ work."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def send_qlc_commands(commands):
    """Send one or more QLC+ commands. Uses the persistent connection."""
    try:
        await _qlc_send_commands(commands)
        return True
    except Exception as e:
        print(f"send_qlc_commands error: {e}")
        return False


def set_channel_values(channel_values):
    """Set absolute QLC+ channel values via the persistent WebSocket.

    Args:
        channel_values: iterable of (absolute_channel, value), both 1-based / 0-255
    """
    commands = []
    for channel, value in channel_values:
        try:
            ch = int(channel)
            val = max(0, min(255, int(value)))
        except (TypeError, ValueError):
            continue
        if ch > 0:
            commands.append(f"CH|{ch}|{val}")
    if not commands:
        return True
    try:
        _qlc_run(_qlc_send_commands(commands), timeout=5)
        return True
    except Exception as e:
        print(f"set_channel_values error: {e}")
        return False


def _workspace_root():
    tree = ET.parse(WORKSPACE_PATH)
    return tree.getroot()


def _fixture_elements(root):
    ns = {'qlc': 'http://www.qlcplus.org/Workspace'}
    return root.findall(".//qlc:Fixture", ns)


def _fixture_to_dict(fixture):
    ns = {'qlc': 'http://www.qlcplus.org/Workspace'}
    def text(tag, default=""):
        elem = fixture.find(f"qlc:{tag}", ns)
        return elem.text if elem is not None and elem.text is not None else default

    return {
        "id": int(text("ID", "0")),
        "name": text("Name"),
        "universe": int(text("Universe", "0")),
        "address": int(text("Address", "0")),
        "channels": int(text("Channels", "1")),
        "manufacturer": text("Manufacturer"),
        "model": text("Model"),
        "mode": text("Mode"),
    }


def get_workspace_fixtures():
    """Return fixture metadata from the configured workspace."""
    if not WORKSPACE_PATH.exists():
        return []
    root = _workspace_root()
    return [_fixture_to_dict(f) for f in _fixture_elements(root)]


def _engine_element(root):
    ns = {'qlc': 'http://www.qlcplus.org/Workspace'}
    engine = root.find("qlc:Engine", ns)
    if engine is not None:
        return engine
    return root.find("Engine")


def get_workspace_scenes():
    """Return real Engine scene functions, excluding Virtual Console references."""
    if not WORKSPACE_PATH.exists():
        return []
    root = _workspace_root()
    engine = _engine_element(root)
    if engine is None:
        return []

    ns = "http://www.qlcplus.org/Workspace"
    scenes = []
    for func in engine.findall(f"{{{ns}}}Function") + engine.findall("Function"):
        if func.get("Type") != "Scene":
            continue
        fid = func.get("ID")
        if not fid or not fid.isdigit():
            continue
        scenes.append({
            "id": int(fid),
            "name": func.get("Name", f"Scene {fid}"),
            "path": func.get("Path", ""),
            "fixture_values": len(_find_children(func, "FixtureVal")),
        })
    return scenes


def get_next_scene_id():
    """Return next available scene/function ID from Engine functions only."""
    scenes = get_workspace_scenes()
    if not scenes:
        return 0
    return max(scene["id"] for scene in scenes) + 1


def _find_children(element, tag):
    ns = "http://www.qlcplus.org/Workspace"
    return element.findall(f"{{{ns}}}{tag}") + element.findall(tag)


def _find_scene_element(scene_id_or_name):
    root = _workspace_root()
    engine = _engine_element(root)
    if engine is None:
        return None
    needle = str(scene_id_or_name).strip().lower()
    ns = "http://www.qlcplus.org/Workspace"
    for func in engine.findall(f"{{{ns}}}Function") + engine.findall("Function"):
        if func.get("Type") != "Scene":
            continue
        fid = func.get("ID", "")
        name = func.get("Name", "")
        if fid == str(scene_id_or_name) or name.lower() == needle:
            return func
    return None


def _scene_root_from_xml(scene_xml):
    scene_xml = scene_xml.strip()
    if not scene_xml:
        raise ValueError("Empty scene XML")
    return ET.fromstring(scene_xml)


def scene_to_channel_values(scene_root):
    """Convert a QLC+ scene Function element to absolute channel/value pairs.

    Existing QLC+ workspace scenes use zero-based FixtureVal channels, while
    generated scenes in this project use one-based channels. Detect either form
    per fixture by checking whether channel 0 appears.
    """
    fixtures = {str(f["id"]): f for f in get_workspace_fixtures()}
    updates = []

    for fixture_val in _find_children(scene_root, "FixtureVal"):
        fixture_id = fixture_val.get("ID")
        fixture = fixtures.get(str(fixture_id))
        if not fixture or not fixture_val.text:
            continue

        raw_parts = [p.strip() for p in fixture_val.text.split(",") if p.strip() != ""]
        pairs = []
        for i in range(0, len(raw_parts) - 1, 2):
            try:
                pairs.append((int(raw_parts[i]), int(raw_parts[i + 1])))
            except ValueError:
                continue
        if not pairs:
            continue

        zero_based = any(channel == 0 for channel, _ in pairs)
        for channel, value in pairs:
            offset = channel if zero_based else channel - 1
            if offset < 0 or offset >= fixture["channels"]:
                continue
            absolute_channel = fixture["universe"] * 512 + fixture["address"] + offset + 1
            updates.append((absolute_channel, value))

    return updates


def apply_scene_xml_live(scene_xml):
    """Apply generated scene XML immediately via WebSocket channel updates."""
    scene_root = _scene_root_from_xml(scene_xml)
    updates = scene_to_channel_values(scene_root)
    if not updates:
        return {
            "success": False,
            "output": "",
            "error": "Scene has no applicable FixtureVal channel values",
        }
    success = set_channel_values(updates)
    return {
        "success": success,
        "output": f"Applied {len(updates)} channel values live via WebSocket",
        "error": "" if success else "Failed to apply channel values via WebSocket",
    }


def apply_existing_scene_live(scene_id_or_name):
    scene = _find_scene_element(scene_id_or_name)
    if scene is None:
        return {
            "success": False,
            "output": "",
            "error": f"Scene not found: {scene_id_or_name}",
        }
    updates = scene_to_channel_values(scene)
    if not updates:
        return {
            "success": False,
            "output": "",
            "error": f"Scene has no applicable channel values: {scene.get('Name', scene_id_or_name)}",
        }
    success = set_channel_values(updates)
    scene_name = scene.get("Name", str(scene_id_or_name))
    return {
        "success": success,
        "output": f"Applied scene '{scene_name}' live via WebSocket ({len(updates)} channel values)",
        "error": "" if success else f"Failed to apply scene '{scene_name}' via WebSocket",
    }


async def _fetch_channel_values(max_ch):
    """Fetch live channel values via the persistent QLC+ WebSocket."""
    values = {}
    try:
        msg = await _qlc_request_reply(
            f"QLC+API|getChannelsValues|1|1|{max_ch}",
            response_marker="getChannelsValues",
            timeout=2.0,
        )
    except (asyncio.TimeoutError, Exception) as e:
        print(f"channel_values fetch error: {e}")
        return values

    parts = msg.split("|")
    # QLC+ 4.14.1 live response starts channel/value groups at index 2:
    # QLC+API|getChannelsValues|1|0||2|0||...
    for i in range(2, len(parts) - 1, 3):
        try:
            values[int(parts[i])] = int(parts[i + 1])
        except (ValueError, IndexError):
            continue
    return values


def get_current_channel_values(max_ch=None):
    if max_ch is None:
        max_ch = 32
        for fixture in get_workspace_fixtures():
            max_ch = max(max_ch, fixture["universe"] * 512 + fixture["address"] + fixture["channels"])
    try:
        return _qlc_run(_fetch_channel_values(max_ch), timeout=4)
    except Exception as e:
        print(f"channel_values fetch error: {e}")
        return {}


def _fixture_channels_info(fixture):
    """Return resolved channel info for a fixture, sourced from .qxf when available.

    Returns a list of dicts with keys: offset, name, role, preset, group, colour.
    Falls back to a synthetic list (Ch 1, Ch 2, ...) when no definition is found.
    """
    manufacturer = fixture.get("manufacturer", "") or ""
    model = fixture.get("model", "") or ""
    mode_name = fixture.get("mode", "") or ""

    mode = fixture_definitions.get_mode(manufacturer, model, mode_name)
    if mode is None or not mode.channels:
        # Fall back: synthesize generic channel info from the heuristic roles
        roles = _fixture_roles_heuristic(fixture)
        offset_to_role: Dict[int, str] = {}
        for role, val in roles.items():
            if role == "brightness":
                continue
            if isinstance(val, int):
                offset_to_role[val] = role
        info = []
        for offset in range(int(fixture.get("channels", 0))):
            role = offset_to_role.get(offset)
            info.append({
                "offset": offset,
                "name": role.title() if role else f"Ch {offset + 1}",
                "role": role,
                "preset": None,
                "group": None,
                "colour": None,
            })
        return info

    return [ch.to_dict() for ch in mode.channels]


def _fixture_roles(fixture):
    """Resolve role offsets for a fixture using its .qxf definition.

    Returns a dict like:
        {"dimmer": 0, "warm": 1, "cool": 2, "amber": 3, "brightness": [0]}

    If the .qxf is missing or doesn't cover this mode, falls back to the
    legacy heuristic that inspects fixture name + channel count.
    """
    manufacturer = fixture.get("manufacturer", "") or ""
    model = fixture.get("model", "") or ""
    mode_name = fixture.get("mode", "") or ""

    mode = fixture_definitions.get_mode(manufacturer, model, mode_name)
    if mode is not None and mode.channels:
        return mode.role_offsets()

    return _fixture_roles_heuristic(fixture)


def _fixture_roles_heuristic(fixture):
    """Legacy heuristic-based role inference. Used only when no .qxf is found.

    Channel offsets are 0-based within the fixture's DMX footprint.
    Returns a dict keyed by role name. "brightness" is a list of offsets
    that should track overall intensity (used by adjust_brightness/fade).
    """
    channels = fixture["channels"]
    name = f"{fixture.get('manufacturer', '')} {fixture.get('model', '')} {fixture.get('name', '')}".lower()

    # Generic 3-channel RGB par (e.g. SlimPAR 56 in 3-Ch mode)
    if channels == 3:
        return {"red": 0, "green": 1, "blue": 2, "brightness": [0, 1, 2]}

    # Chauvet SlimPAR Pro W (9-Channel mode): dimmer + WWA + macros/strobe/programs
    # CH1 Master Dimmer, CH2 Warm, CH3 Cool, CH4 Amber, CH5 Macro, CH6 Strobe,
    # CH7 Auto Programs, CH8 Auto Speed, CH9 Dimmer Speed Mode
    if "pro w" in name and channels >= 9:
        return {
            "dimmer": 0,
            "warm": 1,
            "cool": 2,
            "amber": 3,
            "macro": 4,
            "strobe": 5,
            "brightness": [0],
        }

    # Chauvet SlimPAR Pro H (7-Channel mode): dimmer + RGBA + macro + strobe
    if "pro h" in name and channels >= 7:
        return {
            "dimmer": 0,
            "red": 1,
            "green": 2,
            "blue": 3,
            "amber": 4,
            "macro": 5,
            "strobe": 6,
            "brightness": [0],
        }

    # Generic 7-channel RGBA par fallback
    if channels == 7:
        return {
            "dimmer": 0,
            "red": 1,
            "green": 2,
            "blue": 3,
            "amber": 4,
            "brightness": [0],
        }

    # Generic 8+ channel RGBW par fallback (only used when Pro W/Pro H didn't match)
    if channels >= 7:
        return {
            "dimmer": 0,
            "red": 1,
            "green": 2,
            "blue": 3,
            "white": 4,
            "brightness": [0],
        }

    return {"dimmer": 0, "brightness": [0]}


def _absolute_channel(fixture, offset):
    return fixture["universe"] * 512 + fixture["address"] + offset + 1


def _parse_level(value, current=None, default=200):
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    if text.endswith("%"):
        try:
            return round(max(0, min(100, float(text[:-1]))) * 255 / 100)
        except ValueError:
            return default
    try:
        if text[0] in "+-" and current is not None:
            return max(0, min(255, int(current) + int(float(text))))
        return max(0, min(255, int(float(text))))
    except ValueError:
        return default


def _group_fixture_ids(target_groups):
    if not target_groups:
        return None
    if not GROUPS_FILE.exists():
        return set()
    try:
        groups_data = json.loads(GROUPS_FILE.read_text())
    except Exception:
        return set()
    groups_dict = groups_data.get("groups", groups_data)
    fixture_ids = set()
    for group_name in target_groups:
        group = groups_dict.get(group_name)
        if group:
            fixture_ids.update(str(fid) for fid in group.get("fixtures", []))
    return fixture_ids


def _target_fixtures(target_groups=None):
    fixtures = get_workspace_fixtures()
    fixture_ids = _group_fixture_ids(target_groups)
    if fixture_ids is None:
        return fixtures
    return [fixture for fixture in fixtures if str(fixture["id"]) in fixture_ids]


def apply_brightness_live(value, target_groups=None):
    fixtures = _target_fixtures(target_groups)
    current = get_current_channel_values()
    updates = []
    for fixture in fixtures:
        roles = _fixture_roles(fixture)
        for offset in roles.get("brightness", []):
            absolute = _absolute_channel(fixture, offset)
            updates.append((absolute, _parse_level(value, current.get(absolute), default=200)))
    success = set_channel_values(updates)
    return {
        "success": success,
        "output": f"Applied brightness to {len(updates)} channels live via WebSocket",
        "error": "" if success else "Failed to apply brightness via WebSocket",
    }


COLOR_PRESETS = {
    "red":     {"red": 255, "green": 0,   "blue": 0,   "amber": 0,   "warm": 0,   "cool": 0},
    "green":   {"red": 0,   "green": 255, "blue": 0,   "amber": 0,   "warm": 0,   "cool": 0},
    "blue":    {"red": 0,   "green": 0,   "blue": 255, "amber": 0,   "warm": 0,   "cool": 0},
    "purple":  {"red": 200, "green": 0,   "blue": 255, "amber": 0,   "warm": 0,   "cool": 80},
    "magenta": {"red": 255, "green": 0,   "blue": 255, "amber": 0,   "warm": 0,   "cool": 0},
    "cyan":    {"red": 0,   "green": 255, "blue": 255, "amber": 0,   "warm": 0,   "cool": 200},
    # Pro W has no white channel — it produces white via warm+cool together
    "white":   {"red": 220, "green": 220, "blue": 220, "white": 200, "amber": 0,   "warm": 200, "cool": 200},
    "cool":    {"red": 180, "green": 220, "blue": 255, "white": 180, "amber": 0,   "warm": 0,   "cool": 255},
    "warm":    {"red": 255, "green": 170, "blue": 80,  "white": 80,  "amber": 180, "warm": 255, "cool": 0},
    "amber":   {"red": 255, "green": 120, "blue": 0,   "amber": 255, "warm": 120, "cool": 0},
}


def apply_color_live(color, intensity=None, target_groups=None):
    color_key = str(color or "white").strip().lower()
    preset = COLOR_PRESETS.get(color_key, COLOR_PRESETS["white"])
    relative = str(intensity or "").strip().startswith(("+", "-"))
    fixtures = _target_fixtures(target_groups)
    current = get_current_channel_values()
    updates = []

    # Roles whose values we set explicitly from the color preset
    color_roles = {"red", "green", "blue", "white", "warm", "cool", "amber",
                   "uv", "cyan", "magenta", "yellow", "indigo", "lime"}
    # Roles we leave alone: dimmer (set explicitly above), pan/tilt (motion)
    keep_alone_roles = {"dimmer", "pan", "tilt"}

    for fixture in fixtures:
        roles = _fixture_roles(fixture)
        if "dimmer" in roles and not relative:
            updates.append((_absolute_channel(fixture, roles["dimmer"]), _parse_level(intensity, default=220)))

        for role, base in preset.items():
            if role not in roles:
                continue
            absolute = _absolute_channel(fixture, roles[role])
            if relative:
                value = _parse_level(intensity, current.get(absolute), default=current.get(absolute, 0))
            else:
                scale = _parse_level(intensity, default=255) / 255
                value = round(base * scale)
            updates.append((absolute, value))

        # Zero non-color, non-dimmer, non-motion control channels on absolute
        # color sets so leftover macro/strobe/program/speed values don't bleed
        # through from previously applied scenes.
        if not relative:
            color_offsets = {
                roles[r] for r in color_roles
                if r in roles and isinstance(roles[r], int)
            }
            keep_offsets = {
                roles[r] for r in keep_alone_roles
                if r in roles and isinstance(roles[r], int)
            }
            channel_count = int(fixture.get("channels", 0))
            for offset in range(channel_count):
                if offset in color_offsets or offset in keep_offsets:
                    continue
                updates.append((_absolute_channel(fixture, offset), 0))

    success = set_channel_values(updates)
    return {
        "success": success,
        "output": f"Applied {color_key} to {len(updates)} channels live via WebSocket",
        "error": "" if success else f"Failed to apply {color_key} via WebSocket",
    }


# ----------------------------------------------------------------------------
# Color temperature — Kelvin-based white balance (issue #6)
# ----------------------------------------------------------------------------
#
# Per-fixture-type strategy:
#
#   • Warm + cool (+ optional amber) — WWA/tungsten fixtures like the
#     SlimPAR Pro W. Linear mix between warm and cool channels by Kelvin
#     position. Below 2700K we blend toward amber for candle-warmth.
#
#   • RGB / RGBA / RGBW — drive the RGB channels with Tanner Helland's
#     CCT-to-RGB approximation. RGBW additionally pushes the white
#     channel at full so cooler colors look clean rather than blue.
#
#   • White-only or dimmer-only — set the channel at intensity. Includes a
#     per-fixture note in the response since CCT can't actually be expressed.
#
# Kelvin is clamped to 1800–10000 (candle → overcast sky).


def _cct_to_rgb(kelvin: float) -> tuple[int, int, int]:
    """Approximate the color of a black-body radiator at the given Kelvin
    temperature as 8-bit RGB. Uses Tanner Helland's algorithm — widely-cited,
    accurate enough for stage lighting (where the eye + fixture optics smear
    away any precision past ~50K anyway).

    Reference: https://tannerhelland.com/2012/09/18/convert-temperature-rgb-algorithm-code.html
    """
    temp = max(1000.0, min(40000.0, float(kelvin))) / 100.0

    # Red
    if temp <= 66:
        r = 255.0
    else:
        r = temp - 60.0
        r = 329.698727446 * (r ** -0.1332047592)

    # Green
    if temp <= 66:
        g = 99.4708025861 * math.log(temp) - 161.1195681661
    else:
        g = temp - 60.0
        g = 288.1221695283 * (g ** -0.0755148492)

    # Blue
    if temp >= 66:
        b = 255.0
    elif temp <= 19:
        b = 0.0
    else:
        b = temp - 10.0
        b = 138.5177312231 * math.log(b) - 305.0447927307

    def clamp(v: float) -> int:
        return max(0, min(255, round(v)))

    return clamp(r), clamp(g), clamp(b)


# Kelvin anchors for the warm + cool channel mix on WWA fixtures.
# 2700K is conventional tungsten (pure warm); 6500K is daylight (pure cool).
_WWA_WARM_K = 2700
_WWA_COOL_K = 6500
# Below this we start blending the warm channel toward amber for very-warm
# (candle / firelight) territory.
_AMBER_THRESHOLD_K = 2700
# Below this we are fully amber, no warm.
_AMBER_FLOOR_K = 1800


def _wwa_mix(kelvin: float) -> dict[str, float]:
    """Return {role: 0..1} mix for a fixture's warm/cool/amber channels.

    Outside the warm/cool range we clamp to the nearest anchor. Below 2700K
    we additionally taper warm→amber so candle-warm Kelvin (~1900K) reads
    correctly on fixtures that have an amber channel.
    """
    k = float(kelvin)
    # Warm/cool linear mix in the canonical range
    span = _WWA_COOL_K - _WWA_WARM_K
    cool_amount = max(0.0, min(1.0, (k - _WWA_WARM_K) / span))
    warm_amount = max(0.0, min(1.0, (_WWA_COOL_K - k) / span))

    # Amber blend below 2700K
    if k < _AMBER_THRESHOLD_K:
        amber_span = _AMBER_THRESHOLD_K - _AMBER_FLOOR_K
        amber_amount = max(0.0, min(1.0, (_AMBER_THRESHOLD_K - k) / amber_span))
        # Shift some warm to amber proportionally
        warm_amount *= (1.0 - amber_amount)
    else:
        amber_amount = 0.0

    return {"warm": warm_amount, "cool": cool_amount, "amber": amber_amount}


def apply_color_temperature_live(kelvin, intensity=None, target_groups=None):
    """Set the targeted fixtures to a Kelvin white balance.

    Args:
        kelvin: target color temperature in Kelvin. Numbers or numeric strings.
                Clamped to [1800, 10000].
        intensity: optional 0–255, percentage, or relative (+/-) — same shape
                   accepted by apply_color_live. Defaults to full (255).
        target_groups: optional list of group names. Omit for the entire rig.

    Per-fixture strategy is chosen automatically based on which color roles
    the .qxf parser exposes for that fixture.
    """
    try:
        k = float(kelvin)
    except (TypeError, ValueError):
        return {
            "success": False,
            "output": "",
            "error": f"Invalid kelvin value: {kelvin!r}",
        }
    k = max(1800.0, min(10000.0, k))

    intensity_scale = _parse_level(intensity, default=255) / 255 if intensity is not None else 1.0
    intensity_scale = max(0.0, min(1.0, intensity_scale))
    master_intensity = round(255 * intensity_scale)

    fixtures = _target_fixtures(target_groups)
    updates = []
    per_fixture_notes = []

    # Pre-compute the RGB tint once — same for every fixture, only mix-in
    # differs per fixture type.
    rgb = _cct_to_rgb(k)

    # Roles we explicitly set vs. roles we leave untouched
    color_roles = {"red", "green", "blue", "white", "warm", "cool", "amber",
                   "uv", "cyan", "magenta", "yellow", "indigo", "lime"}
    keep_alone_roles = {"dimmer", "pan", "tilt"}

    for fixture in fixtures:
        roles = _fixture_roles(fixture)
        has_warm = "warm" in roles
        has_cool = "cool" in roles
        has_rgb = all(r in roles for r in ("red", "green", "blue"))
        has_white = "white" in roles
        has_amber = "amber" in roles

        if has_warm and has_cool:
            # Strategy A — WWA / tungsten fixture
            mix = _wwa_mix(k)
            for role in ("warm", "cool", "amber"):
                if role not in roles:
                    continue
                value = round(255 * mix[role] * intensity_scale)
                updates.append((_absolute_channel(fixture, roles[role]), value))
            per_fixture_notes.append({
                "id": fixture["id"],
                "name": fixture.get("name", ""),
                "strategy": "wwa",
                "applied": {role: round(255 * mix[role] * intensity_scale)
                            for role in ("warm", "cool", "amber") if role in roles},
            })
        elif has_rgb:
            # Strategy B — RGB / RGBA / RGBW fixture, Tanner Helland tint
            r_val = round(rgb[0] * intensity_scale)
            g_val = round(rgb[1] * intensity_scale)
            b_val = round(rgb[2] * intensity_scale)
            updates.append((_absolute_channel(fixture, roles["red"]), r_val))
            updates.append((_absolute_channel(fixture, roles["green"]), g_val))
            updates.append((_absolute_channel(fixture, roles["blue"]), b_val))

            applied = {"red": r_val, "green": g_val, "blue": b_val}

            # RGBW — push the white channel at full intensity so cool whites
            # don't read as blue. Skip if no white channel.
            if has_white:
                w_val = master_intensity
                updates.append((_absolute_channel(fixture, roles["white"]), w_val))
                applied["white"] = w_val

            # RGBA — use amber to reinforce warmth below ~3500K
            if has_amber and not has_white:
                # Map 1800K..3500K → amber 255..0
                amber_amount = max(0.0, min(1.0, (3500 - k) / (3500 - 1800)))
                a_val = round(255 * amber_amount * intensity_scale)
                updates.append((_absolute_channel(fixture, roles["amber"]), a_val))
                applied["amber"] = a_val

            per_fixture_notes.append({
                "id": fixture["id"],
                "name": fixture.get("name", ""),
                "strategy": "rgbw" if has_white else ("rgba" if has_amber else "rgb"),
                "applied": applied,
            })
        elif has_white:
            # Strategy C — white-only fixture, can't express CCT, just set intensity
            updates.append((_absolute_channel(fixture, roles["white"]), master_intensity))
            per_fixture_notes.append({
                "id": fixture["id"],
                "name": fixture.get("name", ""),
                "strategy": "white_only",
                "note": "Fixture has no warm/cool or RGB channels; CCT can't be expressed. Set white at intensity.",
                "applied": {"white": master_intensity},
            })
        elif "brightness" in roles or "dimmer" in roles:
            # Strategy D — dimmer-only fixture, even less than white
            offsets = roles.get("brightness", [roles["dimmer"]] if "dimmer" in roles else [])
            for offset in offsets:
                updates.append((_absolute_channel(fixture, offset), master_intensity))
            per_fixture_notes.append({
                "id": fixture["id"],
                "name": fixture.get("name", ""),
                "strategy": "dimmer_only",
                "note": "Fixture has no color channels; CCT can't be expressed. Set dimmer at intensity.",
                "applied": {"dimmer": master_intensity},
            })
        else:
            per_fixture_notes.append({
                "id": fixture["id"],
                "name": fixture.get("name", ""),
                "strategy": "skip",
                "note": "No usable color or brightness channel found.",
            })
            continue

        # Drive the dimmer channel up so the colored channels are actually visible
        if "dimmer" in roles and isinstance(roles["dimmer"], int):
            updates.append((_absolute_channel(fixture, roles["dimmer"]), master_intensity))

        # Zero out non-color, non-motion channels (same pattern as
        # apply_color_live) so macro/strobe/program state doesn't bleed in.
        color_offsets = {
            roles[r] for r in color_roles
            if r in roles and isinstance(roles[r], int)
        }
        keep_offsets = {
            roles[r] for r in keep_alone_roles
            if r in roles and isinstance(roles[r], int)
        }
        channel_count = int(fixture.get("channels", 0))
        for offset in range(channel_count):
            if offset in color_offsets or offset in keep_offsets:
                continue
            updates.append((_absolute_channel(fixture, offset), 0))

    success = set_channel_values(updates)
    return {
        "success": success,
        "output": f"Applied {k:.0f}K to {len(per_fixture_notes)} fixture(s) ({len(updates)} channels)",
        "error": "" if success else f"Failed to apply {k:.0f}K via WebSocket",
        "kelvin": k,
        "rgb": list(rgb),
        "fixtures": per_fixture_notes,
    }


# ----------------------------------------------------------------------------
# Palette — assign different colors/CCTs to different groups in one call
# (issue #7)
# ----------------------------------------------------------------------------
#
# This is the "set the room" primitive — three-point lighting in one move.
# Functionally it's a dispatcher over apply_color_live / apply_color_temperature_live
# per group, but the ergonomics are different: agents (and humans) tend to
# think "here's my palette" as a unit, not as a sequence of independent
# color calls.
#
# Each assignment value is normalized to either {color, intensity} or
# {kelvin, intensity}. The accepted shapes are documented on the MCP tool.


def _normalize_palette_value(value):
    """Coerce a palette assignment value into a routing dict.

    Returns a dict with one of these shapes:
        {"kelvin": float, "intensity": ...}    → routed to color_temperature
        {"color":  str,   "intensity": ...}    → routed to adjust_color
        {"error":  str}                        → unparseable

    Accepted input shapes:
        "warm"                                  → color preset
        3200, 3200.0                            → Kelvin number
        "3200"                                  → Kelvin (numeric string)
        "5600K"                                 → Kelvin (with K suffix)
        {"color": "warm", "intensity": "70%"}   → explicit color
        {"kelvin": 3200, "intensity": "50%"}    → explicit Kelvin
        {"k": 3200}                             → Kelvin (short key)
    """
    if isinstance(value, dict):
        intensity = value.get("intensity")
        kelvin = value.get("kelvin") or value.get("k")
        color = value.get("color")
        if kelvin is not None:
            try:
                return {"kelvin": float(kelvin), "intensity": intensity}
            except (TypeError, ValueError):
                return {"error": f"Invalid kelvin in palette dict: {kelvin!r}"}
        if color is not None:
            return {"color": str(color), "intensity": intensity}
        return {"error": "palette dict requires 'kelvin' or 'color'"}

    if isinstance(value, bool):
        # Bool is technically a number in Python — explicitly reject
        return {"error": f"Invalid palette value: {value!r}"}

    if isinstance(value, (int, float)):
        return {"kelvin": float(value), "intensity": None}

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return {"error": "Empty palette value"}
        # Try Kelvin number with optional K suffix
        kelvin_candidate = stripped.rstrip("Kk").strip() if stripped[-1] in "Kk" else stripped
        try:
            k_val = float(kelvin_candidate)
            # Only treat as Kelvin if it's in a plausible range — otherwise
            # it's likely an intensity-as-string or a malformed color name
            if 1000 <= k_val <= 40000:
                return {"kelvin": k_val, "intensity": None}
        except ValueError:
            pass
        # Otherwise interpret as a color preset name
        return {"color": stripped, "intensity": None}

    return {"error": f"Unsupported palette value type: {type(value).__name__}"}


def apply_palette_live(assignments):
    """Apply a palette: assign different colors/CCTs to different groups in
    one round trip.

    Note: unlike most other actions, palette does NOT accept a
    `target_groups` argument — the assignments dict's keys ARE the targets.
    Group names not present in the assignments are left untouched.

    Args:
        assignments: dict mapping group_name → value. See
                     `_normalize_palette_value` for the accepted value shapes.
    """
    if not isinstance(assignments, dict) or not assignments:
        return {
            "success": False,
            "output": "",
            "error": "assignments must be a non-empty object mapping group → value",
        }

    per_group = {}
    overall_success = True

    for group_name, raw_value in assignments.items():
        routing = _normalize_palette_value(raw_value)
        if "error" in routing:
            per_group[group_name] = {
                "success": False,
                "error": routing["error"],
                "value": raw_value,
            }
            overall_success = False
            continue

        try:
            if "kelvin" in routing:
                result = apply_color_temperature_live(
                    routing["kelvin"],
                    routing.get("intensity"),
                    target_groups=[group_name],
                )
                per_group[group_name] = {
                    "success": bool(result.get("success")),
                    "strategy": "color_temperature",
                    "kelvin": routing["kelvin"],
                    "intensity": routing.get("intensity"),
                    "fixtures_touched": len(result.get("fixtures", [])),
                    "output": result.get("output", ""),
                    "error": result.get("error", "") if not result.get("success") else "",
                }
            else:
                result = apply_color_live(
                    routing["color"],
                    routing.get("intensity"),
                    target_groups=[group_name],
                )
                per_group[group_name] = {
                    "success": bool(result.get("success")),
                    "strategy": "color_preset",
                    "color": routing["color"],
                    "intensity": routing.get("intensity"),
                    "output": result.get("output", ""),
                    "error": result.get("error", "") if not result.get("success") else "",
                }
            if not per_group[group_name]["success"]:
                overall_success = False
        except Exception as e:
            per_group[group_name] = {
                "success": False,
                "error": f"{type(e).__name__}: {e}",
            }
            overall_success = False

    successful = sum(1 for r in per_group.values() if r.get("success"))
    return {
        "success": overall_success,
        "applied_to": len(assignments),
        "successful": successful,
        "groups": per_group,
        "output": f"Palette applied · {successful}/{len(assignments)} groups",
        "error": "" if overall_success else "One or more group assignments failed — see per-group results",
    }


# ----------------------------------------------------------------------------
# Strobe — first-class abstraction over the per-fixture strobe channel
# (issue #5)
# ----------------------------------------------------------------------------
#
# QLC+ fixtures with a dedicated strobe channel almost universally follow
# the convention:
#
#     DMX 0–9    : rest (no strobe)
#     DMX 10–255 : slow → fast strobe
#
# We can't statically know the exact rate-to-DMX mapping for every fixture
# (it varies — some go to 20Hz, some 25Hz, some have non-linear ramps), so
# v1 uses a linear approximation: rate 0 → DMX 0 (rest), rate 20Hz → DMX 255
# (typically the fastest the fixture can do). Most stage fixtures look right
# at this mapping. Future work: parse <Capability> ranges from the .qxf to
# get per-fixture-accurate Hz mappings.


# Fixtures that hit pure-pulse at this Hz get full DMX (255).
_STROBE_MAX_HZ = 20.0
# DMX value where the strobe channel transitions out of "rest" zone.
_STROBE_DMX_REST_FLOOR = 10
_STROBE_DMX_MAX = 255


def _strobe_dmx_value(rate) -> int:
    """Map a rate input to a DMX channel value.

    Accepts:
        "off" / "0" / 0 / 0.0 / None  → 0  (rest)
        positive number (Hz)          → 10..255 linearly (clamped to 0..20 Hz)
    """
    if rate is None:
        return 0
    if isinstance(rate, str):
        normalized = rate.strip().lower()
        if normalized in ("off", "stop", "rest", "none", "0", "0hz"):
            return 0
        # Strip a trailing "hz" if present
        if normalized.endswith("hz"):
            normalized = normalized[:-2].strip()
        try:
            rate_val = float(normalized)
        except ValueError:
            return 0
    else:
        try:
            rate_val = float(rate)
        except (TypeError, ValueError):
            return 0

    if rate_val <= 0:
        return 0
    clamped = min(_STROBE_MAX_HZ, rate_val)
    span = _STROBE_DMX_MAX - _STROBE_DMX_REST_FLOOR
    return int(round(_STROBE_DMX_REST_FLOOR + (clamped / _STROBE_MAX_HZ) * span))


def apply_strobe_live(rate, intensity=None, target_groups=None):
    """Strobe targeted fixtures at the given rate.

    Args:
        rate: 0–20 Hz, or "off"/"0" to stop. Above 20Hz clamps to 20Hz.
        intensity: Optional brightness level (0-255 / "%" / "+/-") applied
                   to the fixture's dimmer / brightness-tracking channels
                   so the strobe is visible. If None, brightness is left
                   alone (operator wants to strobe at whatever's currently lit).
        target_groups: Optional group names to limit the strobe. Omit to
                       target every fixture in the workspace.

    Fixtures without a dedicated strobe channel are skipped and listed in
    the response. Use blackout() and adjust_color() / batch_action for
    fixtures that need brightness-cycled "strobe" effects instead.
    """
    fixtures = _target_fixtures(target_groups)
    if not fixtures:
        return {
            "success": False,
            "output": "",
            "error": "No fixtures matched the request",
        }

    dmx_value = _strobe_dmx_value(rate)
    rest = (dmx_value == 0)

    updates = []
    per_fixture = []

    for fixture in fixtures:
        roles = _fixture_roles(fixture)
        strobe_offset = roles.get("strobe")

        if not isinstance(strobe_offset, int):
            per_fixture.append({
                "id": fixture["id"],
                "name": fixture.get("name", ""),
                "status": "skipped",
                "reason": "no dedicated strobe channel — use batch_action with blackout/adjust_color for brightness-cycled effects",
            })
            continue

        strobe_abs = _absolute_channel(fixture, strobe_offset)
        updates.append((strobe_abs, dmx_value))

        applied = {"strobe": dmx_value}

        # Drive dimmer to make the strobe visible (only if caller specified
        # an intensity and we're not resting)
        if intensity is not None and not rest:
            parsed_intensity = _parse_level(intensity, default=255)
            if "dimmer" in roles and isinstance(roles["dimmer"], int):
                dim_abs = _absolute_channel(fixture, roles["dimmer"])
                updates.append((dim_abs, parsed_intensity))
                applied["dimmer"] = parsed_intensity
            elif "brightness" in roles and roles["brightness"]:
                for offset in roles["brightness"]:
                    abs_ch = _absolute_channel(fixture, offset)
                    updates.append((abs_ch, parsed_intensity))
                applied[f"brightness({len(roles['brightness'])} channels)"] = parsed_intensity

        per_fixture.append({
            "id": fixture["id"],
            "name": fixture.get("name", ""),
            "status": "applied",
            "applied": applied,
        })

    skipped = sum(1 for f in per_fixture if f["status"] == "skipped")
    applied_count = sum(1 for f in per_fixture if f["status"] == "applied")

    if applied_count == 0:
        return {
            "success": False,
            "output": "",
            "error": "No fixtures with a strobe channel were targeted",
            "fixtures": per_fixture,
        }

    # Reconstruct a human-readable rate string for the response
    if rest:
        rate_label = "off"
    else:
        # Reverse-engineer the rate from the DMX value (after clamping) for
        # display — avoids re-parsing a potentially string `rate`
        span = _STROBE_DMX_MAX - _STROBE_DMX_REST_FLOOR
        approx_hz = ((dmx_value - _STROBE_DMX_REST_FLOOR) / span) * _STROBE_MAX_HZ
        rate_label = f"~{approx_hz:.1f}Hz"

    success = set_channel_values(updates)
    return {
        "success": success,
        "output": (
            f"Strobe {'stopped' if rest else 'at ' + rate_label} "
            f"on {applied_count} fixture(s), {skipped} skipped"
        ),
        "error": "" if success else "Failed to apply strobe via WebSocket",
        "rate": rate_label,
        "dmx_value": dmx_value,
        "fixtures": per_fixture,
    }


async def _fade_brightness_async(channels, target_value, seconds, steps):
    """Run a multi-step fade over the persistent QLC+ WebSocket."""
    ws = await _ensure_qlc_ws()
    try:
        for step in range(1, steps + 1):
            ratio = step / steps
            commands = []
            for channel, start in channels:
                val = max(0, min(255, round(start + (target_value - start) * ratio)))
                commands.append(f"CH|{channel}|{val}")
            async with _qlc_ws_lock:
                for cmd in commands:
                    await ws.send(cmd)
            if step < steps and seconds > 0:
                await asyncio.sleep(seconds / steps)
        return True
    except Exception as e:
        print(f"Fade WebSocket error: {e}")
        return False


def fade_brightness_live(target, duration, target_groups=None):
    fixtures = _target_fixtures(target_groups)
    current = get_current_channel_values()
    channels = []
    for fixture in fixtures:
        roles = _fixture_roles(fixture)
        for offset in roles.get("brightness", []):
            absolute = _absolute_channel(fixture, offset)
            channels.append((absolute, current.get(absolute, 0)))

    try:
        seconds = max(0, float(duration))
    except (TypeError, ValueError):
        seconds = 3
    steps = max(1, min(30, math.ceil(seconds * 10)))
    target_value = _parse_level(target, default=0)

    try:
        success = _qlc_run(
            _fade_brightness_async(channels, target_value, seconds, steps),
            timeout=seconds + 5,
        )
    except Exception as e:
        print(f"fade error: {e}")
        success = False

    return {
        "success": success,
        "output": f"Faded {len(channels)} brightness channels to {target_value} over {seconds:g}s",
        "error": "" if success else "Fade WebSocket connection failed",
    }


def execute_command(command):
    """Execute a shell command and return output"""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30
        )
        return {
            "success": result.returncode == 0,
            "output": result.stdout,
            "error": result.stderr
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "output": "",
            "error": "Command timed out"
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": str(e)
        }


def interpret_command(user_input):
    """
    Use AI to interpret natural language command and convert to lighting action
    
    Args:
        user_input: Natural language command from user
    
    Returns:
        dict: Action data with action type, parameters, and explanation
    """
    
    if not user_input or not user_input.strip():
        return {
            "action": "error",
            "parameters": {},
            "explanation": "Empty command"
        }
    
    # Check AI configuration
    if AI_PROVIDER not in ["anthropic", "openai", "ollama"]:
        return {
            "action": "error",
            "parameters": {},
            "explanation": f"Invalid AI provider: {AI_PROVIDER}"
        }
    
    if AI_PROVIDER != "ollama" and not AI_API_KEY:
        return {
            "action": "error",
            "parameters": {},
            "explanation": "AI_API_KEY not configured"
        }
    
    # Build prompt for AI
    system_prompt = """You are a lighting control assistant. Convert natural language commands into structured lighting actions.

Available actions:
1. adjust_brightness: Change overall brightness (value: -100 to +100 or absolute 0-255)
2. adjust_color: Change color (color: red/green/blue/warm/cool/etc, intensity: 0-255)
3. apply_template: Use a template (template: youtube-studio/party/ambient/spotlight/work-light/warm-white/cool-white)
4. generate_scene: Create new scene from description (description: text)
5. fade: Fade to black or specific level (duration: seconds, target: 0-255)
6. activate_scene: Apply an existing named scene (scene: Red/Blue/Green/Lights ON/Lights OFF/Work Light/Purple/Warm Amber/Spotlight/etc)

Respond ONLY with valid JSON in this format:
{
  "action": "action_name",
  "parameters": {
    "param1": "value1",
    "param2": "value2"
  },
  "explanation": "Brief explanation of what will happen"
}

Examples:
Input: "make it brighter"
Output: {"action": "adjust_brightness", "parameters": {"value": "+50"}, "explanation": "Increasing brightness by 50"}

Input: "add more blue"
Output: {"action": "adjust_color", "parameters": {"color": "blue", "intensity": "+50"}, "explanation": "Adding more blue to the scene"}

Input: "switch to party mode"
Output: {"action": "apply_template", "parameters": {"template": "party"}, "explanation": "Applying party template"}

Input: "warm sunset ambiance"
Output: {"action": "generate_scene", "parameters": {"description": "warm sunset ambiance"}, "explanation": "Generating warm sunset scene"}

Input: "fade to black over 5 seconds"
Output: {"action": "fade", "parameters": {"duration": "5", "target": "0"}, "explanation": "Fading to black over 5 seconds"}

Input: "turn on red scene"
Output: {"action": "activate_scene", "parameters": {"scene": "Red"}, "explanation": "Applying the Red scene"}"""

    user_prompt = f"Convert this command: {user_input}"
    
    # Call AI based on provider
    try:
        if AI_PROVIDER == "anthropic":
            response = call_anthropic(system_prompt, user_prompt)
        elif AI_PROVIDER == "openai":
            response = call_openai(system_prompt, user_prompt)
        elif AI_PROVIDER == "ollama":
            response = call_ollama(system_prompt, user_prompt)
        else:
            return {
                "action": "error",
                "parameters": {},
                "explanation": f"Unknown AI provider: {AI_PROVIDER}"
            }
    except Exception as e:
        return {
            "action": "error",
            "parameters": {},
            "explanation": f"AI API error: {str(e)}"
        }
    
    # Parse JSON response
    try:
        # Extract JSON from response (handle markdown code blocks)
        if "```json" in response:
            response = response.split("```json")[1].split("```")[0].strip()
        elif "```" in response:
            response = response.split("```")[1].split("```")[0].strip()
        
        return json.loads(response)
    except json.JSONDecodeError:
        return {
            "action": "error",
            "parameters": {},
            "explanation": f"Failed to parse AI response: {response}"
        }


def call_anthropic(system_prompt, user_prompt):
    """Call Anthropic Claude API"""
    import requests
    
    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": AI_API_KEY,
                "anthropic-version": "2023-06-01"
            },
            json={
                "model": AI_MODEL,
                "max_tokens": 1024,
                "system": system_prompt,
                "messages": [
                    {"role": "user", "content": user_prompt}
                ]
            },
            timeout=30
        )
        response.raise_for_status()
        return response.json()["content"][0]["text"]
    except requests.exceptions.RequestException as e:
        raise Exception(f"Anthropic API error: {str(e)}")


def call_openai(system_prompt, user_prompt):
    """Call OpenAI API"""
    import requests
    
    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {AI_API_KEY}"
            },
            json={
                "model": AI_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
            },
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        text = data["choices"][0]["message"]["content"]
        # Strip markdown code fences if present
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        return text
    except requests.exceptions.RequestException as e:
        raise Exception(f"OpenAI API error: {str(e)}")


def call_ollama(system_prompt, user_prompt):
    """Call Ollama local API"""
    import requests
    
    combined_prompt = f"{system_prompt}\n\n{user_prompt}"
    
    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": AI_MODEL,
                "prompt": combined_prompt,
                "stream": False
            },
            timeout=60
        )
        response.raise_for_status()
        return response.json()["response"]
    except requests.exceptions.ConnectionError:
        raise Exception("Ollama not running. Start with: ollama serve")
    except requests.exceptions.RequestException as e:
        raise Exception(f"Ollama API error: {str(e)}")


def execute_lighting_action(action_data, target_groups=None):
    """Execute the interpreted lighting action.

    When running locally on the Pi (IS_LOCAL=True), generated scenes are
    rendered to a temporary XML file and applied immediately through QLC+
    WebSocket channel updates. The workspace is not modified for runtime
    commands.

    If target_groups is a non-empty list, the action is applied to each named
    group in sequence using group-scene / group-template instead of the
    global generate-scene command.
    """
    action = action_data.get("action")
    params = action_data.get("parameters", {})

    def _build_scene_cmd(description, output_file, group_name=None):
        safe_desc = description.replace("'", "'\\''")
        if group_name:
            safe_group = group_name.replace("'", "'\\''")
            if IS_LOCAL and WORKSPACE_PATH.exists():
                return (
                    f"{LIGHTSCTL} group-scene '{safe_group}' '{safe_desc}'"
                    f" --workspace '{WORKSPACE_PATH}'"
                    f" --output '{output_file}'"
                )
            else:
                return f"{LIGHTSCTL} group-scene '{safe_group}' '{safe_desc}' --add-to-workspace"
        else:
            if IS_LOCAL and WORKSPACE_PATH.exists():
                return (
                    f"{LIGHTSCTL} generate-scene '{safe_desc}'"
                    f" --workspace '{WORKSPACE_PATH}'"
                    f" --output '{output_file}'"
                )
            else:
                return f"{LIGHTSCTL} generate-scene '{safe_desc}' --add-to-workspace"

    def _temp_scene_file():
        fh = tempfile.NamedTemporaryFile(prefix="qlc-scene-", suffix=".xml", delete=False)
        path = fh.name
        fh.close()
        return Path(path)

    def _generate_and_apply_live(cmd, scene_file):
        result = execute_command(cmd)
        if not IS_LOCAL or not WORKSPACE_PATH.exists():
            return result
        if not result["success"]:
            scene_file.unlink(missing_ok=True)
            return result
        if not scene_file.exists() or not scene_file.read_text().strip():
            result["success"] = False
            result["error"] = "Scene file not created"
            return result
        scene_xml_content = scene_file.read_text()
        try:
            apply_result = apply_scene_xml_live(scene_xml_content)
        finally:
            scene_file.unlink(missing_ok=True)
        result["success"] = apply_result["success"]
        result["output"] = (result.get("output", "") + "\n" + apply_result["output"]).strip()
        result["error"] = apply_result["error"] if not apply_result["success"] else result.get("error", "")
        # Stash the scene XML so the caller can offer a "save" option
        result["scene_xml"] = scene_xml_content
        return result

    if action == "apply_template":
        template = params.get("template")
        groups = target_groups if target_groups else []
        if groups:
            # Apply template to each selected group
            combined_output = ""
            for gname in groups:
                safe_name = gname.replace("'", "'\\''")
                scene_file = _temp_scene_file()
                if IS_LOCAL and WORKSPACE_PATH.exists():
                    cmd = (f"{LIGHTSCTL} group-template '{safe_name}' {template}"
                           f" --workspace '{WORKSPACE_PATH}'"
                           f" --output '{scene_file}'")
                else:
                    cmd = f"{LIGHTSCTL} group-template '{safe_name}' {template} --add-to-workspace"
                result = _generate_and_apply_live(cmd, scene_file)
                combined_output += result.get("output", "") + "\n"
                if not result["success"]:
                    return result
            return {"success": True, "output": combined_output.strip(), "error": ""}
        elif IS_LOCAL and WORKSPACE_PATH.exists():
            scene_file = _temp_scene_file()
            cmd = (f"{LIGHTSCTL} generate-from-template {template}"
                   f" --workspace '{WORKSPACE_PATH}'"
                   f" --output '{scene_file}'")
            return _generate_and_apply_live(cmd, scene_file)
        else:
            cmd = f"{LIGHTSCTL} generate-from-template {template} --add-to-workspace"
            return execute_command(cmd)

    elif action == "generate_scene":
        description = params.get("description", "")
        groups = target_groups if target_groups else []
        if groups:
            combined_output = ""
            for gname in groups:
                scene_file = _temp_scene_file()
                cmd = _build_scene_cmd(description, scene_file, group_name=gname)
                result = _generate_and_apply_live(cmd, scene_file)
                combined_output += result.get("output", "") + "\n"
                if not result["success"]:
                    return result
            return {"success": True, "output": combined_output.strip(), "error": ""}
        scene_file = _temp_scene_file()
        cmd = _build_scene_cmd(description, scene_file)
        return _generate_and_apply_live(cmd, scene_file)

    elif action == "adjust_brightness":
        value = params.get("value", "+50")
        return apply_brightness_live(value, target_groups=target_groups)

    elif action == "adjust_color":
        color = params.get("color", "white")
        intensity = params.get("intensity", "200")
        return apply_color_live(color, intensity, target_groups=target_groups)

    elif action == "color_temperature":
        kelvin = params.get("kelvin") or params.get("k")
        if kelvin is None:
            return {"success": False, "output": "", "error": "Missing required parameter 'kelvin'"}
        intensity = params.get("intensity")
        return apply_color_temperature_live(kelvin, intensity, target_groups=target_groups)

    elif action == "palette":
        # Palette uses its own per-group assignments — target_groups is
        # ignored (the assignments dict's keys are the targets).
        assignments = params.get("assignments")
        return apply_palette_live(assignments)

    elif action == "strobe":
        rate = params.get("rate")
        if rate is None:
            return {"success": False, "output": "", "error": "Missing required parameter 'rate'"}
        intensity = params.get("intensity")
        return apply_strobe_live(rate, intensity, target_groups=target_groups)

    elif action == "fade":
        duration = params.get("duration", "3")
        target = params.get("target", "0")
        return fade_brightness_live(target, duration, target_groups=target_groups)

    elif action == "activate_scene":
        scene = params.get("scene") or params.get("name") or params.get("id")
        return apply_existing_scene_live(scene)

    else:
        return {
            "success": False,
            "output": "",
            "error": f"Unknown action: {action}"
        }


@app.route("/")
def index():
    """Serve the control interface"""
    return render_template("index.html")


@app.route("/api/command", methods=["POST"])
def handle_command():
    """Handle natural language command"""
    import time as _time

    data = request.json
    user_input = data.get("command", "").strip()
    target_groups = data.get("groups") or None  # list of group names, or None = all fixtures

    if not user_input:
        return jsonify({
            "success": False,
            "error": "No command provided"
        }), 400

    # Interpret command using AI (with timing)
    t0 = _time.time()
    action_data = interpret_command(user_input)
    interpret_ms = round((_time.time() - t0) * 1000)

    if action_data.get("action") == "error":
        return jsonify({
            "success": False,
            "error": action_data.get("explanation"),
            "action": action_data,
            "debug": {
                "interpret_ms": interpret_ms,
                "provider": AI_PROVIDER,
                "model": AI_MODEL,
                "is_local": IS_LOCAL,
            }
        }), 400

    # Execute the action (with timing)
    t1 = _time.time()
    result = execute_lighting_action(action_data, target_groups=target_groups)
    execute_ms = round((_time.time() - t1) * 1000)

    return jsonify({
        "success": result["success"],
        "action": action_data,
        "groups": target_groups,
        "output": result.get("output", ""),
        "error": result.get("error", "") if not result["success"] else "",
        "log": result.get("error", "") if result["success"] else "",
        "scene_xml": result.get("scene_xml"),  # present for generate_scene/apply_template
        "debug": {
            "interpret_ms": interpret_ms,
            "execute_ms": execute_ms,
            "total_ms": interpret_ms + execute_ms,
            "provider": AI_PROVIDER,
            "model": AI_MODEL,
            "is_local": IS_LOCAL,
        }
    })


@app.route("/api/action", methods=["POST"])
def handle_action():
    """Dispatch a structured lighting action without going through the AI interpreter.

    Body:
        {
            "action": "<adjust_brightness|adjust_color|fade|apply_template|generate_scene|activate_scene>",
            "parameters": { ... action-specific ... },
            "groups": ["group-name", ...]   # optional, applies to all fixtures if omitted
        }

    Designed for programmatic callers (e.g. the MCP server) that have already
    resolved the user's intent into a structured action and don't need the AI
    pass that /api/command performs.
    """
    import time as _time

    data = request.get_json(silent=True) or {}
    action = (data.get("action") or "").strip()
    if not action:
        return jsonify({"success": False, "error": "Missing 'action' field"}), 400

    action_data = {
        "action": action,
        "parameters": data.get("parameters", {}) or {},
        "explanation": data.get("explanation", ""),
    }
    target_groups = data.get("groups") or None

    t0 = _time.time()
    result = execute_lighting_action(action_data, target_groups=target_groups)
    execute_ms = round((_time.time() - t0) * 1000)

    return jsonify({
        "success": result["success"],
        "action": action_data,
        "groups": target_groups,
        "output": result.get("output", ""),
        "error": result.get("error", "") if not result["success"] else "",
        "scene_xml": result.get("scene_xml"),
        "debug": {
            "execute_ms": execute_ms,
            "is_local": IS_LOCAL,
        }
    })


@app.route("/api/status", methods=["GET"])
def get_status():
    """Get detailed multi-service health status"""
    import time as _time

    services = {}

    # 1. AI Provider
    ai_ok = False
    ai_detail = ""
    ai_latency = None
    if AI_PROVIDER == "ollama":
        try:
            t0 = _time.time()
            import requests as _req
            r = _req.get("http://localhost:11434/api/tags", timeout=3)
            ai_latency = round((_time.time() - t0) * 1000)
            ai_ok = r.status_code == 200
            ai_detail = "running" if ai_ok else f"HTTP {r.status_code}"
        except Exception as e:
            ai_detail = str(e)
    elif AI_PROVIDER in ("openai", "anthropic"):
        ai_ok = bool(AI_API_KEY)
        ai_detail = "key configured" if ai_ok else "API key missing"
    else:
        ai_detail = f"unknown provider: {AI_PROVIDER}"

    services["ai"] = {
        "name": f"AI ({AI_PROVIDER})",
        "ok": ai_ok,
        "detail": ai_detail,
        "model": AI_MODEL,
        "latency_ms": ai_latency,
    }

    # 2. QLC+ WebSocket health. Inspect our persistent connection rather than
    # opening a fresh TCP probe — under load, QLC+ may not accept new TCP
    # connections within a tight timeout even though the existing WebSocket
    # is fully functional. Report the persistent connection's live state.
    ws_ok = False
    ws_detail = "unknown"
    try:
        if _qlc_ws is None:
            ws_detail = "not connected (will reconnect on next command)"
        elif getattr(_qlc_ws, "closed", False):
            ws_detail = "closed (will reconnect on next command)"
        else:
            ws_ok = True
            ws_detail = f"connected at {QLC_WS_URL}"
    except Exception as e:
        ws_detail = f"check failed: {e}"
    services["qlc_ws"] = {
        "name": "QLC+ WebSocket",
        "ok": ws_ok,
        "detail": ws_detail,
        "url": QLC_WS_URL,
    }

    # 3. QLC+ Service
    qlc_running = False
    qlc_detail = "unknown"
    try:
        if IS_LOCAL:
            # Running on the Pi — check systemd directly, no SSH
            result = execute_command(f"systemctl is-active {SERVICE_NAME}")
            qlc_running = result["success"] and "active" in result.get("output", "").strip()
            qlc_detail = result.get("output", "").strip() if result["success"] else "stopped"
        else:
            # Remote workstation — SSH via lightsctl
            result = execute_command(f"{LIGHTSCTL} status")
            qlc_running = result["success"] and "running" in result.get("output", "").lower()
            qlc_detail = "running" if qlc_running else "stopped / unreachable"
    except Exception:
        qlc_detail = "check failed"

    services["qlc_service"] = {
        "name": "QLC+ Service",
        "ok": qlc_running,
        "detail": qlc_detail,
    }

    # 4. Workspace file
    ws_exists = WORKSPACE_PATH.exists()
    services["workspace"] = {
        "name": "Workspace",
        "ok": ws_exists,
        "detail": str(WORKSPACE_PATH) if ws_exists else "file not found",
        "path": str(WORKSPACE_PATH),
    }

    overall_ok = all(s["ok"] for s in services.values())

    return jsonify({
        # Legacy fields for backward compat
        "qlc_running": qlc_running,
        "workspace": str(WORKSPACE_PATH),
        "workspace_exists": ws_exists,
        "ai_provider": AI_PROVIDER,
        "ai_model": AI_MODEL,
        # New rich status
        "ok": overall_ok,
        "services": services,
        "is_local": IS_LOCAL,
    })


@app.route("/api/templates", methods=["GET"])
def list_templates():
    """List available templates"""
    templates = [
        {"name": "youtube-studio", "description": "Bright neutral white for video recording"},
        {"name": "party", "description": "Vibrant alternating colors"},
        {"name": "ambient", "description": "Soft warm glow"},
        {"name": "spotlight", "description": "Single fixture at full"},
        {"name": "work-light", "description": "Bright neutral white"},
        {"name": "warm-white", "description": "Warm white (2700K-3000K)"},
        {"name": "cool-white", "description": "Cool white (5000K-6500K)"}
    ]
    
    return jsonify({"templates": templates})


@app.route("/api/scenes", methods=["GET"])
def list_scenes():
    """List existing scene functions from the loaded workspace."""
    try:
        return jsonify({"scenes": get_workspace_scenes()})
    except Exception as e:
        return jsonify({"error": str(e), "scenes": []}), 500


@app.route("/api/scenes/<scene_id>/activate", methods=["POST"])
def activate_scene(scene_id):
    """Apply an existing workspace scene live via WebSocket channel updates."""
    try:
        result = apply_existing_scene_live(scene_id)
        status = 200 if result["success"] else 404
        return jsonify({
            "success": result["success"],
            "action": {
                "action": "activate_scene",
                "parameters": {"scene": scene_id},
                "explanation": result["output"] if result["success"] else result["error"],
            },
            "output": result.get("output", ""),
            "error": result.get("error", "") if not result["success"] else "",
        }), status
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/scenes/save", methods=["POST"])
def save_scene():
    """Save a scene to the workspace permanently.

    Accepts either:
      - scene_xml: raw QLC+ scene XML to inject directly
      - snapshot: true — captures the current live channel state as a new scene

    Required: name (the scene name to save as)
    Optional: path (folder path within QLC+, e.g. "AI Generated")
    """
    try:
        data = request.get_json()
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"success": False, "error": "Scene name is required"}), 400

        scene_xml = data.get("scene_xml", "").strip()
        snapshot = data.get("snapshot", False)
        path = data.get("path", "AI Generated").strip()

        if not scene_xml and snapshot:
            # Build scene XML from current live channel values
            scene_xml = _snapshot_current_state_as_xml(name, path)
        elif not scene_xml and not snapshot:
            return jsonify({
                "success": False,
                "error": "Provide scene_xml or set snapshot=true"
            }), 400

        # If scene_xml was provided but doesn't have the right Name, patch it
        if scene_xml and name:
            scene_xml = _patch_scene_name(scene_xml, name, path)

        # Inject into workspace
        if not WORKSPACE_PATH.exists():
            return jsonify({"success": False, "error": "Workspace file not found"}), 500

        next_id = get_next_scene_id()
        success = _inject_scene_into_workspace(scene_xml, next_id)

        if success:
            return jsonify({
                "success": True,
                "scene": {"id": next_id, "name": name, "path": path},
                "message": f"Scene '{name}' saved (ID {next_id})",
            })
        else:
            return jsonify({"success": False, "error": "Failed to inject scene into workspace"}), 500

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/scenes/snapshot", methods=["POST"])
def snapshot_scene():
    """Capture the current live channel state as a new saved scene.

    Body: { "name": "My Scene Name", "path": "AI Generated" }
    """
    try:
        data = request.get_json()
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"success": False, "error": "Scene name is required"}), 400
        path = data.get("path", "AI Generated").strip()

        scene_xml = _snapshot_current_state_as_xml(name, path)
        if not scene_xml:
            return jsonify({"success": False, "error": "Could not read current channel values"}), 500

        next_id = get_next_scene_id()
        success = _inject_scene_into_workspace(scene_xml, next_id)

        if success:
            return jsonify({
                "success": True,
                "scene": {"id": next_id, "name": name, "path": path},
                "message": f"Snapshot saved as '{name}' (ID {next_id})",
            })
        else:
            return jsonify({"success": False, "error": "Failed to inject scene into workspace"}), 500

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def _snapshot_current_state_as_xml(name: str, path: str = "AI Generated") -> str:
    """Build a QLC+ scene XML from the current live channel values."""
    fixtures = get_workspace_fixtures()
    values = get_current_channel_values()
    if not values:
        return ""

    fixture_vals = []
    for fixture in fixtures:
        pairs = []
        for offset in range(fixture["channels"]):
            abs_ch = fixture["universe"] * 512 + fixture["address"] + offset + 1
            val = values.get(abs_ch, 0)
            # Use 1-based channel numbering for generated scenes
            pairs.append(f"{offset + 1},{val}")
        if pairs:
            fixture_vals.append(
                f'  <FixtureVal ID="{fixture["id"]}">{",".join(pairs)}</FixtureVal>'
            )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE Function>\n'
        f'<Function Type="Scene" Name="{_xml_escape(name)}" Path="{_xml_escape(path)}">\n'
        '  <Speed FadeIn="0" FadeOut="0" Duration="0"/>\n'
        + "\n".join(fixture_vals) + "\n"
        '</Function>'
    )
    return xml


def _patch_scene_name(scene_xml: str, name: str, path: str) -> str:
    """Ensure the scene XML has the correct Name and Path attributes."""
    import re
    # Replace Name attribute
    scene_xml = re.sub(
        r'Name="[^"]*"',
        f'Name="{_xml_escape(name)}"',
        scene_xml,
        count=1,
    )
    # Add or replace Path attribute
    if 'Path="' in scene_xml:
        scene_xml = re.sub(
            r'Path="[^"]*"',
            f'Path="{_xml_escape(path)}"',
            scene_xml,
            count=1,
        )
    else:
        scene_xml = scene_xml.replace(
            'Type="Scene"',
            f'Type="Scene" Path="{_xml_escape(path)}"',
            1,
        )
    return scene_xml


def _xml_escape(text: str) -> str:
    """Escape text for use in XML attributes."""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;"))


def _inject_scene_into_workspace(scene_xml: str, scene_id: int) -> bool:
    """Inject scene XML into the workspace file's Engine element.

    Uses Python XML manipulation directly (no external scripts needed).
    """
    try:
        tree = ET.parse(WORKSPACE_PATH)
        root = tree.getroot()
        engine = _engine_element(root)
        if engine is None:
            print("Error: No Engine element in workspace")
            return False

        # Parse the scene XML
        scene_root = ET.fromstring(scene_xml.strip().split("<!DOCTYPE Function>")[-1].strip()
                                   if "<!DOCTYPE" in scene_xml else scene_xml.strip())

        # Set the ID attribute
        scene_root.set("ID", str(scene_id))

        # Append to Engine
        engine.append(scene_root)

        # Write back
        tree.write(str(WORKSPACE_PATH), encoding="UTF-8", xml_declaration=True)
        return True
    except Exception as e:
        print(f"Error injecting scene: {e}")
        return False


@app.route("/api/groups", methods=["GET"])
def list_groups():
    """List fixture groups"""
    try:
        if not GROUPS_FILE.exists():
            return jsonify({"groups": []})
        
        with open(GROUPS_FILE, 'r') as f:
            groups_data = json.load(f)
        
        # Handle both formats: {"groups": {...}} and direct {...}
        if "groups" in groups_data:
            groups_dict = groups_data["groups"]
        else:
            groups_dict = groups_data
        
        groups = []
        for group_name, group_info in groups_dict.items():
            groups.append({
                "name": group_name,
                "fixtures": group_info.get("fixtures", []),
                "description": group_info.get("description", "")
            })
        
        return jsonify({"groups": groups})
    except Exception as e:
        return jsonify({"error": str(e), "groups": []}), 500


@app.route("/api/groups/<group_name>/template", methods=["POST"])
def apply_group_template(group_name):
    """Apply a template to a specific fixture group"""
    try:
        data = request.get_json()
        template = data.get("template")
        
        if not template:
            return jsonify({"success": False, "error": "Template name required"}), 400
        
        safe_name = group_name.replace("'", "'\\''")
        scene_file = tempfile.NamedTemporaryFile(prefix="qlc-group-template-", suffix=".xml", delete=False)
        scene_path = Path(scene_file.name)
        scene_file.close()
        if IS_LOCAL and WORKSPACE_PATH.exists():
            cmd = (f"{LIGHTSCTL} group-template '{safe_name}' {template}"
                   f" --workspace '{WORKSPACE_PATH}'"
                   f" --output '{scene_path}'")
        else:
            cmd = f"{LIGHTSCTL} group-template '{safe_name}' {template} --add-to-workspace"
        
        result = execute_command(cmd)
        
        # If local, apply the generated template scene immediately with no QLC+ restart.
        if IS_LOCAL and WORKSPACE_PATH.exists() and result["success"]:
            if scene_path.exists() and scene_path.read_text().strip():
                apply_result = apply_scene_xml_live(scene_path.read_text())
                result["success"] = apply_result["success"]
                result["output"] = (result.get("output", "") +
                                    f"\n{apply_result['output']}").strip()
                if not apply_result["success"]:
                    result["error"] = apply_result["error"]
            else:
                result["success"] = False
                result["error"] = "Scene file not created"
        scene_path.unlink(missing_ok=True)
        
        success = result["success"]
        return jsonify({
            "success": success,
            "action": {"action": "apply_template", "explanation": f"Applied {template} to {group_name}",
                       "parameters": {"template": template, "group": group_name}},
            "output": result.get("output", ""),
            "error": result.get("error", "") if not success else "",
            "log": result.get("error", "") if success else "",
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/fixtures", methods=["GET"])
def list_fixtures():
    """List all fixtures from workspace, with resolved per-channel info.

    Each fixture now includes a `channel_info` array with the channel's
    name, offset, role, preset, group, and colour metadata sourced from
    the QLC+ .qxf fixture definitions when available.
    """
    try:
        fixtures = get_workspace_fixtures()
        for fixture in fixtures:
            fixture["channel_info"] = _fixture_channels_info(fixture)
        return jsonify({"fixtures": fixtures})
    except Exception as e:
        return jsonify({"error": str(e), "fixtures": []}), 500


@app.route("/api/fixture_channels/<int:fixture_id>", methods=["GET"])
def fixture_channels(fixture_id):
    """Return resolved channel info for a single fixture.

    Reads the fixture's .qxf definition (manufacturer + model + mode) and
    returns each channel's offset, name, role, preset, group, and colour.
    Useful for the UI to render correct labels and color hints.
    """
    try:
        fixtures = get_workspace_fixtures()
        match = next((f for f in fixtures if str(f["id"]) == str(fixture_id)), None)
        if match is None:
            return jsonify({"error": f"Fixture {fixture_id} not found"}), 404
        return jsonify({
            "fixture": {
                "id": match["id"],
                "name": match["name"],
                "manufacturer": match["manufacturer"],
                "model": match["model"],
                "mode": match["mode"],
                "channels": match["channels"],
            },
            "channel_info": _fixture_channels_info(match),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/fixture_definitions/reload", methods=["POST"])
def reload_fixture_definitions():
    """Force reload of cached .qxf fixture definitions from disk."""
    try:
        count = fixture_definitions.reload_definitions()
        return jsonify({"success": True, "indexed": count})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/channel", methods=["POST"])
def set_channel():
    """Set DMX channel value"""
    try:
        data = request.get_json()
        fixture_id = data.get("fixture_id")
        channel_offset = data.get("channel", 0)  # 0-based offset within fixture
        value = data.get("value", 0)
        
        if fixture_id is None or value is None:
            return jsonify({"success": False, "error": "Missing fixture_id or value"}), 400
        
        # Get fixture info from workspace
        tree = ET.parse(WORKSPACE_PATH)
        root = tree.getroot()
        ns = {'qlc': 'http://www.qlcplus.org/Workspace'}
        
        fixture = root.find(f".//qlc:Fixture[qlc:ID='{fixture_id}']", ns)
        if fixture is None:
            return jsonify({"success": False, "error": f"Fixture {fixture_id} not found"}), 404
        
        universe_elem = fixture.find("qlc:Universe", ns)
        address_elem = fixture.find("qlc:Address", ns)
        
        universe = int(universe_elem.text) if universe_elem is not None else 0
        base_address = int(address_elem.text) if address_elem is not None else 0
        
        # Calculate actual DMX address (1-based)
        dmx_address = base_address + channel_offset + 1
        
        # Set channel value
        success = set_channel_value(universe, dmx_address, int(value))
        
        return jsonify({
            "success": success,
            "fixture_id": fixture_id,
            "universe": universe,
            "address": dmx_address,
            "value": value
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/channel_values", methods=["GET"])
def get_channel_values():
    """Get current DMX channel values from QLC+ via WebSocket.

    QLC+ 4.x response format for getChannelsValues:
        QLC+API|getChannelsValues|<universe>|<ch>|<val>|<pct>.<color>|<ch>|<val>|...
    where universe and ch are 1-based.

    Returns dict keyed by 1-based absolute channel number within universe 0:
        { "1": 0, "4": 241, "7": 255, ... }
    """
    # Determine how many channels we need (highest fixture end address)
    max_ch = 32
    try:
        for fixture in get_workspace_fixtures():
            top = fixture["universe"] * 512 + fixture["address"] + fixture["channels"]
            max_ch = max(max_ch, top)
    except Exception:
        pass

    try:
        values = {str(k): v for k, v in get_current_channel_values(max_ch).items()}
        return jsonify({"values": values})
    except Exception as e:
        return jsonify({"values": {}, "error": str(e)})


# =============================================================================
# Tier 1 endpoints — group CRUD, scene management, identify, blackout, batch
# =============================================================================
#
# These extend the API surface without changing existing behavior. Each block
# is bracketed with a short rationale so it's easy to find later.


# ----------------------------------------------------------------------------
# Group CRUD
# ----------------------------------------------------------------------------
# Storage: ~/.qlcplus/fixture_groups.json. The on-disk format is
#     {"groups": {<name>: {"fixtures": [<id>...], "description": "..."}}}
# Some legacy files store the inner dict directly without the "groups" key —
# _load_groups handles both, _save_groups always writes the wrapped form.

def _load_groups() -> dict:
    """Return the groups dict in {name: {fixtures, description}} form.

    Tolerates the legacy unwrapped format. Returns an empty dict if the file
    doesn't exist yet.
    """
    if not GROUPS_FILE.exists():
        return {}
    try:
        data = json.loads(GROUPS_FILE.read_text())
    except json.JSONDecodeError:
        return {}
    if isinstance(data, dict) and "groups" in data and isinstance(data["groups"], dict):
        return data["groups"]
    return data if isinstance(data, dict) else {}


def _save_groups(groups: dict) -> None:
    """Persist the groups dict in the canonical wrapped format."""
    GROUPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    GROUPS_FILE.write_text(json.dumps({"groups": groups}, indent=2))


def _existing_fixture_ids() -> set:
    """Set of int fixture IDs declared in the current workspace."""
    return {int(f["id"]) for f in get_workspace_fixtures()}


def _normalize_fixture_ids(raw):
    """Coerce a fixture_ids input into a deduped list of ints.

    Accepts list of ints / numeric strings, drops anything unparseable.
    """
    result = []
    seen = set()
    for v in raw or []:
        try:
            fid = int(v)
        except (TypeError, ValueError):
            continue
        if fid not in seen:
            seen.add(fid)
            result.append(fid)
    return result


@app.route("/api/groups", methods=["POST"])
def create_group():
    """Create a new fixture group.

    Body:
        {
          "name": "key-lights",
          "fixtures": [0, 3, 4],
          "description": "Front key wash"   # optional
        }
    """
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "name is required"}), 400

    groups = _load_groups()
    if name in groups:
        return jsonify({"success": False, "error": f"Group '{name}' already exists"}), 409

    fixture_ids = _normalize_fixture_ids(data.get("fixtures"))
    valid_ids = _existing_fixture_ids()
    unknown = [fid for fid in fixture_ids if fid not in valid_ids]
    if unknown:
        return jsonify({
            "success": False,
            "error": f"Unknown fixture IDs: {unknown}",
            "valid_fixture_ids": sorted(valid_ids),
        }), 400

    groups[name] = {
        "fixtures": fixture_ids,
        "description": (data.get("description") or "").strip(),
    }
    _save_groups(groups)
    return jsonify({
        "success": True,
        "group": {
            "name": name,
            "fixtures": fixture_ids,
            "description": groups[name]["description"],
        },
    })


@app.route("/api/groups/<group_name>", methods=["DELETE"])
def delete_group(group_name):
    """Remove a fixture group. Idempotent: returns 404 if missing."""
    groups = _load_groups()
    if group_name not in groups:
        return jsonify({"success": False, "error": f"Group '{group_name}' not found"}), 404
    del groups[group_name]
    _save_groups(groups)
    return jsonify({"success": True})


@app.route("/api/groups/<group_name>", methods=["PATCH"])
def update_group(group_name):
    """Rename a group or update its description / fixture list.

    Body (all fields optional):
        {
          "name": "new-name",
          "description": "...",
          "fixtures": [0, 3, 4]   # replaces the full list
        }
    """
    data = request.get_json(silent=True) or {}
    groups = _load_groups()
    if group_name not in groups:
        return jsonify({"success": False, "error": f"Group '{group_name}' not found"}), 404

    group = groups[group_name]
    new_name = (data.get("name") or "").strip()
    if new_name and new_name != group_name:
        if new_name in groups:
            return jsonify({
                "success": False,
                "error": f"Group '{new_name}' already exists"
            }), 409
        groups[new_name] = group
        del groups[group_name]
        group_name = new_name

    if "description" in data:
        groups[group_name]["description"] = (data.get("description") or "").strip()

    if "fixtures" in data:
        fixture_ids = _normalize_fixture_ids(data.get("fixtures"))
        valid_ids = _existing_fixture_ids()
        unknown = [fid for fid in fixture_ids if fid not in valid_ids]
        if unknown:
            return jsonify({
                "success": False,
                "error": f"Unknown fixture IDs: {unknown}"
            }), 400
        groups[group_name]["fixtures"] = fixture_ids

    _save_groups(groups)
    return jsonify({
        "success": True,
        "group": {
            "name": group_name,
            "fixtures": groups[group_name].get("fixtures", []),
            "description": groups[group_name].get("description", ""),
        },
    })


@app.route("/api/groups/<group_name>/fixtures", methods=["POST"])
def add_fixtures_to_group(group_name):
    """Append fixture IDs to a group (preserving existing members).

    Body: { "fixtures": [0, 3, 4] }
    """
    data = request.get_json(silent=True) or {}
    groups = _load_groups()
    if group_name not in groups:
        return jsonify({"success": False, "error": f"Group '{group_name}' not found"}), 404

    to_add = _normalize_fixture_ids(data.get("fixtures"))
    valid_ids = _existing_fixture_ids()
    unknown = [fid for fid in to_add if fid not in valid_ids]
    if unknown:
        return jsonify({
            "success": False,
            "error": f"Unknown fixture IDs: {unknown}"
        }), 400

    current = list(groups[group_name].get("fixtures") or [])
    seen = set(current)
    for fid in to_add:
        if fid not in seen:
            current.append(fid)
            seen.add(fid)
    groups[group_name]["fixtures"] = current
    _save_groups(groups)

    return jsonify({
        "success": True,
        "group": {
            "name": group_name,
            "fixtures": current,
            "description": groups[group_name].get("description", ""),
        },
    })


@app.route("/api/groups/<group_name>/fixtures", methods=["DELETE"])
def remove_fixtures_from_group(group_name):
    """Remove fixture IDs from a group. Missing IDs are ignored silently.

    Body: { "fixtures": [3, 4] }
    """
    data = request.get_json(silent=True) or {}
    groups = _load_groups()
    if group_name not in groups:
        return jsonify({"success": False, "error": f"Group '{group_name}' not found"}), 404

    to_remove = set(_normalize_fixture_ids(data.get("fixtures")))
    current = [fid for fid in (groups[group_name].get("fixtures") or []) if fid not in to_remove]
    groups[group_name]["fixtures"] = current
    _save_groups(groups)

    return jsonify({
        "success": True,
        "group": {
            "name": group_name,
            "fixtures": current,
            "description": groups[group_name].get("description", ""),
        },
    })


# ----------------------------------------------------------------------------
# Scene management — describe / delete / rename / duplicate
# ----------------------------------------------------------------------------

def _scene_value_breakdown(scene_root) -> list:
    """Convert a scene <Function> element to a fixture-keyed value breakdown.

    Returns a list of dicts:
        [{ "fixture_id": 0, "fixture_name": "SlimPAR Pro",
           "channels": [{ "offset": 0, "name": "Dimmer", "value": 200 }, ...] }, ...]

    The channel name comes from the .qxf parser when available.
    """
    fixtures_by_id = {str(f["id"]): f for f in get_workspace_fixtures()}
    out = []
    for fixture_val in _find_children(scene_root, "FixtureVal"):
        fid = fixture_val.get("ID")
        fixture = fixtures_by_id.get(str(fid))
        if not fixture or not fixture_val.text:
            continue

        raw_parts = [p.strip() for p in fixture_val.text.split(",") if p.strip() != ""]
        pairs = []
        for i in range(0, len(raw_parts) - 1, 2):
            try:
                pairs.append((int(raw_parts[i]), int(raw_parts[i + 1])))
            except ValueError:
                continue
        if not pairs:
            continue

        # Detect 0-based vs 1-based channel numbering (same logic as
        # scene_to_channel_values) so the offsets we report are 0-based.
        zero_based = any(channel == 0 for channel, _ in pairs)

        channel_info = _fixture_channels_info(fixture)
        info_by_offset = {ci["offset"]: ci for ci in channel_info}

        channels = []
        for raw_ch, value in pairs:
            offset = raw_ch if zero_based else raw_ch - 1
            ci = info_by_offset.get(offset, {})
            channels.append({
                "offset": offset,
                "name": ci.get("name", f"Ch {offset + 1}"),
                "role": ci.get("role"),
                "value": value,
            })

        out.append({
            "fixture_id": int(fid),
            "fixture_name": fixture.get("name", ""),
            "channels": channels,
        })
    return out


@app.route("/api/scenes/<scene_id>", methods=["GET"])
def describe_scene(scene_id):
    """Return the contents of a saved scene: fixture/channel/value breakdown."""
    scene = _find_scene_element(scene_id)
    if scene is None:
        return jsonify({"success": False, "error": f"Scene not found: {scene_id}"}), 404
    return jsonify({
        "success": True,
        "scene": {
            "id": int(scene.get("ID", "0")) if (scene.get("ID") or "").isdigit() else scene.get("ID"),
            "name": scene.get("Name", ""),
            "path": scene.get("Path", ""),
        },
        "fixtures": _scene_value_breakdown(scene),
    })


@app.route("/api/scenes/<scene_id>", methods=["DELETE"])
def delete_scene(scene_id):
    """Delete a saved scene from the workspace permanently."""
    try:
        tree = ET.parse(WORKSPACE_PATH)
        root = tree.getroot()
        engine = _engine_element(root)
        if engine is None:
            return jsonify({"success": False, "error": "No Engine element in workspace"}), 500

        needle = str(scene_id).strip().lower()
        ns = "http://www.qlcplus.org/Workspace"
        target = None
        for func in list(engine.findall(f"{{{ns}}}Function")) + list(engine.findall("Function")):
            if func.get("Type") != "Scene":
                continue
            if func.get("ID") == str(scene_id) or (func.get("Name") or "").lower() == needle:
                target = func
                break
        if target is None:
            return jsonify({"success": False, "error": f"Scene not found: {scene_id}"}), 404

        engine.remove(target)
        tree.write(str(WORKSPACE_PATH), encoding="UTF-8", xml_declaration=True)
        return jsonify({
            "success": True,
            "deleted": {"id": target.get("ID"), "name": target.get("Name")},
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/scenes/<scene_id>", methods=["PATCH"])
def rename_scene(scene_id):
    """Rename a scene. Body: { "name": "New Name", "path": "AI Generated" }"""
    data = request.get_json(silent=True) or {}
    new_name = (data.get("name") or "").strip()
    new_path = data.get("path")
    if not new_name and new_path is None:
        return jsonify({"success": False, "error": "Provide name and/or path"}), 400

    try:
        tree = ET.parse(WORKSPACE_PATH)
        root = tree.getroot()
        engine = _engine_element(root)
        if engine is None:
            return jsonify({"success": False, "error": "No Engine element in workspace"}), 500

        needle = str(scene_id).strip().lower()
        ns = "http://www.qlcplus.org/Workspace"
        target = None
        for func in list(engine.findall(f"{{{ns}}}Function")) + list(engine.findall("Function")):
            if func.get("Type") != "Scene":
                continue
            if func.get("ID") == str(scene_id) or (func.get("Name") or "").lower() == needle:
                target = func
                break
        if target is None:
            return jsonify({"success": False, "error": f"Scene not found: {scene_id}"}), 404

        if new_name:
            target.set("Name", new_name)
        if new_path is not None:
            target.set("Path", new_path)
        tree.write(str(WORKSPACE_PATH), encoding="UTF-8", xml_declaration=True)
        return jsonify({
            "success": True,
            "scene": {
                "id": target.get("ID"),
                "name": target.get("Name"),
                "path": target.get("Path", ""),
            },
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/scenes/<scene_id>/duplicate", methods=["POST"])
def duplicate_scene(scene_id):
    """Duplicate an existing scene under a new name. Body: { "name": "..." }"""
    data = request.get_json(silent=True) or {}
    new_name = (data.get("name") or "").strip()
    if not new_name:
        return jsonify({"success": False, "error": "name is required"}), 400

    try:
        import copy as _copy
        tree = ET.parse(WORKSPACE_PATH)
        root = tree.getroot()
        engine = _engine_element(root)
        if engine is None:
            return jsonify({"success": False, "error": "No Engine element in workspace"}), 500

        needle = str(scene_id).strip().lower()
        ns = "http://www.qlcplus.org/Workspace"
        source = None
        for func in list(engine.findall(f"{{{ns}}}Function")) + list(engine.findall("Function")):
            if func.get("Type") != "Scene":
                continue
            if func.get("ID") == str(scene_id) or (func.get("Name") or "").lower() == needle:
                source = func
                break
        if source is None:
            return jsonify({"success": False, "error": f"Scene not found: {scene_id}"}), 404

        clone = _copy.deepcopy(source)
        clone.set("Name", new_name)
        new_id = get_next_scene_id()
        clone.set("ID", str(new_id))
        engine.append(clone)
        tree.write(str(WORKSPACE_PATH), encoding="UTF-8", xml_declaration=True)
        return jsonify({
            "success": True,
            "scene": {
                "id": new_id,
                "name": new_name,
                "source_id": source.get("ID"),
                "source_name": source.get("Name"),
            },
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ----------------------------------------------------------------------------
# identify_fixture — visual ping
# ----------------------------------------------------------------------------

async def _identify_fixture_async(fixture, duration=2.0, pulses=4):
    """Flash a fixture's dimmer/brightness channels on-off-on-off then restore.

    Uses the persistent QLC+ WebSocket. Total pattern time ≈ `duration`
    seconds spread across `pulses` on-off cycles, then a final restore frame.
    """
    ws = await _ensure_qlc_ws()
    roles = _fixture_roles(fixture)
    brightness_offsets = roles.get("brightness", [])
    if not brightness_offsets:
        # Fall back to channel 0 if we can't find a brightness channel
        brightness_offsets = [0]

    # Read current values so we can restore them when done
    max_ch = fixture["universe"] * 512 + fixture["address"] + fixture["channels"]
    current = await _fetch_channel_values(max_ch)

    abs_channels = [_absolute_channel(fixture, offset) for offset in brightness_offsets]
    half_period = duration / (pulses * 2) if pulses > 0 else 0.25

    async def _send(value):
        commands = [f"CH|{ch}|{value}" for ch in abs_channels]
        async with _qlc_ws_lock:
            for cmd in commands:
                await ws.send(cmd)

    try:
        for _ in range(pulses):
            await _send(255)
            await asyncio.sleep(half_period)
            await _send(0)
            await asyncio.sleep(half_period)
        # Restore previous values
        restore_commands = [f"CH|{ch}|{int(current.get(ch, 0))}" for ch in abs_channels]
        async with _qlc_ws_lock:
            for cmd in restore_commands:
                await ws.send(cmd)
        return True
    except Exception as e:
        print(f"identify_fixture error: {e}")
        return False


@app.route("/api/fixtures/<int:fixture_id>/identify", methods=["POST"])
def identify_fixture(fixture_id):
    """Pulse a single fixture so the user can visually identify which is which.

    Body (optional):
        { "duration": 2.0, "pulses": 4 }
    """
    fixtures = get_workspace_fixtures()
    match = next((f for f in fixtures if int(f["id"]) == int(fixture_id)), None)
    if match is None:
        return jsonify({"success": False, "error": f"Fixture {fixture_id} not found"}), 404

    data = request.get_json(silent=True) or {}
    try:
        duration = max(0.5, min(10.0, float(data.get("duration", 2.0))))
    except (TypeError, ValueError):
        duration = 2.0
    try:
        pulses = max(1, min(10, int(data.get("pulses", 4))))
    except (TypeError, ValueError):
        pulses = 4

    try:
        success = _qlc_run(
            _identify_fixture_async(match, duration=duration, pulses=pulses),
            timeout=duration + 5,
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    return jsonify({
        "success": success,
        "fixture": {
            "id": match["id"],
            "name": match["name"],
        },
        "duration": duration,
        "pulses": pulses,
    })


# ----------------------------------------------------------------------------
# blackout — instant zero on all (or grouped) fixtures
# ----------------------------------------------------------------------------

@app.route("/api/blackout", methods=["POST"])
def blackout():
    """Instantly drive every channel of the targeted fixtures to 0.

    Body (optional): { "groups": ["key-lights"] }  # defaults to all fixtures

    Distinct from fade(target:0, duration:0) because it writes EVERY channel
    on the fixture (not just brightness-role channels), so any active strobe,
    macro, or color state is also cleared. Use for "kill it all" moments.
    """
    data = request.get_json(silent=True) or {}
    target_groups = data.get("groups") or None
    fixtures = _target_fixtures(target_groups)

    updates = []
    for fixture in fixtures:
        for offset in range(int(fixture.get("channels", 0))):
            updates.append((_absolute_channel(fixture, offset), 0))

    success = set_channel_values(updates) if updates else True
    return jsonify({
        "success": success,
        "fixtures": len(fixtures),
        "channels_zeroed": len(updates),
        "groups": target_groups,
    })


# ----------------------------------------------------------------------------
# batch_action — dispatch multiple actions in a single HTTP round trip
# ----------------------------------------------------------------------------

@app.route("/api/batch", methods=["POST"])
def batch_action():
    """Execute an ordered list of actions in one request.

    Body:
        {
          "actions": [
            { "action": "adjust_color", "parameters": {"color": "warm"}, "groups": ["key-lights"] },
            { "action": "adjust_color", "parameters": {"color": "cool"}, "groups": ["fill-lights"] },
            { "action": "fade", "parameters": {"target": "0", "duration": "5"} }
          ]
        }

    Returns a per-action result array plus an aggregate success flag.
    Stops on first failure and reports which step broke; remaining
    actions are skipped (stop_on_error: true by default).
    """
    import time as _time
    data = request.get_json(silent=True) or {}
    actions = data.get("actions") or []
    stop_on_error = data.get("stop_on_error", True)

    if not isinstance(actions, list) or not actions:
        return jsonify({"success": False, "error": "Provide non-empty 'actions' array"}), 400

    results = []
    overall_success = True
    t0 = _time.time()

    for i, step in enumerate(actions):
        if not isinstance(step, dict):
            results.append({"index": i, "success": False, "error": "step must be an object"})
            overall_success = False
            if stop_on_error:
                break
            continue

        action_data = {
            "action": step.get("action"),
            "parameters": step.get("parameters", {}) or {},
            "explanation": step.get("explanation", ""),
        }
        groups = step.get("groups") or None

        try:
            result = execute_lighting_action(action_data, target_groups=groups)
            results.append({
                "index": i,
                "action": action_data["action"],
                "success": result["success"],
                "output": result.get("output", ""),
                "error": result.get("error", "") if not result["success"] else "",
            })
            if not result["success"]:
                overall_success = False
                if stop_on_error:
                    break
        except Exception as e:
            results.append({
                "index": i,
                "action": action_data["action"],
                "success": False,
                "error": str(e),
            })
            overall_success = False
            if stop_on_error:
                break

    return jsonify({
        "success": overall_success,
        "executed": len(results),
        "total_requested": len(actions),
        "results": results,
        "debug": {
            "total_ms": round((_time.time() - t0) * 1000),
        },
    })


# =============================================================================
# Diagnostics surface (issue #9)
# =============================================================================
#
# test_dmx       — visible R/G/B sweep across every color-aware fixture
# get_logs      — read systemd journal for a known service (allowlisted)
# get_system   — Pi-level health: CPU temp, load, mem, disk, uptime, USB,
#                  service status
#
# All three are intended to help an agent / operator debug a misbehaving
# rig without SSHing in. None of them touch persistent state; test_dmx
# saves & restores channel values so the rig returns to its pre-test look.


# ----------------------------------------------------------------------------
# test_dmx
# ----------------------------------------------------------------------------

async def _test_dmx_async(fixtures, duration=5.0):
    """Drive a known-good R → G → B → off sweep across every color-aware
    fixture's channels, then restore the previous values.

    Phase split: 4 phases of duration/4 each (red, green, blue, off-restore).
    Within each phase we send one frame at the start; QLC+ holds it until
    the next frame.
    """
    ws = await _ensure_qlc_ws()

    # Snapshot pre-test channel values so we can restore precisely
    max_ch = 32
    for f in fixtures:
        max_ch = max(max_ch, f["universe"] * 512 + f["address"] + f["channels"])
    pre_values = await _fetch_channel_values(max_ch)

    # Collect (absolute_channel, role) tuples for every targeted fixture's
    # color-relevant channels. Falls back to brightness if no color roles
    # exist (e.g. single-channel dimmers — they'll just pulse on/off).
    color_role_priorities = [
        ("red", "green", "blue"),               # RGB fixtures
        ("warm", "cool", "amber"),              # WWA / tungsten fixtures
        ("white",),                             # white-only
    ]
    fixture_plans = []
    for fixture in fixtures:
        roles = _fixture_roles(fixture)
        plan = {}
        for triplet in color_role_priorities:
            if any(r in roles for r in triplet):
                for r in triplet:
                    if r in roles and isinstance(roles[r], int):
                        plan[r] = _absolute_channel(fixture, roles[r])
                break
        # Fall back to brightness/dimmer for fixtures without color channels
        if not plan and "brightness" in roles:
            plan["dimmer"] = _absolute_channel(fixture, roles["brightness"][0])
        fixture_plans.append((fixture, plan, roles))

    async def _send_frame(commands):
        async with _qlc_ws_lock:
            for cmd in commands:
                await ws.send(cmd)

    # Map phase → role pattern for each fixture type
    def _phase_value(plan, phase):
        """Return a {abs_channel: value} map for the given phase index."""
        out = {}
        for role, abs_ch in plan.items():
            if role == "red":
                out[abs_ch] = 255 if phase == 0 else 0
            elif role == "green":
                out[abs_ch] = 255 if phase == 1 else 0
            elif role == "blue":
                out[abs_ch] = 255 if phase == 2 else 0
            elif role == "warm":
                out[abs_ch] = 255 if phase == 0 else 0
            elif role == "cool":
                out[abs_ch] = 255 if phase == 2 else 0
            elif role == "amber":
                out[abs_ch] = 255 if phase == 1 else 0
            elif role == "white":
                out[abs_ch] = 255 if phase in (0, 1, 2) else 0
            elif role == "dimmer":
                out[abs_ch] = 255 if phase in (0, 1, 2) else 0
        return out

    phase_duration = max(0.5, duration / 4)
    try:
        for phase in range(3):
            commands = []
            for _fixture, plan, roles in fixture_plans:
                # Drive dimmer channel up too if it exists (so RGB-only
                # writes don't appear black due to a closed dimmer)
                if "dimmer" in roles and isinstance(roles["dimmer"], int):
                    dimmer_ch = _absolute_channel(_fixture, roles["dimmer"])
                    commands.append(f"CH|{dimmer_ch}|255")
                for ch, val in _phase_value(plan, phase).items():
                    commands.append(f"CH|{ch}|{val}")
            await _send_frame(commands)
            await asyncio.sleep(phase_duration)

        # Restore phase — push every channel that we touched back to its
        # pre-test value
        restore_commands = []
        seen_channels = set()
        for _fixture, plan, roles in fixture_plans:
            for ch in plan.values():
                seen_channels.add(ch)
            if "dimmer" in roles and isinstance(roles["dimmer"], int):
                seen_channels.add(_absolute_channel(_fixture, roles["dimmer"]))
        for ch in sorted(seen_channels):
            restore_commands.append(f"CH|{ch}|{int(pre_values.get(ch, 0))}")
        await _send_frame(restore_commands)
        return True
    except Exception as e:
        print(f"test_dmx error: {e}")
        return False


@app.route("/api/diagnostics/test_dmx", methods=["POST"])
def diagnostics_test_dmx():
    """Run a known-good color sweep across every targeted fixture.

    Body (optional):
        { "duration": 5.0, "groups": ["key-lights"] }

    Returns after the sweep completes (typically 5 seconds). Channel state
    is snapshotted before the test and restored at the end, so the rig
    returns to whatever it looked like before the call.
    """
    data = request.get_json(silent=True) or {}
    try:
        duration = max(2.0, min(30.0, float(data.get("duration", 5.0))))
    except (TypeError, ValueError):
        duration = 5.0
    target_groups = data.get("groups") or None
    fixtures = _target_fixtures(target_groups)

    if not fixtures:
        return jsonify({
            "success": False,
            "error": "No fixtures matched the request",
        }), 400

    try:
        success = _qlc_run(
            _test_dmx_async(fixtures, duration=duration),
            timeout=duration + 10,
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    return jsonify({
        "success": success,
        "fixtures_tested": len(fixtures),
        "duration": duration,
        "groups": target_groups,
        "pattern": "red → green → blue → restore",
    })


# ----------------------------------------------------------------------------
# get_logs — read systemd journals (allowlisted services only)
# ----------------------------------------------------------------------------

LOG_ALLOWED_SERVICES = {
    "qlcplus-web": "qlcplus-web.service",
    "lighting-control": "lighting-control.service",
    "lighting-mcp": "lighting-mcp.service",
    "nginx": "nginx.service",
}


@app.route("/api/diagnostics/logs/<service>", methods=["GET"])
def diagnostics_logs(service):
    """Return the last N lines of a service's systemd journal.

    Path: /api/diagnostics/logs/<service>?n=50

    Allowed services: qlcplus-web, lighting-control, lighting-mcp, nginx.
    Unknown service names are rejected (allowlist enforced — no shell
    injection surface).
    """
    if service not in LOG_ALLOWED_SERVICES:
        return jsonify({
            "success": False,
            "error": f"Unknown service '{service}'. Allowed: {sorted(LOG_ALLOWED_SERVICES.keys())}",
        }), 400

    try:
        n = int(request.args.get("n", 50))
    except (TypeError, ValueError):
        n = 50
    n = max(1, min(500, n))

    unit = LOG_ALLOWED_SERVICES[service]

    if not IS_LOCAL:
        # When running remotely (e.g. developer workstation pointed at a
        # remote rig) we can't read journals directly — bail out clearly.
        return jsonify({
            "success": False,
            "error": "get_logs is only available when running on the Pi itself",
            "is_local": False,
        }), 503

    result = execute_command(f"journalctl -u {unit} -n {n} --no-pager --output=short-iso")
    raw = result.get("output", "")
    lines = [line for line in raw.splitlines() if line.strip()]

    return jsonify({
        "success": result["success"],
        "service": service,
        "unit": unit,
        "lines_returned": len(lines),
        "lines_requested": n,
        "lines": lines,
        "error": result.get("error", "") if not result["success"] else "",
    })


# ----------------------------------------------------------------------------
# get_system_info — Pi-level health & inventory
# ----------------------------------------------------------------------------

def _read_first_line(path: str) -> str:
    try:
        return Path(path).read_text().strip().splitlines()[0]
    except Exception:
        return ""


def _read_proc_meminfo() -> dict:
    """Parse /proc/meminfo into kB integers. Returns {} on non-Linux."""
    info = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if ":" not in line:
                continue
            key, _, rest = line.partition(":")
            parts = rest.strip().split()
            if parts and parts[0].isdigit():
                info[key.strip()] = int(parts[0])  # kB
    except Exception:
        return {}
    return info


@app.route("/api/diagnostics/system", methods=["GET"])
def diagnostics_system():
    """Return Pi-level health info: CPU temp, load, memory, disk, uptime,
    USB devices (ENTTEC filter), and service status for the three services
    we manage.

    Most fields are best-effort — fields that aren't available on this
    platform (e.g. /sys/class/thermal on macOS dev machines) are reported
    as null rather than failing the whole call.
    """
    import shutil as _shutil

    out: dict = {
        "is_local": IS_LOCAL,
        "platform": sys.platform,
    }

    # CPU temperature — Linux only
    try:
        millic = _read_first_line("/sys/class/thermal/thermal_zone0/temp")
        out["cpu_temp_c"] = round(int(millic) / 1000.0, 1) if millic.isdigit() else None
    except Exception:
        out["cpu_temp_c"] = None

    # Load average — POSIX
    try:
        load1, load5, load15 = os.getloadavg()
        out["load_avg"] = {"1m": round(load1, 2), "5m": round(load5, 2), "15m": round(load15, 2)}
    except (OSError, AttributeError):
        out["load_avg"] = None

    # Memory — /proc/meminfo (Linux only)
    mem = _read_proc_meminfo()
    if mem:
        total_kb = mem.get("MemTotal", 0)
        avail_kb = mem.get("MemAvailable", mem.get("MemFree", 0))
        used_kb = max(0, total_kb - avail_kb)
        out["memory"] = {
            "total_mb": round(total_kb / 1024, 1),
            "used_mb": round(used_kb / 1024, 1),
            "available_mb": round(avail_kb / 1024, 1),
            "used_pct": round((used_kb / total_kb) * 100, 1) if total_kb else None,
        }
    else:
        out["memory"] = None

    # Disk usage — / and /home if present
    disk = {}
    for mount in ("/", "/home"):
        try:
            usage = _shutil.disk_usage(mount)
            disk[mount] = {
                "total_gb": round(usage.total / (1024 ** 3), 1),
                "used_gb": round(usage.used / (1024 ** 3), 1),
                "free_gb": round(usage.free / (1024 ** 3), 1),
                "used_pct": round((usage.used / usage.total) * 100, 1),
            }
        except FileNotFoundError:
            continue
        except Exception:
            disk[mount] = None
    out["disk"] = disk

    # Uptime — /proc/uptime (Linux) seconds since boot
    try:
        uptime_line = _read_first_line("/proc/uptime")
        if uptime_line:
            seconds = float(uptime_line.split()[0])
            out["uptime_seconds"] = int(seconds)
            # Human-readable summary
            d, rem = divmod(int(seconds), 86400)
            h, rem = divmod(rem, 3600)
            m, _ = divmod(rem, 60)
            parts = []
            if d:
                parts.append(f"{d}d")
            if h:
                parts.append(f"{h}h")
            if m or not parts:
                parts.append(f"{m}m")
            out["uptime_human"] = " ".join(parts)
        else:
            out["uptime_seconds"] = None
            out["uptime_human"] = None
    except Exception:
        out["uptime_seconds"] = None
        out["uptime_human"] = None

    # USB devices — filter to FTDI / ENTTEC related lines
    if IS_LOCAL:
        usb = execute_command("lsusb")
        if usb["success"]:
            all_lines = [ln for ln in usb["output"].splitlines() if ln.strip()]
            interesting = [ln for ln in all_lines if any(
                k in ln.lower() for k in ("ftdi", "enttec", "dmx")
            )]
            out["usb"] = {
                "all_count": len(all_lines),
                "dmx_related": interesting,
            }
        else:
            out["usb"] = None
    else:
        out["usb"] = None

    # Service status for the three units (only when local)
    services_status = {}
    if IS_LOCAL:
        for label, unit in LOG_ALLOWED_SERVICES.items():
            if label == "nginx":
                continue  # nginx is optional and reporting failure noisy
            check = execute_command(f"systemctl is-active {unit}")
            services_status[label] = (check.get("output") or "").strip() or "unknown"
    out["services"] = services_status or None

    return jsonify({"success": True, **out})


# =============================================================================
# Chases / sequences (issue #4)
# =============================================================================
#
# A chase in QLC+ is a <Function Type="Chaser"> in the workspace's Engine
# element. Each chase has function-level Speed (default FadeIn/Hold/FadeOut)
# plus an ordered list of <Step> elements that reference scenes by ID and
# can override per-step timing.
#
# Playback is driven over the QLC+API WebSocket via
#     QLC+API|setFunctionStatus|<id>|1   (start)
#     QLC+API|setFunctionStatus|<id>|0   (stop)
#
# Chase XML format we generate (QLC+ 4.14.x):
#     <Function ID="N" Type="Chaser" Name="..." Path="...">
#       <Speed FadeIn="500" FadeOut="500" Duration="2000"/>
#       <Direction>Forward</Direction>
#       <RunOrder>Loop</RunOrder>
#       <SpeedModes FadeIn="Default" FadeOut="Default" Duration="Default"/>
#       <Step Number="0" FadeIn="500" Hold="2000" FadeOut="500" Values="42"/>
#       <Step Number="1" FadeIn="500" Hold="2000" FadeOut="500" Values="43"/>
#       ...
#     </Function>


# Enum normalization tables — accept lowercase, store canonical
_CHASE_DIRECTIONS = {"forward": "Forward", "backward": "Backward"}
_CHASE_RUN_ORDERS = {
    "loop": "Loop",
    "singleshot": "SingleShot",
    "single-shot": "SingleShot",
    "single_shot": "SingleShot",
    "pingpong": "PingPong",
    "ping-pong": "PingPong",
    "ping_pong": "PingPong",
    "random": "Random",
}


def _normalize_direction(value: str | None, default: str = "Forward") -> str:
    if not value:
        return default
    return _CHASE_DIRECTIONS.get(str(value).strip().lower(), default)


def _normalize_run_order(value: str | None, default: str = "Loop") -> str:
    if not value:
        return default
    return _CHASE_RUN_ORDERS.get(str(value).strip().lower(), default)


def _engine_functions(engine):
    """All <Function> children of Engine, namespace-tolerant."""
    if engine is None:
        return []
    ns = "http://www.qlcplus.org/Workspace"
    return list(engine.findall(f"{{{ns}}}Function")) + list(engine.findall("Function"))


def get_next_function_id() -> int:
    """Return the next unused Function ID across the entire workspace.

    Functions of every type (Scene, Chaser, EFX, Sequence, …) share a single
    ID space in QLC+, so this scans ALL functions — not just scenes.
    Generalization of the existing get_next_scene_id which only saw scenes.
    """
    if not WORKSPACE_PATH.exists():
        return 0
    root = _workspace_root()
    engine = _engine_element(root)
    if engine is None:
        return 0
    max_id = -1
    for func in _engine_functions(engine):
        fid = func.get("ID", "")
        if fid.isdigit():
            n = int(fid)
            if n > max_id:
                max_id = n
    return max_id + 1


def _find_function_element(id_or_name, function_type: str | None = None):
    """Find a Function by ID or by case-insensitive Name.

    If function_type is given (e.g. "Chaser"), only matches functions of
    that type — so "Red" won't accidentally resolve to a chase if a scene
    of the same name exists.
    """
    if not WORKSPACE_PATH.exists():
        return None
    root = _workspace_root()
    engine = _engine_element(root)
    if engine is None:
        return None
    needle = str(id_or_name).strip().lower()
    for func in _engine_functions(engine):
        if function_type and func.get("Type") != function_type:
            continue
        if func.get("ID") == str(id_or_name) or (func.get("Name") or "").lower() == needle:
            return func
    return None


def get_workspace_chases() -> list[dict]:
    """Return chase summaries from the loaded workspace."""
    if not WORKSPACE_PATH.exists():
        return []
    root = _workspace_root()
    engine = _engine_element(root)
    if engine is None:
        return []
    out = []
    for func in _engine_functions(engine):
        if func.get("Type") != "Chaser":
            continue
        fid = func.get("ID", "")
        speed = next(iter(_find_children(func, "Speed")), None)
        direction = next(iter(_find_children(func, "Direction")), None)
        run_order = next(iter(_find_children(func, "RunOrder")), None)
        steps = _find_children(func, "Step")
        out.append({
            "id": int(fid) if fid.isdigit() else fid,
            "name": func.get("Name", ""),
            "path": func.get("Path", ""),
            "steps": len(steps),
            "direction": direction.text if direction is not None else "Forward",
            "run_order": run_order.text if run_order is not None else "Loop",
            "speed": {
                "fade_in_ms":  int(speed.get("FadeIn", "0"))   if speed is not None else 0,
                "fade_out_ms": int(speed.get("FadeOut", "0"))  if speed is not None else 0,
                "hold_ms":     int(speed.get("Duration", "0")) if speed is not None else 0,
            },
        })
    return out


def _describe_chase_full(chase_element) -> dict:
    """Return the full chase shape (used by /api/chases/<id> GET)."""
    fid = chase_element.get("ID", "")
    speed = next(iter(_find_children(chase_element, "Speed")), None)
    direction = next(iter(_find_children(chase_element, "Direction")), None)
    run_order = next(iter(_find_children(chase_element, "RunOrder")), None)

    # Resolve scene ID → scene name for friendlier output
    scenes_by_id = {str(s["id"]): s for s in get_workspace_scenes()}

    steps = []
    for step in _find_children(chase_element, "Step"):
        # QLC+ writes the scene reference as the Values attribute in 4.12+
        scene_id = step.get("Values") or (step.text or "").strip()
        scene_info = scenes_by_id.get(str(scene_id))
        steps.append({
            "number": int(step.get("Number", "0")) if step.get("Number", "").isdigit() else 0,
            "scene_id": int(scene_id) if scene_id and str(scene_id).isdigit() else scene_id,
            "scene_name": scene_info["name"] if scene_info else None,
            "fade_in_ms":  int(step.get("FadeIn", "-1"))  if step.get("FadeIn", "-1").lstrip("-").isdigit() else -1,
            "hold_ms":     int(step.get("Hold", "-1"))    if step.get("Hold", "-1").lstrip("-").isdigit() else -1,
            "fade_out_ms": int(step.get("FadeOut", "-1")) if step.get("FadeOut", "-1").lstrip("-").isdigit() else -1,
        })
    steps.sort(key=lambda s: s["number"])

    return {
        "id": int(fid) if fid.isdigit() else fid,
        "name": chase_element.get("Name", ""),
        "path": chase_element.get("Path", ""),
        "direction": direction.text if direction is not None else "Forward",
        "run_order": run_order.text if run_order is not None else "Loop",
        "speed": {
            "fade_in_ms":  int(speed.get("FadeIn", "0"))   if speed is not None else 0,
            "fade_out_ms": int(speed.get("FadeOut", "0"))  if speed is not None else 0,
            "hold_ms":     int(speed.get("Duration", "0")) if speed is not None else 0,
        },
        "steps": steps,
    }


def _resolve_scene_id(scene_ref) -> int | None:
    """Coerce a step's scene_ref (int ID or string name) to a numeric scene ID."""
    if isinstance(scene_ref, bool):
        return None
    if isinstance(scene_ref, (int, float)):
        return int(scene_ref)
    if isinstance(scene_ref, str):
        text = scene_ref.strip()
        if text.isdigit():
            return int(text)
        # Look up by case-insensitive name
        needle = text.lower()
        for scene in get_workspace_scenes():
            if scene["name"].lower() == needle:
                return scene["id"]
    return None


def _build_chase_xml(
    name: str,
    steps: list,
    fade_in_ms: int,
    hold_ms: int,
    fade_out_ms: int,
    direction: str,
    run_order: str,
    path: str,
    chase_id: int,
) -> str:
    """Generate the <Function Type="Chaser"> XML to inject into the workspace."""
    lines = [
        f'<Function ID="{chase_id}" Type="Chaser" Name="{_xml_escape(name)}" Path="{_xml_escape(path)}">',
        f'  <Speed FadeIn="{fade_in_ms}" FadeOut="{fade_out_ms}" Duration="{hold_ms}"/>',
        f'  <Direction>{direction}</Direction>',
        f'  <RunOrder>{run_order}</RunOrder>',
        '  <SpeedModes FadeIn="Default" FadeOut="Default" Duration="Default"/>',
    ]
    for i, step in enumerate(steps):
        # step is { scene_id, fade_in_ms?, hold_ms?, fade_out_ms? } (already
        # normalized by the caller)
        sfi = step.get("fade_in_ms", fade_in_ms)
        sh  = step.get("hold_ms",    hold_ms)
        sfo = step.get("fade_out_ms", fade_out_ms)
        lines.append(
            f'  <Step Number="{i}" FadeIn="{sfi}" Hold="{sh}" FadeOut="{sfo}" Values="{step["scene_id"]}"/>'
        )
    lines.append('</Function>')
    return "\n".join(lines)


def _inject_chase_into_workspace(chase_xml: str) -> bool:
    """Inject the chase XML into the workspace's Engine element."""
    try:
        tree = ET.parse(WORKSPACE_PATH)
        root = tree.getroot()
        engine = _engine_element(root)
        if engine is None:
            return False
        chase_root = ET.fromstring(chase_xml.strip())
        engine.append(chase_root)
        tree.write(str(WORKSPACE_PATH), encoding="UTF-8", xml_declaration=True)
        return True
    except Exception as e:
        print(f"_inject_chase_into_workspace error: {e}")
        return False


async def _set_function_status_async(function_id: int, running: bool) -> str:
    """Tell QLC+ to start or stop a function via the persistent WebSocket."""
    flag = "1" if running else "0"
    return await _qlc_request_reply(
        f"QLC+API|setFunctionStatus|{function_id}|{flag}",
        response_marker="setFunctionStatus",
        timeout=2.0,
    )


def set_function_status(function_id: int, running: bool) -> tuple[bool, str]:
    """Sync wrapper around setFunctionStatus. Returns (ok, raw_response)."""
    try:
        raw = _qlc_run(_set_function_status_async(function_id, running), timeout=4)
        return True, raw
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# ------------------------------- endpoints ----------------------------------


@app.route("/api/chases", methods=["GET"])
def list_chases():
    """List all chases (Chaser functions) in the loaded workspace."""
    try:
        return jsonify({"chases": get_workspace_chases()})
    except Exception as e:
        return jsonify({"error": str(e), "chases": []}), 500


@app.route("/api/chases/<chase_id>", methods=["GET"])
def describe_chase(chase_id):
    """Return the full chase definition, including resolved per-step scene names."""
    chase = _find_function_element(chase_id, function_type="Chaser")
    if chase is None:
        return jsonify({"success": False, "error": f"Chase not found: {chase_id}"}), 404
    return jsonify({"success": True, "chase": _describe_chase_full(chase)})


@app.route("/api/chases", methods=["POST"])
def create_chase():
    """Create a new chase.

    Body:
        {
          "name": "Sunset",
          "steps": [                # required, ordered
            "Warm",                  # scene name (or numeric ID)
            42,                      # scene ID
            { "scene": "Amber", "hold_ms": 4000 }
          ],
          "fade_in_ms":  500,       # default per step
          "hold_ms":     2000,
          "fade_out_ms": 500,
          "direction":   "Forward",  # Forward | Backward
          "run_order":   "Loop",     # Loop | SingleShot | PingPong | Random
          "path":        "AI Generated"
        }
    """
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "name is required"}), 400

    raw_steps = data.get("steps") or []
    if not isinstance(raw_steps, list) or not raw_steps:
        return jsonify({"success": False, "error": "steps must be a non-empty array"}), 400

    fade_in_ms  = max(0, int(data.get("fade_in_ms",  500)))
    hold_ms     = max(0, int(data.get("hold_ms",     2000)))
    fade_out_ms = max(0, int(data.get("fade_out_ms", 500)))
    direction   = _normalize_direction(data.get("direction"))
    run_order   = _normalize_run_order(data.get("run_order"))
    path        = (data.get("path") or "AI Generated").strip()

    # Reject duplicate names — chase Name is the agent-friendly key
    if _find_function_element(name, function_type="Chaser") is not None:
        return jsonify({"success": False, "error": f"Chase '{name}' already exists"}), 409

    # Normalize and validate steps. Each step becomes
    # { scene_id, fade_in_ms?, hold_ms?, fade_out_ms? }.
    normalized_steps = []
    unknown_refs = []
    for i, raw in enumerate(raw_steps):
        if isinstance(raw, dict):
            scene_ref = raw.get("scene") or raw.get("scene_id") or raw.get("id")
            step_fade_in  = raw.get("fade_in_ms")
            step_hold     = raw.get("hold_ms")
            step_fade_out = raw.get("fade_out_ms")
        else:
            scene_ref = raw
            step_fade_in = step_hold = step_fade_out = None

        scene_id = _resolve_scene_id(scene_ref)
        if scene_id is None:
            unknown_refs.append({"step": i, "ref": scene_ref})
            continue

        normalized = {"scene_id": scene_id}
        if step_fade_in  is not None: normalized["fade_in_ms"]  = max(0, int(step_fade_in))
        if step_hold     is not None: normalized["hold_ms"]     = max(0, int(step_hold))
        if step_fade_out is not None: normalized["fade_out_ms"] = max(0, int(step_fade_out))
        normalized_steps.append(normalized)

    if unknown_refs:
        return jsonify({
            "success": False,
            "error": "One or more steps reference unknown scenes",
            "unknown": unknown_refs,
        }), 400

    chase_id = get_next_function_id()
    chase_xml = _build_chase_xml(
        name=name, steps=normalized_steps,
        fade_in_ms=fade_in_ms, hold_ms=hold_ms, fade_out_ms=fade_out_ms,
        direction=direction, run_order=run_order, path=path,
        chase_id=chase_id,
    )
    if not _inject_chase_into_workspace(chase_xml):
        return jsonify({"success": False, "error": "Failed to write chase to workspace"}), 500

    return jsonify({
        "success": True,
        "chase": {
            "id": chase_id,
            "name": name,
            "path": path,
            "steps": len(normalized_steps),
            "direction": direction,
            "run_order": run_order,
            "speed": {
                "fade_in_ms":  fade_in_ms,
                "hold_ms":     hold_ms,
                "fade_out_ms": fade_out_ms,
            },
        },
    })


@app.route("/api/chases/<chase_id>", methods=["DELETE"])
def delete_chase(chase_id):
    """Remove a chase from the workspace."""
    try:
        tree = ET.parse(WORKSPACE_PATH)
        root = tree.getroot()
        engine = _engine_element(root)
        if engine is None:
            return jsonify({"success": False, "error": "No Engine element in workspace"}), 500

        needle = str(chase_id).strip().lower()
        target = None
        for func in _engine_functions(engine):
            if func.get("Type") != "Chaser":
                continue
            if func.get("ID") == str(chase_id) or (func.get("Name") or "").lower() == needle:
                target = func
                break
        if target is None:
            return jsonify({"success": False, "error": f"Chase not found: {chase_id}"}), 404

        engine.remove(target)
        tree.write(str(WORKSPACE_PATH), encoding="UTF-8", xml_declaration=True)
        return jsonify({
            "success": True,
            "deleted": {"id": target.get("ID"), "name": target.get("Name")},
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/chases/<chase_id>/start", methods=["POST"])
def start_chase(chase_id):
    """Start chase playback via the QLC+ WebSocket API."""
    chase = _find_function_element(chase_id, function_type="Chaser")
    if chase is None:
        return jsonify({"success": False, "error": f"Chase not found: {chase_id}"}), 404
    fid = chase.get("ID")
    if not (fid and fid.isdigit()):
        return jsonify({"success": False, "error": f"Chase has no numeric ID: {chase.get('Name')}"}), 500
    ok, raw = set_function_status(int(fid), running=True)
    return jsonify({
        "success": ok,
        "chase": {"id": int(fid), "name": chase.get("Name")},
        "response": raw,
        "error": "" if ok else raw,
    })


@app.route("/api/chases/<chase_id>/stop", methods=["POST"])
def stop_chase(chase_id):
    """Stop chase playback via the QLC+ WebSocket API."""
    chase = _find_function_element(chase_id, function_type="Chaser")
    if chase is None:
        return jsonify({"success": False, "error": f"Chase not found: {chase_id}"}), 404
    fid = chase.get("ID")
    if not (fid and fid.isdigit()):
        return jsonify({"success": False, "error": f"Chase has no numeric ID: {chase.get('Name')}"}), 500
    ok, raw = set_function_status(int(fid), running=False)
    return jsonify({
        "success": ok,
        "chase": {"id": int(fid), "name": chase.get("Name")},
        "response": raw,
        "error": "" if ok else raw,
    })


if __name__ == "__main__":
    # Check if lightsctl exists
    if not LIGHTSCTL.exists():
        print(f"Error: lightsctl.sh not found at {LIGHTSCTL}")
        sys.exit(1)

    # Start the dedicated QLC+ WebSocket loop in a background thread.
    # All QLC+ comms go through this one persistent connection.
    _start_qlc_loop()
    try:
        _qlc_run(_ensure_qlc_ws(), timeout=5)
    except Exception as e:
        print(f"Warning: initial QLC+ connect failed (will retry on demand): {e}")

    # Run server with SocketIO (debug=False to avoid stat reloader doubling connections)
    port = int(os.getenv("CONTROL_PORT", "5000"))
    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)
