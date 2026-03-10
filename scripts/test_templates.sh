#!/usr/bin/env bash
# Test script for Scene Templates feature
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

echo "Testing Scene Templates Feature"
echo "================================"
echo ""

# Test 1: List templates
echo "Test 1: List available templates"
echo "---------------------------------"
./lightsctl.sh list-templates

if [[ $? -eq 0 ]]; then
  echo "✓ Test 1 passed: Templates listed successfully"
else
  echo "✗ Test 1 failed: Failed to list templates"
  exit 1
fi

echo ""
echo "Test 2: Generate YouTube Studio template"
echo "-----------------------------------------"
./lightsctl.sh generate-from-template youtube-studio \
  --preview \
  --workspace RiversWayStudio.qxw

if [[ $? -eq 0 ]]; then
  echo "✓ Test 2 passed: YouTube Studio template generated"
else
  echo "✗ Test 2 failed: Template generation failed"
  exit 1
fi

echo ""
echo "Test 3: Generate Party template"
echo "--------------------------------"
./lightsctl.sh generate-from-template party \
  --preview \
  --workspace RiversWayStudio.qxw

if [[ $? -eq 0 ]]; then
  echo "✓ Test 3 passed: Party template generated"
else
  echo "✗ Test 3 failed: Template generation failed"
  exit 1
fi

echo ""
echo "Test 4: Generate Ambient template"
echo "----------------------------------"
./lightsctl.sh generate-from-template ambient \
  --preview \
  --workspace RiversWayStudio.qxw

if [[ $? -eq 0 ]]; then
  echo "✓ Test 4 passed: Ambient template generated"
else
  echo "✗ Test 4 failed: Template generation failed"
  exit 1
fi

echo ""
echo "Test 5: Generate Warm White template"
echo "-------------------------------------"
./lightsctl.sh generate-from-template warm-white \
  --preview \
  --workspace RiversWayStudio.qxw

if [[ $? -eq 0 ]]; then
  echo "✓ Test 5 passed: Warm White template generated"
else
  echo "✗ Test 5 failed: Template generation failed"
  exit 1
fi

echo ""
echo "Test 6: Save template to file"
echo "------------------------------"
./lightsctl.sh generate-from-template cool-white \
  --output /tmp/test-template.xml \
  --workspace RiversWayStudio.qxw

if [[ -f /tmp/test-template.xml ]]; then
  echo "✓ Test 6 passed: Template saved to file"
  
  # Validate XML
  if xmllint --noout /tmp/test-template.xml 2>/dev/null; then
    echo "✓ XML validation passed"
  else
    echo "✗ XML validation failed"
    exit 1
  fi
  
  rm -f /tmp/test-template.xml
else
  echo "✗ Test 6 failed: File not created"
  exit 1
fi

echo ""
echo "Test 7: Test invalid template name"
echo "-----------------------------------"
set +e
output=$(./lightsctl.sh generate-from-template invalid-template --workspace RiversWayStudio.qxw 2>&1)
exit_code=$?
set -e

if [[ $exit_code -ne 0 ]] && echo "$output" | grep -q "Unknown template"; then
  echo "✓ Test 7 passed: Invalid template properly rejected"
else
  echo "✗ Test 7 failed: Should reject invalid template (exit code: $exit_code)"
  exit 1
fi

echo ""
echo "================================="
echo "All tests passed!"
echo ""
echo "Templates tested:"
echo "  - youtube-studio"
echo "  - party"
echo "  - ambient"
echo "  - warm-white"
echo "  - cool-white"
echo ""
echo "Remaining templates (not tested):"
echo "  - spotlight"
echo "  - work-light"
