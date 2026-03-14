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
    
    # QLC+ Simple Desk command format: CH|<absolute_address>|<value>
    command = f"CH|{absolute_address}|{value}"
    
    # Send via asyncio - create new event loop for each call
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        success = loop.run_until_complete(send_qlc_command(command))
        return success
    except Exception as e:
        print(f"Error in set_channel_value: {e}")
        return False
    finally:
        try:
            loop.close()
        except:
            pass


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
Output: {"action": "fade", "parameters": {"duration": "5", "target": "0"}, "explanation": "Fading to black over 5 seconds"}"""

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

    When running locally on the Pi (IS_LOCAL=True) we generate scenes using
    the local workspace file and inject + restart directly, avoiding SSH
    round-trips that would fail without a key.

    If target_groups is a non-empty list, the action is applied to each named
    group in sequence using group-scene / group-template instead of the
    global generate-scene command.
    """
    action = action_data.get("action")
    params = action_data.get("parameters", {})

    def _build_scene_cmd(description, group_name=None):
        safe_desc = description.replace("'", "'\\''")
        if group_name:
            safe_group = group_name.replace("'", "'\\''")
            if IS_LOCAL and WORKSPACE_PATH.exists():
                return (
                    f"{LIGHTSCTL} group-scene '{safe_group}' '{safe_desc}'"
                    f" --workspace '{WORKSPACE_PATH}'"
                    f" --output /tmp/_qlc_scene_latest.xml"
                )
            else:
                return f"{LIGHTSCTL} group-scene '{safe_group}' '{safe_desc}' --add-to-workspace"
        else:
            if IS_LOCAL and WORKSPACE_PATH.exists():
                return (
                    f"{LIGHTSCTL} generate-scene '{safe_desc}'"
                    f" --workspace '{WORKSPACE_PATH}'"
                    f" --output /tmp/_qlc_scene_latest.xml"
                )
            else:
                return f"{LIGHTSCTL} generate-scene '{safe_desc}' --add-to-workspace"

    def _inject_and_restart(result):
        """After a local scene generation, inject the XML into the workspace,
        restart the QLC+ service, then activate the scene via WebSocket."""
        if not IS_LOCAL or not result["success"]:
            return result

        scene_file = Path("/tmp/_qlc_scene_latest.xml")
        if not scene_file.exists():
            result["error"] = (result.get("error", "") +
                               "\nScene file not created").strip()
            result["success"] = False
            return result

        scene_xml = scene_file.read_text()

        # Inject via the Python helper
        inject_script = SCRIPT_DIR / "scripts" / "lib" / "workspace_inject.py"
        if not inject_script.exists():
            result["error"] = "workspace_inject.py not found"
            result["success"] = False
            return result

        # Calculate next function ID
        try:
            tree = ET.parse(str(WORKSPACE_PATH))
            root = tree.getroot()
            ns = "http://www.qlcplus.org/Workspace"
            max_id = 0
            for func in root.iter(f"{{{ns}}}Function"):
                fid = func.get("ID")
                if fid and fid.isdigit():
                    max_id = max(max_id, int(fid))
            next_id = max_id + 1
        except Exception:
            next_id = 100

        inject_result = execute_command(
            f"python3 '{inject_script}' '{WORKSPACE_PATH}' "
            f"'{scene_xml}' '{WORKSPACE_PATH}' {next_id}"
        )
        if not inject_result["success"]:
            result["error"] = (result.get("error", "") +
                               f"\nInject failed: {inject_result.get('error', '')}").strip()
            result["success"] = False
            return result

        # Restart QLC+ so it picks up the modified workspace
        restart = execute_command(f"sudo systemctl restart {SERVICE_NAME}")
        if not restart["success"]:
            result["error"] = (result.get("error", "") +
                               f"\nRestart warning: {restart.get('error', '')}").strip()

        result["output"] = (result.get("output", "") +
                            f"\nScene injected (ID {next_id}) and service restarted").strip()
        scene_file.unlink(missing_ok=True)

        # Activate the scene via WebSocket after QLC+ comes back up
        _activate_scene_ws(next_id, result)
        return result

    def _activate_scene_ws(scene_id, result, retries=6, delay=2.0):
        """Wait for QLC+ to restart then activate the scene via WebSocket API."""
        import time as _t

        async def _do_activate():
            for attempt in range(retries):
                try:
                    async with websockets.connect(
                        QLC_WS_URL, open_timeout=3, close_timeout=2
                    ) as ws:
                        # Start the scene function
                        await ws.send(f"QLC+API|setFunctionStatus|{scene_id}|1")
                        # Brief wait to confirm it was received
                        try:
                            await asyncio.wait_for(ws.recv(), timeout=0.5)
                        except asyncio.TimeoutError:
                            pass
                        return True
                except Exception:
                    if attempt < retries - 1:
                        _t.sleep(delay)
            return False

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            activated = loop.run_until_complete(_do_activate())
            loop.close()
            if activated:
                result["output"] = (result.get("output", "") +
                                    f"\nScene {scene_id} activated").strip()
            else:
                result["output"] = (result.get("output", "") +
                                    f"\nScene {scene_id} injected (activation timed out — QLC+ may still be starting)").strip()
        except Exception as e:
            result["output"] = (result.get("output", "") +
                                f"\nActivation warning: {e}").strip()

    if action == "apply_template":
        template = params.get("template")
        groups = target_groups if target_groups else []
        if groups:
            # Apply template to each selected group
            combined_output = ""
            for gname in groups:
                safe_name = gname.replace("'", "'\\''")
                if IS_LOCAL and WORKSPACE_PATH.exists():
                    cmd = (f"{LIGHTSCTL} group-template '{safe_name}' {template}"
                           f" --workspace '{WORKSPACE_PATH}'"
                           f" --output /tmp/_qlc_scene_latest.xml")
                else:
                    cmd = f"{LIGHTSCTL} group-template '{safe_name}' {template} --add-to-workspace"
                result = execute_command(cmd)
                if IS_LOCAL and WORKSPACE_PATH.exists() and result["success"]:
                    result = _inject_and_restart(result)
                combined_output += result.get("output", "") + "\n"
                if not result["success"]:
                    return result
            return {"success": True, "output": combined_output.strip(), "error": ""}
        elif IS_LOCAL and WORKSPACE_PATH.exists():
            cmd = (f"{LIGHTSCTL} generate-from-template {template}"
                   f" --workspace '{WORKSPACE_PATH}'"
                   f" --output /tmp/_qlc_scene_latest.xml")
            result = execute_command(cmd)
            return _inject_and_restart(result)
        else:
            cmd = f"{LIGHTSCTL} generate-from-template {template} --add-to-workspace"
            return execute_command(cmd)

    elif action == "generate_scene":
        description = params.get("description", "")
        groups = target_groups if target_groups else []
        if groups:
            combined_output = ""
            for gname in groups:
                cmd = _build_scene_cmd(description, group_name=gname)
                result = execute_command(cmd)
                result = _inject_and_restart(result)
                combined_output += result.get("output", "") + "\n"
                if not result["success"]:
                    return result
            return {"success": True, "output": combined_output.strip(), "error": ""}
        cmd = _build_scene_cmd(description)
        result = execute_command(cmd)
        return _inject_and_restart(result)

    elif action == "adjust_brightness":
        value = params.get("value", "+50")
        description = f"adjust brightness by {value}"
        groups = target_groups if target_groups else []
        if groups:
            combined_output = ""
            for gname in groups:
                cmd = _build_scene_cmd(description, group_name=gname)
                result = execute_command(cmd)
                result = _inject_and_restart(result)
                combined_output += result.get("output", "") + "\n"
                if not result["success"]:
                    return result
            return {"success": True, "output": combined_output.strip(), "error": ""}
        cmd = _build_scene_cmd(description)
        result = execute_command(cmd)
        return _inject_and_restart(result)

    elif action == "adjust_color":
        color = params.get("color", "white")
        intensity = params.get("intensity", "200")
        description = f"add more {color} color at intensity {intensity}"
        groups = target_groups if target_groups else []
        if groups:
            combined_output = ""
            for gname in groups:
                cmd = _build_scene_cmd(description, group_name=gname)
                result = execute_command(cmd)
                result = _inject_and_restart(result)
                combined_output += result.get("output", "") + "\n"
                if not result["success"]:
                    return result
            return {"success": True, "output": combined_output.strip(), "error": ""}
        cmd = _build_scene_cmd(description)
        result = execute_command(cmd)
        return _inject_and_restart(result)

    elif action == "fade":
        duration = params.get("duration", "3")
        target = params.get("target", "0")
        description = f"fade to brightness {target} over {duration} seconds"
        groups = target_groups if target_groups else []
        if groups:
            combined_output = ""
            for gname in groups:
                cmd = _build_scene_cmd(description, group_name=gname)
                result = execute_command(cmd)
                result = _inject_and_restart(result)
                combined_output += result.get("output", "") + "\n"
                if not result["success"]:
                    return result
            return {"success": True, "output": combined_output.strip(), "error": ""}
        cmd = _build_scene_cmd(description)
        result = execute_command(cmd)
        return _inject_and_restart(result)

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

    # 2. QLC+ WebSocket
    ws_ok = qlc_websocket is not None and not getattr(qlc_websocket, "closed", True)
    services["qlc_ws"] = {
        "name": "QLC+ WebSocket",
        "ok": ws_ok,
        "detail": f"connected to {QLC_WS_URL}" if ws_ok else "disconnected",
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
        if IS_LOCAL and WORKSPACE_PATH.exists():
            cmd = (f"{LIGHTSCTL} group-template '{safe_name}' {template}"
                   f" --workspace '{WORKSPACE_PATH}'"
                   f" --output /tmp/_qlc_scene_latest.xml")
        else:
            cmd = f"{LIGHTSCTL} group-template '{safe_name}' {template} --add-to-workspace"
        
        result = execute_command(cmd)
        
        # If local, inject and restart (mirrors _inject_and_restart in handle_command)
        if IS_LOCAL and WORKSPACE_PATH.exists() and result["success"]:
            scene_file = Path("/tmp/_qlc_scene_latest.xml")
            if scene_file.exists():
                inject_script = SCRIPT_DIR / "scripts" / "lib" / "workspace_inject.py"
                scene_xml = scene_file.read_text()
                try:
                    tree = ET.parse(str(WORKSPACE_PATH))
                    root = tree.getroot()
                    ns = "http://www.qlcplus.org/Workspace"
                    max_id = 0
                    for func in root.iter(f"{{{ns}}}Function"):
                        fid = func.get("ID")
                        if fid and fid.isdigit():
                            max_id = max(max_id, int(fid))
                    next_id = max_id + 1
                except Exception:
                    next_id = 100
                inject_result = execute_command(
                    f"python3 '{inject_script}' '{WORKSPACE_PATH}' "
                    f"'{scene_xml}' '{WORKSPACE_PATH}' {next_id}"
                )
                execute_command(f"sudo systemctl restart {SERVICE_NAME}")
                scene_file.unlink(missing_ok=True)
                result["output"] = (result.get("output", "") +
                                    f"\nScene injected (ID {next_id}) and service restarted").strip()
        
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
        if not WORKSPACE_PATH.exists():
            return jsonify({"fixtures": []})
        
        tree = ET.parse(WORKSPACE_PATH)
        root = tree.getroot()
        
        # Handle QLC+ namespace
        ns = {'qlc': 'http://www.qlcplus.org/Workspace'}
        
        fixtures = []
        for fixture in root.findall(".//qlc:Fixture", ns):
            fixture_id = fixture.find("qlc:ID", ns)
            fixture_name = fixture.find("qlc:Name", ns)
            fixture_universe = fixture.find("qlc:Universe", ns)
            fixture_address = fixture.find("qlc:Address", ns)
            fixture_channels = fixture.find("qlc:Channels", ns)
            
            if fixture_id is not None and fixture_name is not None:
                fixtures.append({
                    "id": int(fixture_id.text),
                    "name": fixture_name.text,
                    "universe": int(fixture_universe.text) if fixture_universe is not None else 0,
                    "address": int(fixture_address.text) if fixture_address is not None else 0,
                    "channels": int(fixture_channels.text) if fixture_channels is not None else 1
                })
        
        return jsonify({"fixtures": fixtures})
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
    import asyncio as _asyncio
    import websockets as _ws

    # Determine how many channels we need (highest fixture end address)
    max_ch = 32
    try:
        if WORKSPACE_PATH.exists():
            tree = ET.parse(str(WORKSPACE_PATH))
            root = tree.getroot()
            ns = "http://www.qlcplus.org/Workspace"
            for f in root.iter(f"{{{ns}}}Fixture"):
                addr_el = f.find(f"{{{ns}}}Address")
                chs_el = f.find(f"{{{ns}}}Channels")
                if addr_el is not None and chs_el is not None:
                    top = int(addr_el.text) + int(chs_el.text)
                    max_ch = max(max_ch, top)
    except Exception:
        pass

    async def _fetch():
        values = {}
        try:
            async with _ws.connect(QLC_WS_URL, open_timeout=4, close_timeout=2) as ws:
                # Universe is 1-based in QLC+ WS API; start at ch 1
                cmd = f"QLC+API|getChannelsValues|1|1|{max_ch}"
                await ws.send(cmd)
                import time as _t
                deadline = _t.time() + 2.0
                while _t.time() < deadline:
                    try:
                        msg = await _asyncio.wait_for(ws.recv(), timeout=0.5)
                        if "getChannelsValues" in msg:
                            # Format: QLC+API|getChannelsValues|<uni>|<ch>|<val>|<pct.color>|<ch>|...
                            parts = msg.split("|")
                            # parts[0]=QLC+API, parts[1]=getChannelsValues, parts[2]=universe
                            # then repeating groups of 3: ch, value, pct.color
                            i = 3
                            while i + 2 <= len(parts):
                                try:
                                    ch = int(parts[i])
                                    val = int(parts[i + 1])
                                    values[ch] = val
                                except (ValueError, IndexError):
                                    pass
                                i += 3
                            break
                    except _asyncio.TimeoutError:
                        break
        except Exception as e:
            print(f"channel_values fetch error: {e}")
        return values

    try:
        loop = _asyncio.new_event_loop()
        _asyncio.set_event_loop(loop)
        values = loop.run_until_complete(_fetch())
        loop.close()
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
