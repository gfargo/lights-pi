#!/usr/bin/env bash
# QLC+ Workspace manipulation functions

set -euo pipefail

# Inject a scene into a QLC+ workspace
function workspace_inject_scene() {
  local workspace_file="$1"
  local scene_xml="$2"
  local output_file="${3:-$workspace_file}"
  
  if [[ ! -f "$workspace_file" ]]; then
    echo "Error: Workspace file not found: $workspace_file" >&2
    return 1
  fi
  
  # Get next available function ID
  local next_id
  next_id=$(workspace_get_next_function_id "$workspace_file")
  
  # Use Python script for reliable XML manipulation
  local python_script="${SCRIPT_DIR}/lib/workspace_inject.py"
  
  if [[ ! -f "$python_script" ]]; then
    echo "Error: workspace_inject.py not found" >&2
    return 1
  fi
  
  # Call Python script
  python3 "$python_script" "$workspace_file" "$scene_xml" "$output_file" "$next_id"
  
  return $?
}

# Get next available function ID in workspace
function workspace_get_next_function_id() {
  local workspace_file="$1"
  
  # Find all function IDs
  local max_id=0
  local ids
  ids=$(xmllint --xpath "//*[local-name()='Function']/@ID" "$workspace_file" 2>/dev/null | grep -oE '[0-9]+' || echo "0")
  
  for id in $ids; do
    if [[ $id -gt $max_id ]]; then
      max_id=$id
    fi
  done
  
  echo $((max_id + 1))
}

# List all scenes in workspace
function workspace_list_scenes() {
  local workspace_file="$1"
  
  if [[ ! -f "$workspace_file" ]]; then
    echo "Error: Workspace file not found: $workspace_file" >&2
    return 1
  fi
  
  # Extract all Function elements with Type="Scene"
  local scene_count
  scene_count=$(xmllint --xpath "count(//*[local-name()='Function'][@Type='Scene'])" "$workspace_file" 2>/dev/null || echo "0")
  
  if [[ "$scene_count" -eq 0 ]]; then
    echo "No scenes found in workspace"
    return 0
  fi
  
  echo "Scenes in workspace:"
  echo "==================="
  
  for i in $(seq 1 "$scene_count"); do
    local id name path
    id=$(xmllint --xpath "string(//*[local-name()='Function'][@Type='Scene'][$i]/@ID)" "$workspace_file" 2>/dev/null)
    name=$(xmllint --xpath "string(//*[local-name()='Function'][@Type='Scene'][$i]/@Name)" "$workspace_file" 2>/dev/null)
    path=$(xmllint --xpath "string(//*[local-name()='Function'][@Type='Scene'][$i]/@Path)" "$workspace_file" 2>/dev/null)
    
    if [[ -n "$path" ]]; then
      echo "  [$id] $path/$name"
    else
      echo "  [$id] $name"
    fi
  done
}

# Extract a scene from workspace by ID
function workspace_extract_scene() {
  local workspace_file="$1"
  local scene_id="$2"
  
  if [[ ! -f "$workspace_file" ]]; then
    echo "Error: Workspace file not found: $workspace_file" >&2
    return 1
  fi
  
  # Extract the scene
  xmllint --xpath "//*[local-name()='Function'][@Type='Scene'][@ID='$scene_id']" "$workspace_file" 2>/dev/null
}

# Validate workspace XML
function workspace_validate() {
  local workspace_file="$1"
  
  if [[ ! -f "$workspace_file" ]]; then
    echo "Error: Workspace file not found: $workspace_file" >&2
    return 1
  fi
  
  # Check XML syntax
  if ! xmllint --noout "$workspace_file" 2>/dev/null; then
    echo "Error: Invalid XML syntax" >&2
    return 1
  fi
  
  # Check for required elements
  if ! xmllint --xpath "//*[local-name()='Workspace']" "$workspace_file" >/dev/null 2>&1; then
    echo "Error: Not a valid QLC+ workspace (missing Workspace element)" >&2
    return 1
  fi
  
  echo "Workspace is valid"
  return 0
}

# Export functions
export -f workspace_inject_scene
export -f workspace_get_next_function_id
export -f workspace_list_scenes
export -f workspace_extract_scene
export -f workspace_validate
