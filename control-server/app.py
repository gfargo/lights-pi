#!/usr/bin/env python3
"""
Natural Language Lighting Control Server
Interprets natural language commands and adjusts QLC+ workspace in real-time
"""

import os
import sys
import json
import subprocess
from pathlib import Path
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

app = Flask(__name__)
CORS(app)

# Configuration
SCRIPT_DIR = Path(__file__).parent.parent
LIGHTSCTL = SCRIPT_DIR / "lightsctl.sh"
WORKSPACE_PATH = Path.home() / ".qlcplus" / "default.qxw"

# AI Configuration from environment
AI_PROVIDER = os.getenv("AI_PROVIDER", "anthropic")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "claude-3-5-sonnet-20241022")


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


if __name__ == "__main__":
    # Check if lightsctl exists
    if not LIGHTSCTL.exists():
        print(f"Error: lightsctl.sh not found at {LIGHTSCTL}")
        sys.exit(1)
    
    # Run server
    port = int(os.getenv("CONTROL_PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
