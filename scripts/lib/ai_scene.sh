#!/usr/bin/env bash
# AI Scene Generation Library
# Generates QLC+ scenes using AI based on natural language descriptions

set -euo pipefail

# Load environment variables
if [[ -f "${ENV_FILE:-}" ]]; then
  source "${ENV_FILE}"
fi

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
    if ! curl -s http://localhost:11434/api/tags | jq -e ".models[] | select(.name | startswith(\"$AI_MODEL\"))" >/dev/null 2>&1; then
      echo "Warning: Model '$AI_MODEL' may not be available in Ollama" >&2
      echo "Available models:" >&2
      curl -s http://localhost:11434/api/tags | jq -r '.models[].name' >&2
      echo "" >&2
      echo "Pull model with: ollama pull $AI_MODEL" >&2
    fi
  fi
  
  return 0
}

# Extract fixture inventory from QLC+ workspace XML
function ai_extract_fixtures() {
  local workspace_file="$1"
  
  if [[ ! -f "$workspace_file" ]]; then
    echo "Error: Workspace file not found: $workspace_file" >&2
    return 1
  fi
  
  # Extract fixtures using xmllint with namespace handling
  local fixtures_json='{"fixtures":[]}'
  
  # Get fixture count (use local-name() to ignore namespace)
  local fixture_count
  fixture_count=$(xmllint --xpath "count(//*[local-name()='Fixture'])" "$workspace_file" 2>/dev/null || echo "0")
  
  if [[ "$fixture_count" -eq 0 ]]; then
    echo "$fixtures_json"
    return 0
  fi
  
  # Build JSON array of fixtures
  local fixtures_array="["
  local first=true
  
  for i in $(seq 1 "$fixture_count"); do
    local id manufacturer model mode name universe address channels
    
    id=$(xmllint --xpath "string(//*[local-name()='Fixture'][$i]/*[local-name()='ID'])" "$workspace_file" 2>/dev/null)
    manufacturer=$(xmllint --xpath "string(//*[local-name()='Fixture'][$i]/*[local-name()='Manufacturer'])" "$workspace_file" 2>/dev/null)
    model=$(xmllint --xpath "string(//*[local-name()='Fixture'][$i]/*[local-name()='Model'])" "$workspace_file" 2>/dev/null)
    mode=$(xmllint --xpath "string(//*[local-name()='Fixture'][$i]/*[local-name()='Mode'])" "$workspace_file" 2>/dev/null)
    name=$(xmllint --xpath "string(//*[local-name()='Fixture'][$i]/*[local-name()='Name'])" "$workspace_file" 2>/dev/null)
    universe=$(xmllint --xpath "string(//*[local-name()='Fixture'][$i]/*[local-name()='Universe'])" "$workspace_file" 2>/dev/null)
    address=$(xmllint --xpath "string(//*[local-name()='Fixture'][$i]/*[local-name()='Address'])" "$workspace_file" 2>/dev/null)
    channels=$(xmllint --xpath "string(//*[local-name()='Fixture'][$i]/*[local-name()='Channels'])" "$workspace_file" 2>/dev/null)
    
    # Determine capabilities based on model and mode
    local capabilities
    capabilities=$(ai_determine_capabilities "$manufacturer" "$model" "$mode" "$channels")
    
    # Build channel map
    local channel_map
    channel_map=$(ai_build_channel_map "$manufacturer" "$model" "$mode" "$channels")
    
    if [[ "$first" == true ]]; then
      first=false
    else
      fixtures_array+=","
    fi
    
    fixtures_array+=$(cat <<JSON
{
  "id": $id,
  "name": $(echo "$name" | jq -Rs .),
  "manufacturer": $(echo "$manufacturer" | jq -Rs .),
  "model": $(echo "$model" | jq -Rs .),
  "mode": $(echo "$mode" | jq -Rs .),
  "universe": $universe,
  "address": $address,
  "channels": $channels,
  "capabilities": $capabilities,
  "channel_map": $channel_map
}
JSON
)
  done
  
  fixtures_array+="]"
  
  echo "{\"fixtures\":$fixtures_array}"
}

# Determine fixture capabilities based on model
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
  "system": $(jq -Rs . <<< "$system_prompt"),
  "messages": [
    {
      "role": "user",
      "content": $(jq -Rs . <<< "$user_prompt")
    }
  ]
}
JSON
)
  
  # Extract content from response
  echo "$response" | jq -r '.content[0].text'
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
      "content": $(jq -Rs . <<< "$system_prompt")
    },
    {
      "role": "user",
      "content": $(jq -Rs . <<< "$user_prompt")
    }
  ]
}
JSON
)
  
  echo "$response" | jq -r '.choices[0].message.content'
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
  "prompt": $(echo "$combined_prompt" | jq -Rs .),
  "stream": false
}
JSON
)
  
  local generated_text
  generated_text=$(echo "$response" | jq -r '.response')
  
  # Debug: Save raw response
  if [[ "${DEBUG:-}" == "true" ]]; then
    echo "=== RAW OLLAMA RESPONSE ===" >&2
    echo "$generated_text" >&2
    echo "=== END RAW RESPONSE ===" >&2
  fi
  
  # Try to extract XML if wrapped in markdown or other text
  if echo "$generated_text" | grep -q '<?xml'; then
    # Extract from first <?xml to last </Function>
    echo "$generated_text" | sed -n '/<\?xml/,/<\/Function>/p'
  else
    echo "$generated_text"
  fi
}

# Validate generated XML
function ai_validate_xml() {
  local xml_content="$1"
  local workspace_file="$2"
  
  # Check XML syntax
  if ! echo "$xml_content" | xmllint --noout - 2>/dev/null; then
    echo "Error: Invalid XML syntax" >&2
    return 1
  fi
  
  # TODO: Add more validation:
  # - Fixture ID existence
  # - Channel number validity
  # - DMX value range (0-255)
  
  return 0
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

# Export functions
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
