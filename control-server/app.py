#!/usr/bin/env python3
"""
Natural Language Lighting Control Server
Interprets natural language commands and adjusts QLC+ workspace in real-time
Also provides direct fixture/group controls with QLC+ WebSocket integration
"""

import os
import sys
import json
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
AI_PROVIDER = os.getenv("AI_PROVIDER", "anthropic")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "claude-3-5-sonnet-20241022")

# Global QLC+ WebSocket connection
qlc_websocket = None


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
        return response.json()["choices"][0]["message"]["content"]
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


def execute_lighting_action(action_data):
    """Execute the interpreted lighting action"""
    action = action_data.get("action")
    params = action_data.get("parameters", {})
    
    if action == "apply_template":
        template = params.get("template")
        cmd = f"{LIGHTSCTL} generate-from-template {template} --add-to-workspace"
        result = execute_command(cmd)
        return result
    
    elif action == "generate_scene":
        description = params.get("description")
        cmd = f"{LIGHTSCTL} generate-scene '{description}' --add-to-workspace"
        result = execute_command(cmd)
        return result
    
    elif action == "adjust_brightness":
        # This would require modifying current workspace
        # For now, generate a scene with adjusted brightness
        value = params.get("value", "+50")
        cmd = f"{LIGHTSCTL} generate-scene 'adjust brightness by {value}' --add-to-workspace"
        result = execute_command(cmd)
        return result
    
    elif action == "adjust_color":
        color = params.get("color", "white")
        intensity = params.get("intensity", "200")
        cmd = f"{LIGHTSCTL} generate-scene 'add more {color} color at intensity {intensity}' --add-to-workspace"
        result = execute_command(cmd)
        return result
    
    elif action == "fade":
        duration = params.get("duration", "3")
        target = params.get("target", "0")
        cmd = f"{LIGHTSCTL} generate-scene 'fade to brightness {target} over {duration} seconds' --add-to-workspace"
        result = execute_command(cmd)
        return result
    
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
    data = request.json
    user_input = data.get("command", "").strip()
    
    if not user_input:
        return jsonify({
            "success": False,
            "error": "No command provided"
        }), 400
    
    # Interpret command using AI
    action_data = interpret_command(user_input)
    
    if action_data.get("action") == "error":
        return jsonify({
            "success": False,
            "error": action_data.get("explanation"),
            "action": action_data
        }), 400
    
    # Execute the action
    result = execute_lighting_action(action_data)
    
    return jsonify({
        "success": result["success"],
        "action": action_data,
        "output": result.get("output", ""),
        "error": result.get("error", "")
    })


@app.route("/api/status", methods=["GET"])
def get_status():
    """Get current lighting status"""
    # Check if QLC+ is running
    result = execute_command(f"{LIGHTSCTL} health")
    
    return jsonify({
        "qlc_running": "running" in result.get("output", "").lower(),
        "workspace": str(WORKSPACE_PATH),
        "workspace_exists": WORKSPACE_PATH.exists(),
        "ai_provider": AI_PROVIDER,
        "ai_model": AI_MODEL
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
        
        # Use lightsctl to apply template to group
        cmd = f"{LIGHTSCTL} group-template '{group_name}' {template} --add-to-workspace"
        result = execute_command(cmd)
        
        return jsonify({
            "success": result["success"],
            "output": result.get("output", ""),
            "error": result.get("error", "")
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
