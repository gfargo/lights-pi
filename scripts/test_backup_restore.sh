#!/usr/bin/env bash
# Smoke test for the rclone restore-fetch path (OSS-880 / gfargo/lights-pi#70).
# Exercises _fetch_from_remote() against a local-filesystem rclone remote —
# no credentials or network required.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/backup.sh
source "${SCRIPT_DIR}/scripts/lib/backup.sh"

echo "Testing rclone restore fetch path"
echo "=================================="
echo ""

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT

echo "Test 1: fetch an existing file via rclone:"
echo "-------------------------------------------"
src_dir="${workdir}/remote"
mkdir -p "$src_dir"
fixture="${src_dir}/qlcplus-backup-20260101T000000Z.tar.gz"
echo "fake backup contents" >"$fixture"

fetched="$(_fetch_from_remote "rclone:${fixture}")"

if [[ -f "$fetched" ]]; then
  echo "✓ Test 1 passed: fetched file exists at ${fetched}"
else
  echo "✗ Test 1 failed: expected local file at ${fetched}, not found"
  exit 1
fi

if [[ "$(basename "$fetched")" == "restore-$(basename "$fixture")" ]]; then
  echo "✓ Fetched file uses the expected restore- prefixed basename"
else
  echo "✗ Fetched file has unexpected name: $(basename "$fetched")"
  exit 1
fi

if cmp -s "$fixture" "$fetched"; then
  echo "✓ Fetched file content matches source"
else
  echo "✗ Fetched file content does not match source"
  exit 1
fi

rm -f "$fetched"

echo ""
echo "Test 2: fetch a nonexistent remote file surfaces as missing"
echo "-------------------------------------------------------------"
missing_fixture="${src_dir}/does-not-exist.tar.gz"
set +e
fetched_missing="$(_fetch_from_remote "rclone:${missing_fixture}" 2>/dev/null)"
fetch_rc=$?
set -e

if [[ $fetch_rc -ne 0 || ! -f "$fetched_missing" ]]; then
  echo "✓ Test 2 passed: missing remote file correctly not present locally"
else
  echo "✗ Test 2 failed: expected no local file for a nonexistent remote source"
  exit 1
fi

echo ""
echo "================================="
echo "All backup-restore tests passed!"
