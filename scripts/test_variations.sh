#!/usr/bin/env bash
# Test script for Scene Variations feature
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

echo "Testing Scene Variations Feature"
echo "================================="
echo ""

# Test 1: Generate variations with mock (auto-select first)
echo "Test 1: Generate 3 variations with mock mode"
echo "---------------------------------------------"
echo "1" | ./lightsctl.sh generate-scene "warm sunset" \
  --variations 3 \
  --mock \
  --preview \
  --workspace RiversWayStudio.qxw

if [[ $? -eq 0 ]]; then
  echo "✓ Test 1 passed: Variations generated successfully"
else
  echo "✗ Test 1 failed: Variation generation failed"
  exit 1
fi

echo ""
echo "Test 2: Generate single scene (baseline)"
echo "-----------------------------------------"
./lightsctl.sh generate-scene "cool blue ambient" \
  --mock \
  --preview \
  --workspace RiversWayStudio.qxw

if [[ $? -eq 0 ]]; then
  echo "✓ Test 2 passed: Single scene generated successfully"
else
  echo "✗ Test 2 failed: Single scene generation failed"
  exit 1
fi

echo ""
echo "Test 3: Save variation to file"
echo "-------------------------------"
echo "1" | ./lightsctl.sh generate-scene "party lights" \
  --variations 2 \
  --mock \
  --output /tmp/test-variation.xml \
  --workspace RiversWayStudio.qxw

if [[ -f /tmp/test-variation.xml ]]; then
  echo "✓ Test 3 passed: Variation saved to file"
  rm -f /tmp/test-variation.xml
else
  echo "✗ Test 3 failed: File not created"
  exit 1
fi

echo ""
echo "================================="
echo "All tests passed!"
echo ""
echo "Note: Interactive selection requires manual testing with fzf"
