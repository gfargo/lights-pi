#!/usr/bin/env bash
# Fixture Groups/Zones Management
# Organize fixtures into named groups for easier control

set -euo pipefail

# Configuration
GROUPS_FILE="${GROUPS_FILE:-${HOME}/.qlcplus/fixture_groups.json}"

# Python helper for all groups JSON operations (jq-free)
_groups_py() {
  python3 -c "
import sys, json, os
from datetime import datetime, timezone

args = sys.argv[1:]
cmd = args[0] if args else ''
gfile = args[1] if len(args) > 1 else ''

def load(f):
    with open(f) as fh:
        return json.load(fh)

def save(f, data):
    with open(f, 'w') as fh:
        json.dump(data, fh, indent=2)

if cmd == 'count':
    data = load(gfile)
    print(len(data.get('groups', {})))

elif cmd == 'list':
    data = load(gfile)
    for name, g in data.get('groups', {}).items():
        fixtures = ', '.join(g.get('fixtures', []))
        desc = g.get('description', '') or 'No description'
        print(f'  {name}')
        print(f'    Fixtures: {fixtures}')
        print(f'    Description: {desc}')
        print()

elif cmd == 'exists':
    name = args[2]
    data = load(gfile)
    sys.exit(0 if name in data.get('groups', {}) else 1)

elif cmd == 'create':
    name, ids_json, desc = args[2], args[3], args[4] if len(args) > 4 else ''
    data = load(gfile)
    ids = json.loads(ids_json)
    data.setdefault('groups', {})[name] = {
        'fixtures': ids,
        'description': desc,
        'created': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    }
    save(gfile, data)

elif cmd == 'delete':
    name = args[2]
    data = load(gfile)
    del data['groups'][name]
    save(gfile, data)

elif cmd == 'get':
    name = args[2]
    data = load(gfile)
    print(json.dumps(data['groups'][name]['fixtures']))

elif cmd == 'update_desc':
    name, desc = args[2], args[3]
    data = load(gfile)
    data['groups'][name]['description'] = desc
    save(gfile, data)

elif cmd == 'add_fixtures':
    name, ids_json = args[2], args[3]
    data = load(gfile)
    ids = json.loads(ids_json)
    existing = data['groups'][name]['fixtures']
    for fid in ids:
        if fid not in existing:
            existing.append(fid)
    save(gfile, data)

elif cmd == 'remove_fixtures':
    name, ids_json = args[2], args[3]
    data = load(gfile)
    ids = json.loads(ids_json)
    data['groups'][name]['fixtures'] = [f for f in data['groups'][name]['fixtures'] if f not in ids]
    save(gfile, data)

elif cmd == 'filter_fixtures':
    # Read all_fixtures_json from stdin, filter by fixture_ids (args[2])
    fixture_ids = json.loads(args[2])
    all_data = json.load(sys.stdin)
    filtered = [f for f in all_data.get('fixtures', []) if str(f.get('id', '')) in fixture_ids]
    print(json.dumps({'fixtures': filtered}))
" "$@"
}

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
  
  local group_count
  group_count=$(_groups_py count "$GROUPS_FILE")
  
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
  
  _groups_py list "$GROUPS_FILE"
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
  
  # Parse fixture IDs (comma-separated) into JSON array
  local ids_array
  IFS=',' read -ra ids_array <<< "$fixture_ids"
  local json_ids
  json_ids=$(printf '%s\n' "${ids_array[@]}" | python3 -c "import sys,json; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))")
  
  _groups_py create "$GROUPS_FILE" "$name" "$json_ids" "$description"
  
  echo "✓ Group '$name' created with ${#ids_array[@]} fixture(s)"
}

# Delete a group
function groups_delete() {
  local name="$1"
  
  groups_init
  
  if ! _groups_py exists "$GROUPS_FILE" "$name"; then
    echo "Error: Group '$name' not found" >&2
    return 1
  fi
  
  _groups_py delete "$GROUPS_FILE" "$name"
  
  echo "✓ Group '$name' deleted"
}

# Get fixtures in a group
function groups_get() {
  local name="$1"
  
  groups_init
  
  if ! _groups_py exists "$GROUPS_FILE" "$name"; then
    echo "Error: Group '$name' not found" >&2
    return 1
  fi
  
  _groups_py get "$GROUPS_FILE" "$name"
}

# Update group description
function groups_update() {
  local name="$1"
  local description="$2"
  
  groups_init
  
  if ! _groups_py exists "$GROUPS_FILE" "$name"; then
    echo "Error: Group '$name' not found" >&2
    return 1
  fi
  
  _groups_py update_desc "$GROUPS_FILE" "$name" "$description"
  
  echo "✓ Group '$name' updated"
}

# Add fixtures to existing group
function groups_add_fixtures() {
  local name="$1"
  local fixture_ids="$2"
  
  groups_init
  
  if ! _groups_py exists "$GROUPS_FILE" "$name"; then
    echo "Error: Group '$name' not found" >&2
    return 1
  fi
  
  # Parse fixture IDs into JSON array
  local ids_array
  IFS=',' read -ra ids_array <<< "$fixture_ids"
  local json_ids
  json_ids=$(printf '%s\n' "${ids_array[@]}" | python3 -c "import sys,json; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))")
  
  _groups_py add_fixtures "$GROUPS_FILE" "$name" "$json_ids"
  
  echo "✓ Added ${#ids_array[@]} fixture(s) to group '$name'"
}

# Remove fixtures from group
function groups_remove_fixtures() {
  local name="$1"
  local fixture_ids="$2"
  
  groups_init
  
  if ! _groups_py exists "$GROUPS_FILE" "$name"; then
    echo "Error: Group '$name' not found" >&2
    return 1
  fi
  
  # Parse fixture IDs into JSON array
  local ids_array
  IFS=',' read -ra ids_array <<< "$fixture_ids"
  local json_ids
  json_ids=$(printf '%s\n' "${ids_array[@]}" | python3 -c "import sys,json; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))")
  
  _groups_py remove_fixtures "$GROUPS_FILE" "$name" "$json_ids"
  
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
  group_fixtures_json=$(echo "$all_fixtures_json" | _groups_py filter_fixtures "" "$fixture_ids")
  
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
  group_fixtures_json=$(echo "$all_fixtures_json" | _groups_py filter_fixtures "" "$fixture_ids")
  
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
export -f _groups_py
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

# Import groups from QLC+ workspace
function groups_import() {
  local workspace_file="${1:-}"
  
  groups_init
  
  # Get workspace
  if [[ -z "$workspace_file" ]]; then
    workspace_file=$(mktemp /tmp/qlc-workspace-XXXXXX.qxw)
    echo "Pulling current workspace from Pi..." >&2
    source "${SCRIPT_DIR}/scripts/lib/qlc.sh"
    qlc_pull_workspace "$workspace_file" >/dev/null
  fi
  
  # Import using Python script
  python3 "${SCRIPT_DIR}/scripts/lib/fixture_groups_sync.py" import "$workspace_file" "$GROUPS_FILE"
}

# Export groups to QLC+ workspace
function groups_export() {
  local workspace_file="${1:-}"
  local deploy="${2:-false}"
  
  groups_init
  
  # Get workspace
  local temp_workspace=false
  if [[ -z "$workspace_file" ]]; then
    workspace_file=$(mktemp /tmp/qlc-workspace-XXXXXX.qxw)
    temp_workspace=true
    echo "Pulling current workspace from Pi..." >&2
    source "${SCRIPT_DIR}/scripts/lib/qlc.sh"
    qlc_pull_workspace "$workspace_file" >/dev/null
  fi
  
  # Export using Python script
  local output_file=$(mktemp /tmp/qlc-workspace-modified-XXXXXX.qxw)
  python3 "${SCRIPT_DIR}/scripts/lib/fixture_groups_sync.py" export "$GROUPS_FILE" "$workspace_file" "$output_file"
  
  # Deploy if requested
  if [[ "$deploy" == "true" ]]; then
    echo "Deploying to Pi..." >&2
    source "${SCRIPT_DIR}/scripts/lib/qlc.sh"
    qlc_deploy_workspace "$output_file"
  else
    # Output to stdout or save
    cat "$output_file"
  fi
  
  # Cleanup
  rm -f "$output_file"
  if [[ "$temp_workspace" == true ]]; then
    rm -f "$workspace_file"
  fi
}

export -f groups_import
export -f groups_export
