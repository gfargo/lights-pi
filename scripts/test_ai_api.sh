#!/usr/bin/env bash
# Test real AI API integration (requires AI_API_KEY in .env)
# Tests: OpenAI scene generation, modular style, output saving, XML validation

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LIGHTSCTL="${PROJECT_ROOT}/lightsctl.sh"
WORKSPACE="${PROJECT_ROOT}/RiversWayStudio.qxw"

passed=0
failed=0
errors=()

function test_pass() {
  echo "  ✓ $1"
  passed=$((passed + 1))
}

function test_fail() {
  echo "  ✗ $1"
  failed=$((failed + 1))
  errors+=("$1")
}

echo "AI API Integration Tests"
echo "========================"
echo ""

# Pre-flight: check env
source "${PROJECT_ROOT}/.env" 2>/dev/null || true
if [[ -z "${AI_API_KEY:-}" ]]; then
  echo "Error: AI_API_KEY not set in .env — cannot run API tests"
  exit 1
fi
echo "Provider: ${AI_PROVIDER:-openai}"
echo "Model: ${AI_MODEL:-gpt-4.1}"
echo "Workspace: ${WORKSPACE}"
echo ""

# ── Test 1: Complete style scene generation ──
echo "1. Complete style — generate-scene --preview"
output=$(bash "$LIGHTSCTL" generate-scene "warm amber glow" --preview --workspace "$WORKSPACE" 2>&1)
if echo "$output" | grep -q '<Function.*Type="Scene"'; then
  test_pass "Complete style returns valid Scene XML"
else
  test_fail "Complete style did not return Scene XML"
fi

if echo "$output" | grep -q 'FixtureVal ID='; then
  test_pass "Complete style includes FixtureVal elements"
else
  test_fail "Complete style missing FixtureVal elements"
fi

# ── Test 2: Modular style ──
echo ""
echo "2. Modular style — generate-scene --style modular --preview"
output=$(bash "$LIGHTSCTL" generate-scene "cool blue ambient" --style modular --preview --workspace "$WORKSPACE" 2>&1)
if echo "$output" | grep -q '<Function'; then
  test_pass "Modular style returns Function XML"
else
  test_fail "Modular style did not return Function XML"
fi

# ── Test 3: Save to file ──
echo ""
echo "3. Save to file — generate-scene --output"
tmpfile=$(mktemp /tmp/lightsctl-test-XXXXXX.xml)
bash "$LIGHTSCTL" generate-scene "dramatic red" --output "$tmpfile" --workspace "$WORKSPACE" 2>&1
if [[ -s "$tmpfile" ]]; then
  test_pass "Output file created and non-empty"
else
  test_fail "Output file missing or empty"
fi

if xmllint --noout "$tmpfile" 2>/dev/null; then
  test_pass "Output file is valid XML"
else
  test_fail "Output file is not valid XML"
fi
rm -f "$tmpfile"

# ── Test 4: Template generation (no API call) ──
echo ""
echo "4. Template generation — generate-from-template"
output=$(bash "$LIGHTSCTL" generate-from-template warm-white --preview --workspace "$WORKSPACE" 2>&1)
if echo "$output" | grep -q '<Function.*Name="Warm White"'; then
  test_pass "Template generates named scene"
else
  test_fail "Template did not generate expected scene"
fi

# ── Test 5: Mock generation still works ──
echo ""
echo "5. Mock generation — generate-scene --mock"
output=$(bash "$LIGHTSCTL" generate-scene "purple haze" --mock --preview --workspace "$WORKSPACE" 2>&1)
if echo "$output" | grep -q '<Function.*Type="Scene"'; then
  test_pass "Mock generation still works"
else
  test_fail "Mock generation broken"
fi

# ── Test 6: Fixture extraction ──
echo ""
echo "6. Fixture extraction"
source "${SCRIPT_DIR}/lib/ai_scene.sh"
fixtures=$(ai_extract_fixtures "$WORKSPACE")
count=$(echo "$fixtures" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('fixtures',[])))")
if [[ "$count" -gt 0 ]]; then
  test_pass "Extracted $count fixtures from workspace"
else
  test_fail "No fixtures extracted"
fi

# ── Test 7: Config validation ──
echo ""
echo "7. Config validation"
if ai_validate_config 2>/dev/null; then
  test_pass "AI config validates successfully"
else
  test_fail "AI config validation failed"
fi

# ── Summary ──
echo ""
echo "========================"
echo "Results: $passed passed, $failed failed"
if [[ $failed -gt 0 ]]; then
  echo ""
  echo "Failures:"
  for e in "${errors[@]}"; do
    echo "  - $e"
  done
  exit 1
fi
echo "All tests passed."
