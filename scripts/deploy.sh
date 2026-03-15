#!/usr/bin/env bash
# deploy.sh — sync latest code to Pi and restart services
#
# Usage (from repo root on your Mac):
#   bash scripts/deploy.sh
#
# Or with explicit host:
#   PI_HOST=192.168.1.50 bash scripts/deploy.sh

set -euo pipefail

PI_USER="${PI_USER:-riversway}"
PI_HOST="${PI_HOST:-lights.local}"
PI_HOME="/home/${PI_USER}"

echo "=== Deploying to ${PI_USER}@${PI_HOST} ==="

echo "--- Syncing files ---"
# Sync control-server and scripts; exclude venv, cache, .git
rsync -avz --delete \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.git' \
  control-server/ "${PI_USER}@${PI_HOST}:${PI_HOME}/control-server/"

rsync -avz --delete \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  scripts/ "${PI_USER}@${PI_HOST}:${PI_HOME}/scripts/"

rsync -avz lightsctl.sh "${PI_USER}@${PI_HOST}:${PI_HOME}/lightsctl.sh"

echo ""
echo "--- Restarting control server ---"
ssh "${PI_USER}@${PI_HOST}" "
  sudo systemctl restart lighting-control.service
  sleep 2
  systemctl is-active lighting-control.service && echo '✓ lighting-control.service active' || echo '✗ lighting-control.service failed'
"

echo ""
echo "=== Done ==="
echo "Control server: http://${PI_HOST}:5000"
echo "QLC+ web:       http://${PI_HOST}:9999"
