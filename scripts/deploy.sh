#!/usr/bin/env bash
# deploy.sh — sync latest code to Pi and restart services
#
# Usage (from repo root on your Mac):
#   bash scripts/deploy.sh
#
# Or with explicit host:
#   PI_HOST=192.168.1.50 bash scripts/deploy.sh

set -euo pipefail

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

PI_USER="${PI_USER:-riversway}"
PI_HOST="${PI_HOST:-lights.local}"
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
  control-server/ "${PI_USER}@${PI_HOST}:${PI_HOME}/control-server/"

rsync -avz --delete -e "$RSYNC_RSH" \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  scripts/ "${PI_USER}@${PI_HOST}:${PI_HOME}/scripts/"

rsync -avz -e "$RSYNC_RSH" lightsctl.sh "${PI_USER}@${PI_HOST}:${PI_HOME}/lightsctl.sh"

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
