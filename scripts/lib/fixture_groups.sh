#!/usr/bin/env bash
# Fixture Groups/Zones Management
# Organize fixtures into named groups for easier control

set -euo pipefail

# Configuration
GROUPS_FILE="${GROUPS_FILE:-${HOME}/.qlcplus/fixture_groups.json}"

# Initialize groups file if it doesn't exist
function groups_init() {
  if [[ ! -f "$GROUPS_FILE" ]]; then
    mkdir -p "$(dirname "$GROUPS_FILE")"
    echo '{"groups":{}}' > "$GROUPS_FILE"
  fi
}

# List all groups
function groups_list() {
  groups_init
  
  local groups_json
  groups_json=$(cat "$GROUPS_FILE")
  
  local group_count
  group_count=$(echo "$groups_json" | jq '.groups | length')
  
  if [[ "$group_count" -eq 0 ]]; then
    echo "No fixture groups defined."
    echo ""
    echo "Create a group with:"
    echo "  ./lightsctl.sh group-create <name> <fixture-ids>"
    return 0
  fi
  
  echo "Fixture Groups:"
  echo "==============="
  echo ""
  
  echo "$groups_json" | jq -r '.groups | to_entries[] | 
    "  \(.key)\n    Fixtures: \(.value.fixtures | join(", "))\n    Description: \(.value.description // "No description")\n"'
}

# Create a new group
function groups_create() {
  local name="$1"
  local fixture_ids="$2"
  local description="${3:-}"
  
  groups_init
  
  # Validate name
  if [[ -z "$name" ]]; then
    echo "Error: Group name required" >&2
    return 1
  fi
  
  # Parse fixture IDs (comma-separated)
  local ids_array
  IFS=',' read -ra ids_array <<< "$fixture_ids"
  
  # Build JSON array
  local json_ids
  json_ids=$(printf '%s\n' "${ids_array[@]}" | jq -R . | jq -s .)
  
  # Update groups file
  local updated
  updated=$(cat "$GROUPS_FILE" | jq \
    --arg name "$name" \
    --argjson ids "$json_ids" \
    --arg desc "$description" \
    '.groups[$name] = {
      "fixtures": $ids,
      "description": $desc,
      "created": (now | todate)
    }')
  
  echo "$updated" > "$GROUPS_FILE"
  
  echo "✓ Group '$name' created with ${#ids_array[@]} fixture(s)"
}

# Delete a group
function groups_delete() {
  local name="$1"
  
  groups_init
  
  # Check if group exists
  if ! cat "$GROUPS_FILE" | jq -e ".groups[\"$name\"]" >/dev/null 2>&1; then
    echo "Error: Group '$name' not found" >&2
    return 1
  fi
  
  # Delete group
  local updated
  updated=$(cat "$GROUPS_FILE" | jq --arg name "$name" 'del(.groups[$name])')
  echo "$updated" > "$GROUPS_FILE"
  
  echo "✓ Group '$name' deleted"
}

# Get fixtures in a group
function groups_get() {
  local name="$1"
  
  groups_init
  
  # Check if group exists
  if ! cat "$GROUPS_FILE" | jq -e ".groups[\"$name\"]" >/dev/null 2>&1; then
    echo "Error: Group '$name' not found" >&2
    return 1
  fi
  
  # Return fixture IDs as JSON array
  cat "$GROUPS_FILE" | jq -r ".groups[\"$name\"].fixtures | @json"
}

# Update group description
function groups_update() {
  local name="$1"
  local description="$2"
  
  groups_init
  
  # Check if group exists
  if ! cat "$GROUPS_FILE" | jq -e ".groups[\"$name\"]" >/dev/null 2>&1; then
    echo "Error: Group '$name' not found" >&2
    return 1
  fi
  
  # Update description
  local updated
  updated=$(cat "$GROUPS_FILE" | jq \
    --arg name "$name" \
    --arg desc "$description" \
    '.groups[$name].description = $desc')
  
  echo "$updated" > "$GROUPS_FILE"
  
  echo "✓ Group '$name' updated"
}

# Add fixtures to existing group
function groups_add_fixtures() {
  local name="$1"
  local fixture_ids="$2"
  
  groups_init
  
  # Check if group exists
  if ! cat "$GROUPS_FILE" | jq -e ".groups[\"$name\"]" >/dev/null 2>&1; then
    echo "Error: Group '$name' not found" >&2
    return 1
  fi
  
  # Parse fixture IDs
  local ids_array
  IFS=',' read -ra ids_array <<< "$fixture_ids"
  
  # Add to existing fixtures
  local updated
  for id in "${ids_array[@]}"; do
    updated=$(cat "$GROUPS_FILE" | jq \
      --arg name "$name" \
      --arg id "$id" \
      '.groups[$name].fixtures += [$id] | .groups[$name].fixtures |= unique')
    echo "$updated" > "$GROUPS_FILE"
  done
  
  echo "✓ Added ${#ids_array[@]} fixture(s) to group '$name'"
}

# Remove fixtures from group
function groups_remove_fixtures() {
  local name="$1"
  local fixture_ids="$2"
  
  groups_init
  
  # Check if group exists
  if ! cat "$GROUPS_FILE" | jq -e ".groups[\"$name\"]" >/dev/null 2>&1; then
    echo "Error: Group '$name' not found" >&2
    return 1
  fi
  
  # Parse fixture IDs
  local ids_array
  IFS=',' read -ra ids_array <<< "$fixture_ids"
  
  # Remove from fixtures
  local updated
  for id in "${ids_array[@]}"; do
    updated=$(cat "$GROUPS_FILE" | jq \
      --arg name "$name" \
      --arg id "$id" \
      '.groups[$name].fixtures -= [$id]')
    echo "$updated" > "$GROUPS_FILE"
  done
  
  echo "✓ Removed ${#ids_array[@]} fixture(s) from group '$name'"
}

# Generate scene for a specific group
function groups_generate_scene() {
  local group_name="$1"
  local description="$2"
  local workspace_file="${3:-}"
  
  groups_init
  
  # Get fixtures in group
  local fixture_ids
  if ! fixture_ids=$(groups_get "$group_name"); then
    return 1
  fi
  
  # Load AI scene generation
  source "${SCRIPT_DIR}/scripts/lib/ai_scene.sh"
  
  # Get workspace
  if [[ -z "$workspace_file" ]]; then
    workspace_file=$(mktemp /tmp/qlc-workspace-XXXXXX.qxw)
    source "${SCRIPT_DIR}/scripts/lib/qlc.sh"
    qlc_pull_workspace "$workspace_file" >/dev/null
  fi
  
  # Extract all fixtures
  local all_fixtures_json
  all_fixtures_json=$(ai_extract_fixtures "$workspace_file")
  
  # Filter to only group fixtures
  local group_fixtures_json
  group_fixtures_json=$(echo "$all_fixtures_json" | jq \
    --argjson ids "$fixture_ids" \
    '{fixtures: [.fixtures[] | select(.id | tostring | IN($ids[]))]}')
  
  # Build prompts
  local system_prompt
  system_prompt=$(ai_build_system_prompt "complete")
  
  local user_prompt
  user_prompt=$(ai_build_user_prompt "$description" "complete" "$group_fixtures_json")
  
  # Call AI
  echo "Generating scene for group '$group_name'..." >&2
  local scene_xml
  scene_xml=$(ai_call_api "$system_prompt" "$user_prompt")
  
  # Validate
  if ! ai_validate_xml "$scene_xml" "$workspace_file"; then
    echo "Error: Generated XML failed validation" >&2
    return 1
  fi
  
  echo "$scene_xml"
}

# Apply template to a specific group
function groups_apply_template() {
  local group_name="$1"
  local template_name="$2"
  local workspace_file="${3:-}"
  
  groups_init
  
  # Get fixtures in group
  local fixture_ids
  if ! fixture_ids=$(groups_get "$group_name"); then
    return 1
  fi
  
  # Load template library
  source "${SCRIPT_DIR}/scripts/lib/scene_templates.sh"
  source "${SCRIPT_DIR}/scripts/lib/ai_scene.sh"
  
  # Get workspace
  if [[ -z "$workspace_file" ]]; then
    workspace_file=$(mktemp /tmp/qlc-workspace-XXXXXX.qxw)
    source "${SCRIPT_DIR}/scripts/lib/qlc.sh"
    qlc_pull_workspace "$workspace_file" >/dev/null
  fi
  
  # Extract all fixtures
  local all_fixtures_json
  all_fixtures_json=$(ai_extract_fixtures "$workspace_file")
  
  # Filter to only group fixtures
  local group_fixtures_json
  group_fixtures_json=$(echo "$all_fixtures_json" | jq \
    --argjson ids "$fixture_ids" \
    '{fixtures: [.fixtures[] | select(.id | tostring | IN($ids[]))]}')
  
  # Generate template
  echo "Applying template '$template_name' to group '$group_name'..." >&2
  local scene_xml
  scene_xml=$(template_generate "$template_name" "$group_fixtures_json")
  
  if [[ $? -ne 0 ]]; then
    return 1
  fi
  
  echo "$scene_xml"
}

# Export functions
export -f groups_init
export -f groups_list
export -f groups_create
export -f groups_delete
export -f groups_get
export -f groups_update
export -f groups_add_fixtures
export -f groups_remove_fixtures
export -f groups_generate_scene
export -f groups_apply_template
