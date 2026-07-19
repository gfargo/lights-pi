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
echo "Test 3: s3 cp completion output does not leak into the returned path"
echo "----------------------------------------------------------------------"
fakebin="${workdir}/fakebin"
mkdir -p "$fakebin"
# Stand-in for `aws` that mimics `aws s3 cp`'s stdout completion line
# (real aws prints "download: s3://... to /tmp/..." unless --quiet is passed).
cat >"${fakebin}/aws" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
src="$3"
dest="$4"
cp "${src#s3://}" "$dest"
echo "download: ${src} to ${dest}"
EOF
chmod +x "${fakebin}/aws"

s3_fixture="${src_dir}/qlcplus-backup-20260102T000000Z.tar.gz"
echo "fake s3 backup contents" >"$s3_fixture"

fetched_s3="$(PATH="${fakebin}:${PATH}" _fetch_from_remote "s3://${s3_fixture}")"

if [[ "$fetched_s3" == *$'\n'* ]]; then
  echo "✗ Test 3 failed: returned path contains embedded stdout noise: ${fetched_s3}"
  exit 1
fi

if [[ -f "$fetched_s3" ]]; then
  echo "✓ Test 3 passed: s3 completion output did not leak; file exists at ${fetched_s3}"
else
  echo "✗ Test 3 failed: expected local file at ${fetched_s3}, not found"
  exit 1
fi

echo ""
echo "Test 4: scp completion output does not leak into the returned path"
echo "----------------------------------------------------------------------"
# Stand-in for `scp` that mimics real scp's SRC DEST arg order and prints a
# noisy completion line to stdout (real scp can print progress/status lines).
cat >"${fakebin}/scp" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
src="$1"
dest="$2"
cp "${src##*:}" "$dest"
echo "${dest##*/}  100%   done"
EOF
chmod +x "${fakebin}/scp"

scp_fixture="${src_dir}/qlcplus-backup-20260103T000000Z.tar.gz"
echo "fake scp backup contents" >"$scp_fixture"

fetched_scp="$(PATH="${fakebin}:${PATH}" _fetch_from_remote "testuser@testhost:${scp_fixture}")"

if [[ "$fetched_scp" == *$'\n'* ]]; then
  echo "✗ Test 4 failed: returned path contains embedded stdout noise: ${fetched_scp}"
  exit 1
fi

if [[ -f "$fetched_scp" ]]; then
  echo "✓ Test 4 passed: scp completion output did not leak; file exists at ${fetched_scp}"
else
  echo "✗ Test 4 failed: expected local file at ${fetched_scp}, not found"
  exit 1
fi

if [[ "$(basename "$fetched_scp")" == "restore-$(basename "$scp_fixture")" ]]; then
  echo "✓ Fetched file uses the expected restore- prefixed basename"
else
  echo "✗ Fetched file has unexpected name: $(basename "$fetched_scp")"
  exit 1
fi

if cmp -s "$scp_fixture" "$fetched_scp"; then
  echo "✓ Fetched file content matches source"
else
  echo "✗ Fetched file content does not match source"
  exit 1
fi

echo ""
echo "================================="
echo "All backup-restore tests passed!"
