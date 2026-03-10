#!/usr/bin/env bash
# Test scene generation with mock AI

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Source libraries
source "${SCRIPT_DIR}/lib/ai_scene.sh"
source "${SCRIPT_DIR}/lib/ai_scene_mock.sh"

# Test with RiversWayStudio.qxw
WORKSPACE="${PROJECT_ROOT}/RiversWayStudio.qxw"

if [[ ! -f "$WORKSPACE" ]]; then
  echo "Error: Workspace file not found: $WORKSPACE"
  exit 1
fi

echo "Testing AI Scene Generation (Mock Mode)"
echo "========================================"
echo ""

# Extract fixtures
echo "1. Extracting fixtures..."
fixtures_json=$(ai_extract_fixtures "$WORKSPACE")
fixture_count=$(echo "$fixtures_json" | jq '.fixtures | length')
echo "   Found $fixture_count fixtures"
echo ""

# Test descriptions
descriptions=(
  "warm sunset ambiance"
  "cool blue ambient"
  "dramatic red spotlight"
  "party mode purple"
  "soft white for video"
)

styles=(
  "complete"
  "complete"
  "complete"
  "modular"
  "complete"
)

for i in "${!descriptions[@]}"; do
  desc="${descriptions[$i]}"
  style="${styles[$i]}"
  
  echo "2. Generating scene: \"$desc\" (style: $style)"
  echo "   ----------------------------------------"
  
  scene_xml=$(ai_generate_mock_scene "$desc" "$style" "$fixtures_json")
  
  # Save to file
  output_file="${PROJECT_ROOT}/scenes/examples/ai-generated-$(echo "$desc" | tr ' ' '-')-${style}.xml"
  echo "$scene_xml" > "$output_file"
  
  echo "   Saved to: $output_file"
  echo ""
  
  # Show preview
  echo "$scene_xml" | head -n 15
  echo "   ..."
  echo ""
done

echo "========================================"
echo "Test complete! Generated ${#descriptions[@]} scenes."
echo ""
echo "View generated scenes:"
echo "  ls -lh scenes/examples/ai-generated-*.xml"
