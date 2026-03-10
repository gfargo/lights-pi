#!/usr/bin/env bash
# Test complete AI scene generation workflow

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Source libraries
source "${SCRIPT_DIR}/lib/ai_scene.sh"
source "${SCRIPT_DIR}/lib/ai_scene_mock.sh"
source "${SCRIPT_DIR}/lib/workspace.sh"

# Test workspace
WORKSPACE="${PROJECT_ROOT}/RiversWayStudio.qxw"
TEST_WORKSPACE="${PROJECT_ROOT}/RiversWayStudio-test.qxw"

echo "AI Scene Generation - Full Workflow Test"
echo "========================================="
echo ""

# Step 1: Validate original workspace
echo "Step 1: Validating workspace..."
if ! workspace_validate "$WORKSPACE"; then
  echo "Error: Workspace validation failed"
  exit 1
fi
echo "✓ Workspace is valid"
echo ""

# Step 2: List existing scenes
echo "Step 2: Listing existing scenes..."
workspace_list_scenes "$WORKSPACE"
echo ""

# Step 3: Extract fixtures
echo "Step 3: Extracting fixture inventory..."
fixtures_json=$(ai_extract_fixtures "$WORKSPACE")
fixture_count=$(echo "$fixtures_json" | jq '.fixtures | length')
echo "✓ Found $fixture_count fixtures:"
echo "$fixtures_json" | jq -r '.fixtures[] | "  - [\(.id)] \(.name | gsub("\n";"")) (\(.channels) ch)"'
echo ""

# Step 4: Generate a new scene
echo "Step 4: Generating new scene..."
description="AI Generated Warm Sunset"
style="complete"
echo "   Description: $description"
echo "   Style: $style"

scene_xml=$(ai_generate_mock_scene "$description" "$style" "$fixtures_json")
echo "✓ Scene generated"
echo ""

# Step 5: Save scene to file
echo "Step 5: Saving scene to file..."
scene_file="${PROJECT_ROOT}/scenes/examples/test-workflow-scene.xml"
echo "$scene_xml" > "$scene_file"
echo "✓ Saved to: $scene_file"
echo ""

# Step 6: Create test workspace copy
echo "Step 6: Creating test workspace copy..."
cp "$WORKSPACE" "$TEST_WORKSPACE"
echo "✓ Created: $TEST_WORKSPACE"
echo ""

# Step 7: Inject scene into workspace
echo "Step 7: Injecting scene into workspace..."
if workspace_inject_scene "$TEST_WORKSPACE" "$scene_xml" "$TEST_WORKSPACE"; then
  echo "✓ Scene injected successfully"
else
  echo "✗ Scene injection failed"
  exit 1
fi
echo ""

# Step 8: Validate modified workspace
echo "Step 8: Validating modified workspace..."
if ! workspace_validate "$TEST_WORKSPACE"; then
  echo "✗ Modified workspace is invalid"
  exit 1
fi
echo "✓ Modified workspace is valid"
echo ""

# Step 9: List scenes in modified workspace
echo "Step 9: Listing scenes in modified workspace..."
workspace_list_scenes "$TEST_WORKSPACE"
echo ""

# Step 10: Compare scene counts
original_count=$(xmllint --xpath "count(//*[local-name()='Function'][@Type='Scene'])" "$WORKSPACE" 2>/dev/null)
modified_count=$(xmllint --xpath "count(//*[local-name()='Function'][@Type='Scene'])" "$TEST_WORKSPACE" 2>/dev/null)

echo "Step 10: Verification..."
echo "   Original scene count: $original_count"
echo "   Modified scene count: $modified_count"
echo "   Difference: $((modified_count - original_count))"

if [[ $((modified_count - original_count)) -eq 1 ]]; then
  echo "✓ Scene count increased by 1"
else
  echo "✗ Unexpected scene count change"
  exit 1
fi
echo ""

echo "========================================="
echo "✓ Full workflow test PASSED!"
echo ""
echo "Test workspace created: $TEST_WORKSPACE"
echo "You can deploy this to your Pi with:"
echo "  ./lightsctl.sh deploy-workspace $TEST_WORKSPACE"
echo ""
echo "To clean up test files:"
echo "  rm $TEST_WORKSPACE"
echo "  rm $scene_file"
