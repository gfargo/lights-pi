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
"${SSH_CMD[@]}" "${PI_USER}@${PI_HOST}" "
  sudo systemctl restart lighting-control.service
  sleep 2
  systemctl is-active lighting-control.service && echo '✓ lighting-control.service active' || echo '✗ lighting-control.service failed'
"

echo ""
echo "=== Done ==="
echo "Control server: http://${PI_HOST}:5000"
echo "QLC+ web:       http://${PI_HOST}:9999"
