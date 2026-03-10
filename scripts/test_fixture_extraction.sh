#!/usr/bin/env bash
# Test fixture extraction from QLC+ workspace

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Source the AI library
source "${SCRIPT_DIR}/lib/ai_scene.sh"

# Test with RiversWayStudio.qxw
WORKSPACE="${PROJECT_ROOT}/RiversWayStudio.qxw"

if [[ ! -f "$WORKSPACE" ]]; then
  echo "Error: Workspace file not found: $WORKSPACE"
  exit 1
fi

echo "Testing fixture extraction from: $WORKSPACE"
echo "================================================"
echo ""

fixtures_json=$(ai_extract_fixtures "$WORKSPACE")

echo "Extracted Fixtures:"
echo "$fixtures_json" | jq .

echo ""
echo "================================================"
echo "Fixture Summary:"
echo "$fixtures_json" | jq -r '.fixtures[] | "\(.id): \(.name) - \(.manufacturer) \(.model) (\(.channels) channels)"'
