#!/usr/bin/env bash
# deploy.sh — sync latest code to Pi and restart services
#
# Usage (from repo root on your Mac):
#   bash scripts/deploy.sh
#
# Or with explicit host:
#   PI_HOST=192.168.1.50 bash scripts/deploy.sh

set -euo pipefail

# Save any explicitly-passed PI_HOST before sourcing .env
_EXPLICIT_PI_HOST="${PI_HOST:-}"

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

# Explicit CLI override takes priority over .env
PI_USER="${PI_USER:-riversway}"
if [ -n "$_EXPLICIT_PI_HOST" ]; then
  PI_HOST="$_EXPLICIT_PI_HOST"
else
  PI_HOST="${PI_HOST:-${TAILSCALE_HOST:-lights.local}}"
fi
PI_HOME="/home/${PI_USER}"

SSH_OPTIONS=()
if [ -n "${SSH_KEY:-}" ]; then
  SSH_OPTIONS+=("-i" "$SSH_KEY" "-o" "IdentitiesOnly=yes")
fi
SSH_CMD=(ssh "${SSH_OPTIONS[@]}")
RSYNC_RSH="ssh ${SSH_OPTIONS[*]}"

echo "=== Deploying to ${PI_USER}@${PI_HOST} ==="

echo "--- Syncing files ---"
# Sync control-server and scripts; exclude venv, cache, .git, and .env
# .env lives on the Pi only (contains secrets) — never overwrite it
rsync -avz --delete -e "$RSYNC_RSH" \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.git' \
  --exclude='.env' \
  --exclude='.pytest_cache' \
  --exclude='.ruff_cache' \
  --exclude='static/logo.*' \
  --exclude='static/theme.css' \
  control-server/ "${PI_USER}@${PI_HOST}:${PI_HOME}/control-server/"

rsync -avz --delete -e "$RSYNC_RSH" \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  scripts/ "${PI_USER}@${PI_HOST}:${PI_HOME}/scripts/"

rsync -avz -e "$RSYNC_RSH" lightsctl.sh "${PI_USER}@${PI_HOST}:${PI_HOME}/lightsctl.sh"

echo ""
echo "--- Syncing Python dependencies ---"
# requirements.txt is part of the control-server/ sync above, but nothing
# actually installs it — a deploy that changes/adds a dependency (e.g. a new
# feature's import) would otherwise crash-loop the service on restart with
# no rsync-visible warning. pip install is idempotent/fast when nothing
# changed, so this runs on every deploy rather than only on first install.
"${SSH_CMD[@]}" "${PI_USER}@${PI_HOST}" "
  ~/control-server-venv/bin/pip install -q -r ${PI_HOME}/control-server/requirements.txt
"

echo ""
echo "--- Restarting control server ---"
# `systemctl is-active` 2s after restart says "active" even when the process
# is about to die on an import error (it took ~4s to crash on 2026-09-19).
# Poll /healthz instead and fail the deploy loudly if it never answers.
"${SSH_CMD[@]}" "${PI_USER}@${PI_HOST}" "
  sudo systemctl restart lighting-control.service
  for i in \$(seq 1 20); do
    # 503 is fine here: it means Flask is up but a subsystem (QLC+ ws) is red,
    # which is a rig problem, not a deploy failure. Only no-answer is a failure.
    code=\$(curl -s -o /tmp/healthz.json --max-time 3 -w '%{http_code}' http://localhost:5000/healthz 2>/dev/null || true)
    if [ \"\$code\" = 200 ] || [ \"\$code\" = 503 ]; then
      echo \"✓ lighting-control.service serving (/healthz \$code after \${i}s): \$(cat /tmp/healthz.json)\"
      exit 0
    fi
    sleep 1
  done
  echo '✗ lighting-control.service did not become healthy within 20s'
  echo \"   state: \$(systemctl is-active lighting-control.service)\"
  journalctl -u lighting-control.service --since '30 sec ago' --no-pager | grep -v midi-listener | tail -25
  exit 1
"

echo ""
echo "--- MCP server ---"
# Only on Pis where it was installed (./lightsctl.sh mcp-install, which also
# writes the unit and the venv). ~/mcp-server/.env holds its secrets and is
# never overwritten or deleted.
if "${SSH_CMD[@]}" "${PI_USER}@${PI_HOST}" "systemctl cat lighting-mcp.service >/dev/null 2>&1"; then
  rsync -avz --delete -e "$RSYNC_RSH" \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.env' \
    mcp-server/ "${PI_USER}@${PI_HOST}:${PI_HOME}/mcp-server/"
  "${SSH_CMD[@]}" "${PI_USER}@${PI_HOST}" "
    ~/mcp-server-venv/bin/pip install -q --disable-pip-version-check -r ${PI_HOME}/mcp-server/requirements.txt
    sudo systemctl restart lighting-mcp.service
    for i in \$(seq 1 30); do
      # Any HTTP answer means uvicorn is up (GET /mcp without a session is a 4xx).
      code=\$(curl -s -o /dev/null --max-time 3 -w '%{http_code}' http://localhost:5001/mcp 2>/dev/null || true)
      if [ \"\$code\" != 000 ] && [ -n \"\$code\" ]; then
        echo \"✓ lighting-mcp.service answering on :5001 (HTTP \$code after \${i}s)\"
        exit 0
      fi
      sleep 1
    done
    echo '✗ lighting-mcp.service did not answer on :5001 within 30s'
    echo \"   state: \$(systemctl is-active lighting-mcp.service)\"
    journalctl -u lighting-mcp.service --since '45 sec ago' --no-pager | tail -25
    exit 1
  "
else
  echo "Not installed on this Pi; skipping (./lightsctl.sh mcp-install to add it)."
fi

echo ""
echo "=== Done ==="
echo "Control server: http://${PI_HOST}:5000"
echo "QLC+ web:       http://${PI_HOST}:9999"
