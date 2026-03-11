# Natural Language Lighting Control

Control your QLC+ lighting setup using natural language commands via a web interface.

## Features

- 🎤 Voice and text input
- 🤖 AI-powered command interpretation
- ⚡ Real-time lighting adjustments
- 📱 Works on any device (phone, tablet, computer)
- 🎨 Beautiful, responsive UI
- 📜 Command history

## Installation

### On the Raspberry Pi

```bash
# Install the control server
./lightsctl.sh control-install

# Check status
./lightsctl.sh control-status

# View logs
./lightsctl.sh control-logs
```

The server will be available at:
- `http://lights.local:5000`
- `http://<pi-ip-address>:5000`

### Local Development

```bash
cd control-server

# Install dependencies
pip3 install -r requirements.txt

# Set environment variables
export AI_PROVIDER=anthropic
export AI_API_KEY=your-key-here
export AI_MODEL=claude-3-5-sonnet-20241022

# Run server
python3 app.py
```

## Usage

### Web Interface

1. Open `http://lights.local:5000` in your browser
2. Type or speak a command
3. Watch your lights respond in real-time

### Example Commands

**Brightness:**
- "Make it brighter"
- "Dim the lights"
- "Set brightness to 50%"

**Color:**
- "Make it warmer"
- "Add more blue"
- "Cool white"
- "Warm sunset"

**Templates:**
- "Party mode"
- "YouTube studio"
- "Ambient lighting"
- "Work light"

**Effects:**
- "Fade to black over 5 seconds"
- "Strobe effect"
- "Slow pulse"

**Scenes:**
- "Warm sunset ambiance"
- "Dramatic spotlight"
- "Cozy evening"

## How It Works

1. **Input**: User types or speaks a command
2. **Interpretation**: AI converts natural language to structured action
3. **Execution**: Server calls `lightsctl.sh` to modify workspace
4. **Deployment**: Changes are deployed to QLC+ in real-time

## API Endpoints

### POST /api/command
Execute a natural language command

```json
{
  "command": "make it brighter"
}
```

Response:
```json
{
  "success": true,
  "action": {
    "action": "adjust_brightness",
    "parameters": {"value": "+50"},
    "explanation": "Increasing brightness by 50"
  },
  "output": "...",
  "error": ""
}
```

### GET /api/status
Get current system status

Response:
```json
{
  "qlc_running": true,
  "workspace": "/home/pi/.qlcplus/default.qxw",
  "ai_provider": "anthropic",
  "ai_model": "claude-3-5-sonnet-20241022"
}
```

### GET /api/templates
List available templates

Response:
```json
{
  "templates": [
    {
      "name": "youtube-studio",
      "description": "Bright neutral white for video recording"
    },
    ...
  ]
}
```

## Configuration

Edit `.env` in the project root:

```bash
# AI Provider
AI_PROVIDER=anthropic          # anthropic, openai, or ollama
AI_API_KEY=your-key-here       # Not needed for ollama
AI_MODEL=claude-3-5-sonnet-20241022

# Server Port
CONTROL_PORT=5000
```

## Troubleshooting

**Server won't start:**
```bash
# Check logs
./lightsctl.sh control-logs

# Restart service
./lightsctl.sh control-restart
```

**AI not responding:**
- Check AI_PROVIDER and AI_API_KEY in `.env`
- For Ollama: ensure `ollama serve` is running
- Check logs for API errors

**Commands not working:**
- Verify QLC+ is running: `./lightsctl.sh health`
- Check workspace exists: `./lightsctl.sh pull-workspace`
- Test basic commands first

## Development

### Adding New Actions

Edit `app.py` and add to `execute_lighting_action()`:

```python
elif action == "your_action":
    param = params.get("param")
    cmd = f"{LIGHTSCTL} your-command {param}"
    result = execute_command(cmd)
    return result
```

### Customizing UI

Edit `templates/index.html` to modify the interface.

## Security

- Server runs on local network only
- No external access by default
- Uses existing QLC+ security model
- API keys stored in `.env` (not committed to git)

## Performance

- Response time: 1-3 seconds (depends on AI provider)
- Ollama (local): Fastest, no API costs
- Anthropic/OpenAI: Slower, requires internet

## License

MIT License - see main project LICENSE file
