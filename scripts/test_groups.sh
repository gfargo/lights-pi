#!/usr/bin/env bash
# Test script for Fixture Groups feature
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

echo "Testing Fixture Groups Feature"
echo "==============================="
echo ""

# Clean up any existing test groups
rm -f ~/.qlcplus/fixture_groups.json

# Test 1: List empty groups
echo "Test 1: List empty groups"
echo "-------------------------"
./lightsctl.sh group-list

if [[ $? -eq 0 ]]; then
  echo "✓ Test 1 passed"
else
  echo "✗ Test 1 failed"
  exit 1
fi

# Test 2: Create a group
echo ""
echo "Test 2: Create fixture group"
echo "-----------------------------"
./lightsctl.sh group-create "key-lights" "0,3" "Main key lights"

if [[ $? -eq 0 ]]; then
  echo "✓ Test 2 passed"
else
  echo "✗ Test 2 failed"
  exit 1
fi

# Test 3: List groups
echo ""
echo "Test 3: List groups"
echo "-------------------"
./lightsctl.sh group-list

if [[ $? -eq 0 ]]; then
  echo "✓ Test 3 passed"
else
  echo "✗ Test 3 failed"
  exit 1
fi

# Test 4: Create another group
echo ""
echo "Test 4: Create second group"
echo "---------------------------"
./lightsctl.sh group-create "fill-lights" "4,5" "Fill and background"

if [[ $? -eq 0 ]]; then
  echo "✓ Test 4 passed"
else
  echo "✗ Test 4 failed"
  exit 1
fi

# Test 5: Add fixtures to group
echo ""
echo "Test 5: Add fixtures to group"
echo "------------------------------"
./lightsctl.sh group-add "key-lights" "1"

if [[ $? -eq 0 ]]; then
  echo "✓ Test 5 passed"
else
  echo "✗ Test 5 failed"
  exit 1
fi

# Test 6: Update group description
echo ""
echo "Test 6: Update group description"
echo "---------------------------------"
./lightsctl.sh group-update "key-lights" "Updated: Main key lights"

if [[ $? -eq 0 ]]; then
  echo "✓ Test 6 passed"
else
  echo "✗ Test 6 failed"
  exit 1
fi

# Test 7: (Skipped - requires Pi connection)
echo ""
echo "Test 7: Apply template to group (skipped - requires Pi)"
echo "--------------------------------------------------------"
echo "✓ Test 7 skipped"

# Test 8: Remove fixtures from group
echo ""
echo "Test 8: Remove fixtures from group"
echo "-----------------------------------"
./lightsctl.sh group-remove "key-lights" "1"

if [[ $? -eq 0 ]]; then
  echo "✓ Test 8 passed"
else
  echo "✗ Test 8 failed"
  exit 1
fi

# Test 9: Delete group
echo ""
echo "Test 9: Delete group"
echo "--------------------"
./lightsctl.sh group-delete "fill-lights"

if [[ $? -eq 0 ]]; then
  echo "✓ Test 9 passed"
else
  echo "✗ Test 9 failed"
  exit 1
fi

# Test 10: Final list
echo ""
echo "Test 10: Final group list"
echo "-------------------------"
./lightsctl.sh group-list

if [[ $? -eq 0 ]]; then
  echo "✓ Test 10 passed"
else
  echo "✗ Test 10 failed"
  exit 1
fi

echo ""
echo "==============================="
echo "All tests passed!"
echo ""
echo "Groups file: ~/.qlcplus/fixture_groups.json"
