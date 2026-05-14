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
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

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

# Global QLC+ WebSocket connection
qlc_websocket = None


async def connect_to_qlc():
    """Connect to QLC+ WebSocket"""
    global qlc_websocket
    try:
        qlc_websocket = await websockets.connect(QLC_WS_URL)
        print(f"✓ Connected to QLC+ WebSocket at {QLC_WS_URL}")
        return True
    except Exception as e:
        print(f"✗ Failed to connect to QLC+: {e}")
        qlc_websocket = None
        return False


async def send_qlc_command(command):
    """Send command to QLC+ WebSocket"""
    global qlc_websocket
    try:
        if qlc_websocket is None or qlc_websocket.closed:
            await connect_to_qlc()
        
        if qlc_websocket:
            await qlc_websocket.send(command)
            return True
    except Exception as e:
        print(f"Error sending QLC+ command: {e}")
        qlc_websocket = None
    return False


def set_channel_value(universe, address, value):
    """
    Set DMX channel value via QLC+ Simple Desk
    
    Args:
        universe: Universe index (0-based)
        address: DMX address within universe (1-512)
        value: DMX value (0-255)
    """
    # Calculate absolute DMX address
    # Universe 0: addresses 1-512
    # Universe 1: addresses 513-1024, etc.
    absolute_address = (universe * 512) + address
    
    return set_channel_values([(absolute_address, value)])


def _run_async(coro):
    """Run a coroutine from Flask's sync request handlers."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def send_qlc_commands(commands):
    """Send one or more QLC+ WebSocket commands over a single connection."""
    ws = None
    try:
        ws = await websockets.connect(QLC_WS_URL, open_timeout=2, close_timeout=0.1)
        for command in commands:
            await ws.send(command)
        await asyncio.sleep(0.02)
        return True
    except Exception as e:
        print(f"Error sending QLC+ commands: {e}")
        return False
    finally:
        if ws is not None:
            transport = getattr(ws, "transport", None)
            if transport is not None:
                transport.abort()


def set_channel_values(channel_values):
    """Set absolute QLC+ channel values.

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
    return _run_async(send_qlc_commands(commands))


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
    values = {}
    ws = None
    try:
        ws = await websockets.connect(QLC_WS_URL, open_timeout=2, close_timeout=0.1)
        await ws.send(f"QLC+API|getChannelsValues|1|1|{max_ch}")
        deadline = time.time() + 2.0
        while time.time() < deadline:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
            except asyncio.TimeoutError:
                break
            if "getChannelsValues" not in msg:
                continue
            parts = msg.split("|")
            # QLC+ 4.14.1 live response starts channel/value groups at index 2:
            # QLC+API|getChannelsValues|1|0||2|0||...
            for i in range(2, len(parts) - 1, 3):
                try:
                    values[int(parts[i])] = int(parts[i + 1])
                except (ValueError, IndexError):
                    continue
            break
    finally:
        if ws is not None:
            transport = getattr(ws, "transport", None)
            if transport is not None:
                transport.abort()
    return values


def get_current_channel_values(max_ch=None):
    if max_ch is None:
        max_ch = 32
        for fixture in get_workspace_fixtures():
            max_ch = max(max_ch, fixture["universe"] * 512 + fixture["address"] + fixture["channels"])
    try:
        return _run_async(_fetch_channel_values(max_ch))
    except Exception as e:
        print(f"channel_values fetch error: {e}")
        return {}


def _fixture_roles(fixture):
    """Infer common color/intensity offsets for the fixtures used here."""
    channels = fixture["channels"]
    name = f"{fixture.get('manufacturer', '')} {fixture.get('model', '')} {fixture.get('name', '')}".lower()

    if channels == 3:
        return {"red": 0, "green": 1, "blue": 2, "brightness": [0, 1, 2]}

    if channels >= 7:
        roles = {
            "dimmer": 0,
            "red": 1,
            "green": 2,
            "blue": 3,
            "brightness": [0],
        }
        if "pro h" in name or "amber" in name or channels == 7:
            roles["amber"] = 5
        if "pro w" in name or "white" in name or channels >= 9:
            roles["white"] = 5
            roles["warm"] = 5
        return roles

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
    "red": {"red": 255, "green": 0, "blue": 0},
    "green": {"red": 0, "green": 255, "blue": 0},
    "blue": {"red": 0, "green": 0, "blue": 255},
    "purple": {"red": 200, "green": 0, "blue": 255},
    "magenta": {"red": 255, "green": 0, "blue": 255},
    "cyan": {"red": 0, "green": 255, "blue": 255},
    "white": {"red": 220, "green": 220, "blue": 220, "white": 120, "amber": 0},
    "cool": {"red": 180, "green": 220, "blue": 255, "white": 180, "amber": 0},
    "warm": {"red": 255, "green": 170, "blue": 80, "white": 80, "amber": 180},
    "amber": {"red": 255, "green": 120, "blue": 0, "amber": 180},
}


def apply_color_live(color, intensity=None, target_groups=None):
    color_key = str(color or "white").strip().lower()
    preset = COLOR_PRESETS.get(color_key, COLOR_PRESETS["white"])
    relative = str(intensity or "").strip().startswith(("+", "-"))
    fixtures = _target_fixtures(target_groups)
    current = get_current_channel_values()
    updates = []

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

    success = set_channel_values(updates)
    return {
        "success": success,
        "output": f"Applied {color_key} to {len(updates)} channels live via WebSocket",
        "error": "" if success else f"Failed to apply {color_key} via WebSocket",
    }


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

    for step in range(1, steps + 1):
        ratio = step / steps
        updates = [
            (channel, round(start + (target_value - start) * ratio))
            for channel, start in channels
        ]
        set_channel_values(updates)
        if step < steps and seconds > 0:
            time.sleep(seconds / steps)

    return {
        "success": True,
        "output": f"Faded {len(channels)} brightness channels to {target_value} over {seconds:g}s",
        "error": "",
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
        try:
            apply_result = apply_scene_xml_live(scene_file.read_text())
        finally:
            scene_file.unlink(missing_ok=True)
        result["success"] = apply_result["success"]
        result["output"] = (result.get("output", "") + "\n" + apply_result["output"]).strip()
        result["error"] = apply_result["error"] if not apply_result["success"] else result.get("error", "")
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
        "debug": {
            "interpret_ms": interpret_ms,
            "execute_ms": execute_ms,
            "total_ms": interpret_ms + execute_ms,
            "provider": AI_PROVIDER,
            "model": AI_MODEL,
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

    # 2. QLC+ WebSocket port. Do a TCP reachability probe instead of holding a
    # status WebSocket open; QLC+ 4.14.1 is sensitive to excess WS clients.
    ws_ok = False
    ws_detail = "unreachable"
    try:
        with socket.create_connection((QLC_HOST, QLC_PORT), timeout=1):
            ws_ok = True
            ws_detail = f"reachable at {QLC_WS_URL}"
    except Exception as e:
        ws_detail = str(e)
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
    """List all fixtures from workspace"""
    try:
        return jsonify({"fixtures": get_workspace_fixtures()})
    except Exception as e:
        return jsonify({"error": str(e), "fixtures": []}), 500


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


if __name__ == "__main__":
    # Check if lightsctl exists
    if not LIGHTSCTL.exists():
        print(f"Error: lightsctl.sh not found at {LIGHTSCTL}")
        sys.exit(1)
    
    # Connect to QLC+ on startup
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(connect_to_qlc())
    except Exception as e:
        print(f"Warning: Could not connect to QLC+ on startup: {e}")
    finally:
        try:
            loop.close()
        except:
            pass
    
    # Run server with SocketIO
    port = int(os.getenv("CONTROL_PORT", "5000"))
    socketio.run(app, host="0.0.0.0", port=port, debug=True, allow_unsafe_werkzeug=True)
