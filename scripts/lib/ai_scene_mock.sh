#!/usr/bin/env bash
# Mock AI scene generation for testing without API keys

set -euo pipefail

# Generate a mock scene based on description and style
function ai_generate_mock_scene() {
  local description="$1"
  local style="${2:-complete}"
  local fixtures_json="$3"
  
  case "$style" in
    complete)
      ai_generate_mock_complete "$description" "$fixtures_json"
      ;;
    modular)
      ai_generate_mock_modular "$description" "$fixtures_json"
      ;;
    timeline)
      ai_generate_mock_timeline "$description" "$fixtures_json"
      ;;
    reactive)
      ai_generate_mock_reactive "$description" "$fixtures_json"
      ;;
    *)
      echo "Error: Unknown style: $style" >&2
      return 1
      ;;
  esac
}

# Generate complete style mock scene
function ai_generate_mock_complete() {
  local description="$1"
  local fixtures_json="$2"
  
  # Parse description for color hints
  local red=255 green=140 blue=0 dimmer=180
  
  if [[ "$description" =~ "blue" ]] || [[ "$description" =~ "cool" ]]; then
    red=0 green=100 blue=255 dimmer=200
  elif [[ "$description" =~ "red" ]]; then
    red=255 green=0 blue=0 dimmer=200
  elif [[ "$description" =~ "green" ]]; then
    red=0 green=255 blue=0 dimmer=200
  elif [[ "$description" =~ "purple" ]]; then
    red=200 green=0 blue=255 dimmer=180
  elif [[ "$description" =~ "white" ]]; then
    red=255 green=255 blue=255 dimmer=220
  elif [[ "$description" =~ "warm" ]] || [[ "$description" =~ "sunset" ]] || [[ "$description" =~ "amber" ]]; then
    red=255 green=140 blue=0 dimmer=180
  fi
  
  # Extract fixture IDs
  local fixture_ids
  fixture_ids=$(echo "$fixtures_json" | jq -r '.fixtures[].id')
  
  cat <<XML
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE Function>
<Function Type="Scene" Name="AI: ${description}">
  <Speed FadeIn="500" FadeOut="500" Duration="0"/>
XML

  for fid in $fixture_ids; do
    local channels
    channels=$(echo "$fixtures_json" | jq -r ".fixtures[] | select(.id==$fid) | .channels")
    local model
    model=$(echo "$fixtures_json" | jq -r ".fixtures[] | select(.id==$fid) | .model")
    
    # Generate channel values based on fixture type
    if [[ "$model" =~ "SlimPAR Pro H USB" ]]; then
      # 7 Channel: Dimmer, Red, Green, Blue, Color Macros, Amber, Strobe
      echo "  <FixtureVal ID=\"$fid\">1,$dimmer,2,$red,3,$green,4,$blue,5,0,6,0,7,0</FixtureVal>"
    elif [[ "$model" =~ "SlimPAR 56" ]]; then
      # 3 Channel: Red, Green, Blue
      echo "  <FixtureVal ID=\"$fid\">1,$red,2,$green,3,$blue</FixtureVal>"
    else
      # Generic RGB
      echo "  <FixtureVal ID=\"$fid\">1,$red,2,$green,3,$blue,4,$dimmer</FixtureVal>"
    fi
  done
  
  echo "</Function>"
}

# Generate modular style mock scenes
function ai_generate_mock_modular() {
  local description="$1"
  local fixtures_json="$2"
  
  # Generate color layer
  echo "<!-- Color Layer -->"
  ai_generate_mock_complete "Color: $description" "$fixtures_json"
  echo ""
  
  # Generate intensity layer
  echo "<!-- Intensity Layer -->"
  cat <<XML
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE Function>
<Function Type="Scene" Name="AI: Intensity - Medium">
  <Speed FadeIn="500" FadeOut="500" Duration="0"/>
XML
  
  local fixture_ids
  fixture_ids=$(echo "$fixtures_json" | jq -r '.fixtures[].id')
  
  for fid in $fixture_ids; do
    local model
    model=$(echo "$fixtures_json" | jq -r ".fixtures[] | select(.id==$fid) | .model")
    
    if [[ "$model" =~ "SlimPAR Pro H USB" ]]; then
      echo "  <FixtureVal ID=\"$fid\">1,180</FixtureVal>"
    fi
  done
  
  echo "</Function>"
}

# Generate timeline style mock scene
function ai_generate_mock_timeline() {
  local description="$1"
  local fixtures_json="$2"
  
  cat <<XML
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE Function>
<Function Type="Chaser" Name="AI: ${description}">
  <Speed FadeIn="1000" FadeOut="1000" Duration="3000"/>
  <Direction>Forward</Direction>
  <RunOrder>Loop</RunOrder>
  <!-- Timeline scenes would be implemented as QLC+ Chasers -->
  <!-- Step 1: Start state -->
  <!-- Step 2: Mid state -->
  <!-- Step 3: End state -->
</Function>
XML
}

# Generate reactive style mock scene
function ai_generate_mock_reactive() {
  local description="$1"
  local fixtures_json="$2"
  
  cat <<XML
<?xml version="1.0" encoding="UTF-8"?>
<!-- Reactive scenes require QLC+ Audio Triggers or external control -->
<!-- This is a placeholder showing the concept -->
<Function Type="Scene" Name="AI: ${description} (Reactive)">
  <Speed FadeIn="100" FadeOut="100" Duration="0"/>
  <!-- Would be controlled by Audio Triggers in Virtual Console -->
</Function>
XML
}

# Export functions
export -f ai_generate_mock_scene
export -f ai_generate_mock_complete
export -f ai_generate_mock_modular
export -f ai_generate_mock_timeline
export -f ai_generate_mock_reactive
