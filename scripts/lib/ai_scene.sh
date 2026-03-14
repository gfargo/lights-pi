#!/usr/bin/env bash
# AI Scene Generation Library
# Generates QLC+ scenes using AI based on natural language descriptions

set -euo pipefail

# Load environment variables
if [[ -f "${ENV_FILE:-}" ]]; then
  source "${ENV_FILE}"
fi

# --- jq-free helpers (Python fallback) ---
# JSON-encode a string for safe embedding in JSON payloads
# Replaces: jq -Rs .
_json_encode() {
  python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))"
}

# Extract a value from JSON by path expression
# Usage: echo '{"a":"b"}' | _json_extract '.key' [default]
# Replaces: jq -r '.path'
_json_extract() {
  local path="$1"
  local default="${2:-}"
  python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    keys = [k for k in '''${path}'''.replace(']','').replace('[','.').split('.') if k]
    val = data
    for k in keys:
        if isinstance(val, list):
            val = val[int(k)]
        else:
            val = val[k]
    if val is None:
        print('${default}')
    else:
        print(val if isinstance(val, str) else json.dumps(val))
except Exception:
    print('${default}')
"
}

# Check if a JSON path exists / matches (for jq -e style checks)
_json_check() {
  local expr="$1"
  python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    ${expr}
except Exception:
    sys.exit(1)
"
}

# AI Configuration
AI_PROVIDER="${AI_PROVIDER:-anthropic}"
AI_API_KEY="${AI_API_KEY:-}"
AI_MODEL="${AI_MODEL:-claude-3-5-sonnet-20241022}"
AI_SCENE_STYLE="${AI_SCENE_STYLE:-complete}"
AI_SCENE_VARIATIONS="${AI_SCENE_VARIATIONS:-1}"

# Validate AI configuration
function ai_validate_config() {
  # Check provider
  case "$AI_PROVIDER" in
    anthropic|openai|ollama) ;;
    *)
      echo "Error: Invalid AI_PROVIDER: $AI_PROVIDER" >&2
      echo "Valid options: anthropic, openai, ollama" >&2
      return 1
      ;;
  esac
  
  # API key only required for anthropic and openai
  if [[ "$AI_PROVIDER" != "ollama" ]] && [[ -z "$AI_API_KEY" ]]; then
    echo "Error: AI_API_KEY not set in .env file" >&2
    echo "Add: AI_API_KEY=sk-ant-... (for $AI_PROVIDER)" >&2
    return 1
  fi
  
  # Check if Ollama is running
  if [[ "$AI_PROVIDER" == "ollama" ]]; then
    if ! curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
      echo "Error: Ollama is not running" >&2
      echo "Start Ollama with: ollama serve" >&2
      return 1
    fi
    
    # Check if model is available
    if ! curl -s http://localhost:11434/api/tags | _json_check "
models = data.get('models', [])
if not any(m['name'].startswith('$AI_MODEL') for m in models):
    sys.exit(1)
" 2>/dev/null; then
      echo "Warning: Model '$AI_MODEL' may not be available in Ollama" >&2
      echo "Available models:" >&2
      curl -s http://localhost:11434/api/tags | python3 -c "import sys,json; [print(m['name']) for m in json.load(sys.stdin).get('models',[])]" >&2
      echo "" >&2
      echo "Pull model with: ollama pull $AI_MODEL" >&2
    fi
  fi
  
  return 0
}

# Extract fixture inventory from QLC+ workspace XML
# Extract fixture inventory from QLC+ workspace XML (Python-based, no xmllint needed)
function ai_extract_fixtures() {
  local workspace_file="$1"
  
  if [[ ! -f "$workspace_file" ]]; then
    echo "Error: Workspace file not found: $workspace_file" >&2
    return 1
  fi
  
  python3 -c "
import sys, json
import xml.etree.ElementTree as ET

workspace = sys.argv[1]

def get_text(el, tag):
    child = el.find(tag)
    return child.text.strip() if child is not None and child.text else ''

def determine_capabilities(model, channels):
    ch = int(channels) if channels else 0
    if any(x in model for x in ['Moving', 'Spot', 'Wash']):
        return ['rgb', 'dimmer', 'pan_tilt', 'gobo', 'color_wheel']
    if 'Pro H' in model or ch >= 5:
        return ['rgb', 'amber', 'white', 'dimmer']
    if 'RGB' in model or 'Par' in model or ch >= 3:
        return ['rgb', 'dimmer']
    return ['dimmer']

def build_channel_map(model, mode):
    if model == 'SlimPAR 56' and mode == '3-Ch':
        return {'1': 'Red', '2': 'Green', '3': 'Blue'}
    if model == 'SlimPAR Pro H USB' and mode == '7 Channel':
        return {'1': 'Dimmer', '2': 'Red', '3': 'Green', '4': 'Blue',
                '5': 'Color Macros', '6': 'Amber', '7': 'Strobe'}
    return {}

try:
    tree = ET.parse(workspace)
    root = tree.getroot()
    ns_prefix = ''
    if root.tag.startswith('{'):
        ns_prefix = root.tag[:root.tag.index('}')+1]
    
    fixtures = []
    for fix in root.iter(ns_prefix + 'Fixture'):
        fid_el = fix.find(ns_prefix + 'ID')
        if fid_el is None:
            continue
        fid      = int(fid_el.text.strip())
        mfr      = get_text(fix, ns_prefix + 'Manufacturer')
        model    = get_text(fix, ns_prefix + 'Model')
        mode     = get_text(fix, ns_prefix + 'Mode')
        name     = get_text(fix, ns_prefix + 'Name')
        universe = int(get_text(fix, ns_prefix + 'Universe') or '0')
        address  = int(get_text(fix, ns_prefix + 'Address') or '0')
        channels = int(get_text(fix, ns_prefix + 'Channels') or '0')
        fixtures.append({
            'id': fid, 'name': name, 'manufacturer': mfr,
            'model': model, 'mode': mode, 'universe': universe,
            'address': address, 'channels': channels,
            'capabilities': determine_capabilities(model, channels),
            'channel_map': build_channel_map(model, mode)
        })
    print(json.dumps({'fixtures': fixtures}))
except Exception as e:
    import sys as _sys
    _sys.stderr.write('Error parsing workspace: ' + str(e) + '\n')
    print(json.dumps({'fixtures': []}))
" "$workspace_file"
}

function ai_determine_capabilities() {
  local manufacturer="$1"
  local model="$2"
  local mode="$3"
  local channels="$4"
  
  local caps='["dimmer"]'
  
  # Check for RGB/RGBW based on model and channel count
  if [[ "$model" =~ "RGB" ]] || [[ "$model" =~ "Par" ]] || [[ "$channels" -ge 3 ]]; then
    caps='["rgb","dimmer"]'
  fi
  
  # Check for RGBWA (Amber/White)
  if [[ "$model" =~ "Pro H" ]] || [[ "$channels" -ge 5 ]]; then
    caps='["rgb","amber","white","dimmer"]'
  fi
  
  # Check for moving head capabilities
  if [[ "$model" =~ "Moving" ]] || [[ "$model" =~ "Spot" ]] || [[ "$model" =~ "Wash" ]]; then
    caps='["rgb","dimmer","pan_tilt","gobo","color_wheel"]'
  fi
  
  echo "$caps"
}

# Build channel map for fixture
function ai_build_channel_map() {
  local manufacturer="$1"
  local model="$2"
  local mode="$3"
  local channels="$4"
  
  # Default RGB Par mapping
  local channel_map='{}'
  
  # Chauvet SlimPAR 56 (3-Ch mode)
  if [[ "$model" == "SlimPAR 56" ]] && [[ "$mode" == "3-Ch" ]]; then
    channel_map='{"1":"Red","2":"Green","3":"Blue"}'
  fi
  
  # Chauvet SlimPAR Pro H USB (7 Channel mode)
  if [[ "$model" == "SlimPAR Pro H USB" ]] && [[ "$mode" == "7 Channel" ]]; then
    channel_map='{"1":"Dimmer","2":"Red","3":"Green","4":"Blue","5":"Color Macros","6":"Amber","7":"Strobe"}'
  fi
  
  echo "$channel_map"
}

# Build system prompt for AI
function ai_build_system_prompt() {
  local style="$1"
  
  cat <<'PROMPT'
You are a professional lighting designer with expertise in DMX control and QLC+.
Your task is to generate scene configurations based on user descriptions.

CRITICAL: You must output ONLY valid QLC+ scene XML. No explanations, no markdown, no extra text.

You will receive:
1. A natural language description of the desired scene
2. A complete fixture inventory with capabilities
3. A style profile (complete, modular, timeline, or reactive)

You must output valid QLC+ scene XML that:
- Uses ONLY the provided fixture IDs (do not invent fixture IDs)
- Sets appropriate DMX values (0-255)
- Considers fixture capabilities
- Matches the described mood/effect
- Follows the specified style profile

DMX Value Guidelines:
- Dimmer: 0=off, 255=full brightness
- RGB: 0-255 per channel
- Pan/Tilt: 0-255 (fixture-specific range)
- Color Wheel: Discrete positions (check fixture manual)
- Gobo: Discrete positions (check fixture manual)

Lighting Design Principles:
- Warm colors: High red, medium green, low blue
- Cool colors: Low red, medium green, high blue
- Sunset: Orange/red gradient, 60-80% intensity
- Concert: High intensity, saturated colors, movement
- Ambient: Low intensity, soft colors
- Dramatic: High contrast, focused beams

OUTPUT FORMAT:
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE Function>
<Function Type="Scene" Name="Scene Name">
  <Speed FadeIn="500" FadeOut="500" Duration="0"/>
  <FixtureVal ID="fixture_id">channel,value,channel,value,...</FixtureVal>
</Function>

IMPORTANT: Output ONLY the XML above. No explanations before or after.
PROMPT

  case "$style" in
    complete)
      cat <<'COMPLETE'

For COMPLETE style:
- Set all relevant channels for each fixture
- Create self-contained scenes
- Include colors, intensities, and positions
- Scene should be immediately playable
COMPLETE
      ;;
    modular)
      cat <<'MODULAR'

For MODULAR style:
- Create separate scenes for different aspects (color, intensity, position)
- Name scenes clearly (e.g., "Color: Warm Orange", "Intensity: Evening Glow")
- Allow for composition and reuse
- Each scene should control only one aspect
- Generate 2-3 complementary scenes that can be combined
MODULAR
      ;;
    timeline)
      cat <<'TIMELINE'

For TIMELINE style:
- Define keyframes at specific time points
- Include smooth transitions between states
- Specify duration and easing curves
- Create temporal progression (e.g., sunrise over 3 minutes)
- Use <Keyframe Time="milliseconds"> tags
TIMELINE
      ;;
    reactive)
      cat <<'REACTIVE'

For REACTIVE style:
- Define input mappings (audio, sensors, etc.)
- Create conditional logic for different states
- Map inputs to DMX parameters with curves
- Include threshold-based behaviors
- Use <Mapping> and <Condition> tags
REACTIVE
      ;;
  esac
}

# Build user prompt for AI
function ai_build_user_prompt() {
  local description="$1"
  local style="$2"
  local fixtures_json="$3"
  
  cat <<PROMPT
Generate a QLC+ scene for: "${description}"
Style: ${style}

Available Fixtures (USE THESE IDs ONLY):
${fixtures_json}

IMPORTANT INSTRUCTIONS:
1. Use ONLY the fixture IDs listed above
2. Output ONLY the XML - no explanations, no markdown code blocks
3. Start with <?xml version="1.0" encoding="UTF-8"?>
4. Use the exact FixtureVal format: <FixtureVal ID="id">channel,value,channel,value,...</FixtureVal>
5. Match the fixture channel counts and capabilities shown above

Generate the scene XML now:
PROMPT
}

# Clean AI response: strip markdown fences, extract XML
function ai_clean_response() {
  local text="$1"
  
  # Use Python for robust cleaning - handles all edge cases
  echo "$text" | python3 -c "
import sys, re

text = sys.stdin.read()

# Strip markdown code fences (e.g. \`\`\`xml ... \`\`\` or \`\`\` ... \`\`\`)
fence_match = re.search(r'\`\`\`(?:xml)?\s*\n?(.*?)\n?\`\`\`', text, re.DOTALL)
if fence_match:
    text = fence_match.group(1).strip()

# Extract XML: find from <?xml or <Function to last </Function>
xml_match = re.search(r'(<\?xml.*?</Function>|<Function.*?</Function>)', text, re.DOTALL)
if xml_match:
    text = xml_match.group(1).strip()

print(text)
"
}

# Call AI API
function ai_call_api() {
  local system_prompt="$1"
  local user_prompt="$2"
  
  case "$AI_PROVIDER" in
    anthropic)
      ai_call_anthropic "$system_prompt" "$user_prompt"
      ;;
    openai)
      ai_call_openai "$system_prompt" "$user_prompt"
      ;;
    ollama)
      ai_call_ollama "$system_prompt" "$user_prompt"
      ;;
  esac
}

# Call Anthropic Claude API
function ai_call_anthropic() {
  local system_prompt="$1"
  local user_prompt="$2"
  
  local response
  response=$(curl -s https://api.anthropic.com/v1/messages \
    -H "Content-Type: application/json" \
    -H "x-api-key: ${AI_API_KEY}" \
    -H "anthropic-version: 2023-06-01" \
    -d @- <<JSON
{
  "model": "${AI_MODEL}",
  "max_tokens": 4096,
  "system": $(echo "$system_prompt" | _json_encode),
  "messages": [
    {
      "role": "user",
      "content": $(echo "$user_prompt" | _json_encode)
    }
  ]
}
JSON
)
  
  # Check for API errors
  local error_type
  error_type=$(echo "$response" | _json_extract 'error.type')
  if [[ -n "$error_type" ]]; then
    local error_msg
    error_msg=$(echo "$response" | _json_extract 'error.message' 'Unknown error')
    echo "Error: Anthropic API ($error_type): $error_msg" >&2
    return 1
  fi
  
  local generated_text
  generated_text=$(echo "$response" | _json_extract 'content.0.text')
  
  if [[ -z "$generated_text" || "$generated_text" == "null" ]]; then
    echo "Error: Empty response from Anthropic" >&2
    return 1
  fi
  
  # Debug: Save raw response
  if [[ "${DEBUG:-}" == "true" ]]; then
    echo "=== RAW ANTHROPIC RESPONSE ===" >&2
    echo "$generated_text" >&2
    echo "=== END RAW RESPONSE ===" >&2
  fi
  
  # Clean response: strip markdown code blocks if present
  generated_text=$(ai_clean_response "$generated_text")
  
  echo "$generated_text"
}

# Call OpenAI API
function ai_call_openai() {
  local system_prompt="$1"
  local user_prompt="$2"
  
  local response
  response=$(curl -s https://api.openai.com/v1/chat/completions \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${AI_API_KEY}" \
    -d @- <<JSON
{
  "model": "${AI_MODEL}",
  "messages": [
    {
      "role": "system",
      "content": $(echo "$system_prompt" | _json_encode)
    },
    {
      "role": "user",
      "content": $(echo "$user_prompt" | _json_encode)
    }
  ]
}
JSON
)
  
  # Check for API errors
  local error_msg
  error_msg=$(echo "$response" | _json_extract 'error.message')
  if [[ -n "$error_msg" ]]; then
    echo "Error: OpenAI API: $error_msg" >&2
    return 1
  fi
  
  local generated_text
  generated_text=$(echo "$response" | _json_extract 'choices.0.message.content')
  
  if [[ -z "$generated_text" || "$generated_text" == "null" ]]; then
    echo "Error: Empty response from OpenAI" >&2
    return 1
  fi
  
  # Always log raw response for debugging
  echo "$generated_text" > /tmp/qlc_ai_raw_response.txt 2>/dev/null || true
  
  # Debug: Save raw response
  if [[ "${DEBUG:-}" == "true" ]]; then
    echo "=== RAW OPENAI RESPONSE ===" >&2
    echo "$generated_text" >&2
    echo "=== END RAW RESPONSE ===" >&2
  fi
  
  # Clean response: strip markdown code blocks if present
  generated_text=$(ai_clean_response "$generated_text")
  
  # Log cleaned response
  echo "$generated_text" > /tmp/qlc_ai_cleaned_response.txt 2>/dev/null || true
  
  if [[ -z "$generated_text" ]]; then
    echo "Error: Response was empty after cleaning (raw response saved to /tmp/qlc_ai_raw_response.txt)" >&2
    return 1
  fi
  
  echo "$generated_text"
}

# Call Ollama (local LLM)
function ai_call_ollama() {
  local system_prompt="$1"
  local user_prompt="$2"
  
  local combined_prompt="${system_prompt}\n\n${user_prompt}"
  
  local response
  response=$(curl -s http://localhost:11434/api/generate \
    -d @- <<JSON
{
  "model": "${AI_MODEL}",
  "prompt": $(echo "$combined_prompt" | _json_encode),
  "stream": false
}
JSON
)
  
  local generated_text
  generated_text=$(echo "$response" | _json_extract 'response')
  
  # Debug: Save raw response
  if [[ "${DEBUG:-}" == "true" ]]; then
    echo "=== RAW OLLAMA RESPONSE ===" >&2
    echo "$generated_text" >&2
    echo "=== END RAW RESPONSE ===" >&2
  fi
  
  # Clean response: strip markdown code blocks, extract XML
  generated_text=$(ai_clean_response "$generated_text")
  
  echo "$generated_text"
}

# Validate generated XML
function ai_validate_xml() {
  local xml_content="$1"
  local workspace_file="$2"
  
  local validate_content="$xml_content"
  
  # For modular style with multiple Function elements, wrap in a root
  local func_count
  func_count=$(echo "$xml_content" | grep -c '<Function ' || true)
  
  if [[ "$func_count" -gt 1 ]]; then
    validate_content="<?xml version=\"1.0\" encoding=\"UTF-8\"?><Scenes>"
    validate_content+=$(echo "$xml_content" | sed '/<\?xml/d; /<!DOCTYPE/d')
    validate_content+="</Scenes>"
  fi
  
  # Validate using Python (xmllint may not be installed on Pi)
  local py_result
  py_result=$(echo "$validate_content" | python3 -c "
import sys, re
import xml.etree.ElementTree as ET
content = sys.stdin.read().strip()
if not content:
    print('EMPTY'); sys.exit(1)
try:
    clean = re.sub(r'<!DOCTYPE[^>]*>', '', content)
    root = ET.fromstring(clean)
    if len(root.findall('.//FixtureVal')) == 0:
        print('NO_FIXTURE_VALS'); sys.exit(1)
    print('OK')
except ET.ParseError as e:
    print('PARSE_ERROR: ' + str(e)); sys.exit(1)
" 2>&1)
  
  case "$py_result" in
    OK) return 0 ;;
    EMPTY)
      echo "Error: Empty XML content after cleaning" >&2
      return 1 ;;
    NO_FIXTURE_VALS)
      echo "Error: AI returned empty scene (no FixtureVal elements)" >&2
      echo "=== CONTENT ===" >&2; echo "$validate_content" >&2; echo "=== END ===" >&2
      return 1 ;;
    *)
      echo "Error: Invalid XML syntax" >&2
      echo "=== XML ERROR ===" >&2; echo "$py_result" >&2
      echo "=== CONTENT (first 500 chars) ===" >&2
      echo "$validate_content" | head -c 500 >&2; echo "" >&2; echo "=== END ===" >&2
      return 1 ;;
  esac
}

# Main scene generation function
function ai_generate_scene() {
  local description="$1"
  local style="${2:-$AI_SCENE_STYLE}"
  local workspace_file="${3:-}"
  
  echo "Generating scene: ${description}" >&2
  echo "Style: ${style}" >&2
  
  # Validate configuration
  if ! ai_validate_config; then
    return 1
  fi
  
  # Pull workspace if not provided
  if [[ -z "$workspace_file" ]]; then
    workspace_file=$(mktemp /tmp/qlc-workspace-XXXXXX.qxw)
    echo "Pulling current workspace..." >&2
    source "${SCRIPT_DIR}/scripts/lib/qlc.sh"
    qlc_pull_workspace "$workspace_file" >/dev/null
  fi
  
  # Extract fixture inventory
  echo "Analyzing fixtures..." >&2
  local fixtures_json
  fixtures_json=$(ai_extract_fixtures "$workspace_file")
  
  # Build prompts
  local system_prompt
  system_prompt=$(ai_build_system_prompt "$style")
  
  local user_prompt
  user_prompt=$(ai_build_user_prompt "$description" "$style" "$fixtures_json")
  
  # Call AI
  echo "Calling AI (${AI_PROVIDER}/${AI_MODEL})..." >&2
  local scene_xml
  scene_xml=$(ai_call_api "$system_prompt" "$user_prompt")
  
  # Validate
  if ! ai_validate_xml "$scene_xml" "$workspace_file"; then
    echo "Error: Generated XML failed validation" >&2
    return 1
  fi
  
  echo "$scene_xml"
}

# Generate multiple scene variations
function ai_generate_variations() {
  local description="$1"
  local style="$2"
  local fixtures_json="$3"
  local count="${4:-3}"
  local use_mock="${5:-false}"
  
  local variations=()
  
  for i in $(seq 1 "$count"); do
    echo "Generating variation $i of $count..." >&2
    
    # Add variation hint to description
    local varied_description="$description"
    case $i in
      1) varied_description="$description (variation: more intense)" ;;
      2) varied_description="$description (variation: softer, more subtle)" ;;
      3) varied_description="$description (variation: different color balance)" ;;
    esac
    
    local scene_xml
    if [[ "$use_mock" == true ]]; then
      scene_xml=$(ai_generate_mock_scene "$varied_description" "$style" "$fixtures_json")
    else
      # For real AI, we'll modify the prompt slightly
      local temp_workspace=$(mktemp /tmp/qlc-workspace-XXXXXX.qxw)
      echo "$fixtures_json" > "$temp_workspace"
      scene_xml=$(ai_generate_scene "$varied_description" "$style" "$temp_workspace")
      rm -f "$temp_workspace"
    fi
    
    variations+=("$scene_xml")
  done
  
  # Return variations as JSON array
  local json_output='{"variations":['
  local first=true
  for var in "${variations[@]}"; do
    if [[ "$first" == true ]]; then
      first=false
    else
      json_output+=","
    fi
    json_output+=$(echo "$var" | _json_encode)
  done
  json_output+=']}'
  
  echo "$json_output"
}

# Export functions
export -f _json_encode
export -f _json_extract
export -f _json_check
export -f ai_clean_response
export -f ai_validate_config
export -f ai_extract_fixtures
export -f ai_determine_capabilities
export -f ai_build_channel_map
export -f ai_build_system_prompt
export -f ai_build_user_prompt
export -f ai_call_api
export -f ai_call_anthropic
export -f ai_call_openai
export -f ai_call_ollama
export -f ai_validate_xml
export -f ai_generate_scene
export -f ai_generate_variations
