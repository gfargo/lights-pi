#!/usr/bin/env bash
# Scene Templates Library
# Pre-defined templates for common lighting scenarios

set -euo pipefail

# Template definitions
# Each template defines DMX values for common fixture types

# YouTube Studio / Video Recording
function template_youtube_studio() {
  local fixtures_json="$1"
  
  cat <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE Function>
<Function Type="Scene" Name="YouTube Studio">
  <Speed FadeIn="1000" FadeOut="1000" Duration="0"/>
XML

  # Set all fixtures to neutral white, high brightness
  echo "$fixtures_json" | jq -r '.fixtures[] | @json' | while IFS= read -r fixture; do
    local id=$(echo "$fixture" | jq -r '.id')
    local channels=$(echo "$fixture" | jq -r '.channels')
    local capabilities=$(echo "$fixture" | jq -r '.capabilities[]' 2>/dev/null || echo "")
    
    # Build DMX values based on capabilities
    local dmx_values=""
    
    if echo "$capabilities" | grep -q "rgb"; then
      # RGB fixture: neutral white (equal RGB at 80%)
      if [[ "$channels" -eq 3 ]]; then
        dmx_values="1,200,2,200,3,200"
      elif [[ "$channels" -ge 7 ]]; then
        # RGBWA fixture with dimmer
        dmx_values="1,255,2,200,3,200,4,200,5,0,6,0,7,0"
      fi
    else
      # Dimmer only
      dmx_values="1,255"
    fi
    
    if [[ -n "$dmx_values" ]]; then
      echo "  <FixtureVal ID=\"${id}\">${dmx_values}</FixtureVal>"
    fi
  done
  
  echo "</Function>"
}

# Party Mode
function template_party() {
  local fixtures_json="$1"
  
  cat <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE Function>
<Function Type="Scene" Name="Party Mode">
  <Speed FadeIn="100" FadeOut="100" Duration="0"/>
XML

  # Alternate fixtures between vibrant colors
  local fixture_count=$(echo "$fixtures_json" | jq '.fixtures | length')
  local color_index=0
  
  echo "$fixtures_json" | jq -r '.fixtures[] | @json' | while IFS= read -r fixture; do
    local id=$(echo "$fixture" | jq -r '.id')
    local channels=$(echo "$fixture" | jq -r '.channels')
    local capabilities=$(echo "$fixture" | jq -r '.capabilities[]' 2>/dev/null || echo "")
    
    local dmx_values=""
    
    if echo "$capabilities" | grep -q "rgb"; then
      # Cycle through vibrant colors
      case $((color_index % 4)) in
        0) # Magenta
          if [[ "$channels" -eq 3 ]]; then
            dmx_values="1,255,2,0,3,255"
          else
            dmx_values="1,255,2,255,3,0,4,255,5,0,6,0,7,0"
          fi
          ;;
        1) # Cyan
          if [[ "$channels" -eq 3 ]]; then
            dmx_values="1,0,2,255,3,255"
          else
            dmx_values="1,255,2,0,3,255,4,255,5,0,6,0,7,0"
          fi
          ;;
        2) # Yellow
          if [[ "$channels" -eq 3 ]]; then
            dmx_values="1,255,2,255,3,0"
          else
            dmx_values="1,255,2,255,3,255,4,0,5,0,6,0,7,0"
          fi
          ;;
        3) # Green
          if [[ "$channels" -eq 3 ]]; then
            dmx_values="1,0,2,255,3,0"
          else
            dmx_values="1,255,2,0,3,255,4,0,5,0,6,0,7,0"
          fi
          ;;
      esac
      color_index=$((color_index + 1))
    else
      dmx_values="1,255"
    fi
    
    if [[ -n "$dmx_values" ]]; then
      echo "  <FixtureVal ID=\"${id}\">${dmx_values}</FixtureVal>"
    fi
  done
  
  echo "</Function>"
}

# Ambient Lighting
function template_ambient() {
  local fixtures_json="$1"
  
  cat <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE Function>
<Function Type="Scene" Name="Ambient">
  <Speed FadeIn="3000" FadeOut="3000" Duration="0"/>
XML

  # Soft warm glow at low intensity
  echo "$fixtures_json" | jq -r '.fixtures[] | @json' | while IFS= read -r fixture; do
    local id=$(echo "$fixture" | jq -r '.id')
    local channels=$(echo "$fixture" | jq -r '.channels')
    local capabilities=$(echo "$fixture" | jq -r '.capabilities[]' 2>/dev/null || echo "")
    
    local dmx_values=""
    
    if echo "$capabilities" | grep -q "rgb"; then
      # Warm amber glow
      if [[ "$channels" -eq 3 ]]; then
        dmx_values="1,100,2,60,3,20"
      else
        dmx_values="1,80,2,100,3,60,4,20,5,0,6,40,7,0"
      fi
    else
      dmx_values="1,80"
    fi
    
    if [[ -n "$dmx_values" ]]; then
      echo "  <FixtureVal ID=\"${id}\">${dmx_values}</FixtureVal>"
    fi
  done
  
  echo "</Function>"
}

# Spotlight
function template_spotlight() {
  local fixtures_json="$1"
  
  cat <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE Function>
<Function Type="Scene" Name="Spotlight">
  <Speed FadeIn="500" FadeOut="500" Duration="0"/>
XML

  # First fixture at full, others off
  local first=true
  echo "$fixtures_json" | jq -r '.fixtures[] | @json' | while IFS= read -r fixture; do
    local id=$(echo "$fixture" | jq -r '.id')
    local channels=$(echo "$fixture" | jq -r '.channels')
    local capabilities=$(echo "$fixture" | jq -r '.capabilities[]' 2>/dev/null || echo "")
    
    local dmx_values=""
    
    if [[ "$first" == true ]]; then
      # First fixture: full white
      if echo "$capabilities" | grep -q "rgb"; then
        if [[ "$channels" -eq 3 ]]; then
          dmx_values="1,255,2,255,3,255"
        else
          dmx_values="1,255,2,255,3,255,4,255,5,0,6,0,7,0"
        fi
      else
        dmx_values="1,255"
      fi
      first=false
    else
      # Other fixtures: off
      if [[ "$channels" -eq 3 ]]; then
        dmx_values="1,0,2,0,3,0"
      elif [[ "$channels" -ge 7 ]]; then
        dmx_values="1,0,2,0,3,0,4,0,5,0,6,0,7,0"
      else
        dmx_values="1,0"
      fi
    fi
    
    if [[ -n "$dmx_values" ]]; then
      echo "  <FixtureVal ID=\"${id}\">${dmx_values}</FixtureVal>"
    fi
  done
  
  echo "</Function>"
}

# Work Light (bright neutral)
function template_work_light() {
  local fixtures_json="$1"
  
  cat <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE Function>
<Function Type="Scene" Name="Work Light">
  <Speed FadeIn="500" FadeOut="500" Duration="0"/>
XML

  # All fixtures at full brightness, neutral white
  echo "$fixtures_json" | jq -r '.fixtures[] | @json' | while IFS= read -r fixture; do
    local id=$(echo "$fixture" | jq -r '.id')
    local channels=$(echo "$fixture" | jq -r '.channels')
    local capabilities=$(echo "$fixture" | jq -r '.capabilities[]' 2>/dev/null || echo "")
    
    local dmx_values=""
    
    if echo "$capabilities" | grep -q "rgb"; then
      if [[ "$channels" -eq 3 ]]; then
        dmx_values="1,255,2,255,3,255"
      else
        dmx_values="1,255,2,255,3,255,4,255,5,0,6,0,7,0"
      fi
    else
      dmx_values="1,255"
    fi
    
    if [[ -n "$dmx_values" ]]; then
      echo "  <FixtureVal ID=\"${id}\">${dmx_values}</FixtureVal>"
    fi
  done
  
  echo "</Function>"
}

# Warm White
function template_warm_white() {
  local fixtures_json="$1"
  
  cat <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE Function>
<Function Type="Scene" Name="Warm White">
  <Speed FadeIn="1000" FadeOut="1000" Duration="0"/>
XML

  # Warm white (high red, medium green, low blue)
  echo "$fixtures_json" | jq -r '.fixtures[] | @json' | while IFS= read -r fixture; do
    local id=$(echo "$fixture" | jq -r '.id')
    local channels=$(echo "$fixture" | jq -r '.channels')
    local capabilities=$(echo "$fixture" | jq -r '.capabilities[]' 2>/dev/null || echo "")
    
    local dmx_values=""
    
    if echo "$capabilities" | grep -q "rgb"; then
      if [[ "$channels" -eq 3 ]]; then
        dmx_values="1,255,2,180,3,100"
      else
        dmx_values="1,220,2,255,3,180,4,100,5,0,6,80,7,0"
      fi
    else
      dmx_values="1,220"
    fi
    
    if [[ -n "$dmx_values" ]]; then
      echo "  <FixtureVal ID=\"${id}\">${dmx_values}</FixtureVal>"
    fi
  done
  
  echo "</Function>"
}

# Cool White
function template_cool_white() {
  local fixtures_json="$1"
  
  cat <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE Function>
<Function Type="Scene" Name="Cool White">
  <Speed FadeIn="1000" FadeOut="1000" Duration="0"/>
XML

  # Cool white (low red, medium green, high blue)
  echo "$fixtures_json" | jq -r '.fixtures[] | @json' | while IFS= read -r fixture; do
    local id=$(echo "$fixture" | jq -r '.id')
    local channels=$(echo "$fixture" | jq -r '.channels')
    local capabilities=$(echo "$fixture" | jq -r '.capabilities[]' 2>/dev/null || echo "")
    
    local dmx_values=""
    
    if echo "$capabilities" | grep -q "rgb"; then
      if [[ "$channels" -eq 3 ]]; then
        dmx_values="1,180,2,220,3,255"
      else
        dmx_values="1,220,2,180,3,220,4,255,5,0,6,0,7,0"
      fi
    else
      dmx_values="1,220"
    fi
    
    if [[ -n "$dmx_values" ]]; then
      echo "  <FixtureVal ID=\"${id}\">${dmx_values}</FixtureVal>"
    fi
  done
  
  echo "</Function>"
}

# List all available templates
function template_list() {
  cat <<'LIST'
Available Scene Templates:

  youtube-studio    Bright neutral white for video recording
  party             Vibrant alternating colors with fast transitions
  ambient           Soft warm glow at low intensity
  spotlight         Single fixture at full, others off
  work-light        Bright neutral white for task lighting
  warm-white        Warm white (2700K-3000K color temperature)
  cool-white        Cool white (5000K-6500K color temperature)

Usage:
  ./lightsctl.sh generate-from-template <template-name> [options]
  
Options:
  --preview                   Show generated XML without deploying
  --add-to-workspace          Add to current workspace and deploy
  --output <file>             Save scene XML to file
  --workspace <file>          Use specific workspace file

Examples:
  ./lightsctl.sh generate-from-template youtube-studio --preview
  ./lightsctl.sh generate-from-template party --add-to-workspace
  ./lightsctl.sh generate-from-template warm-white --output scenes/warm.xml
LIST
}

# Generate scene from template
function template_generate() {
  local template_name="$1"
  local fixtures_json="$2"
  
  case "$template_name" in
    youtube-studio)
      template_youtube_studio "$fixtures_json"
      ;;
    party)
      template_party "$fixtures_json"
      ;;
    ambient)
      template_ambient "$fixtures_json"
      ;;
    spotlight)
      template_spotlight "$fixtures_json"
      ;;
    work-light)
      template_work_light "$fixtures_json"
      ;;
    warm-white)
      template_warm_white "$fixtures_json"
      ;;
    cool-white)
      template_cool_white "$fixtures_json"
      ;;
    *)
      echo "Error: Unknown template: $template_name" >&2
      echo "" >&2
      template_list >&2
      return 1
      ;;
  esac
}

# Export functions
export -f template_youtube_studio
export -f template_party
export -f template_ambient
export -f template_spotlight
export -f template_work_light
export -f template_warm_white
export -f template_cool_white
export -f template_list
export -f template_generate
