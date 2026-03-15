#!/usr/bin/env bash
# deploy.sh — pull latest code on Pi and restart services
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

ssh "${PI_USER}@${PI_HOST}" "
  set -e
  cd ${PI_HOME}
  echo '--- Pulling latest code ---'
  git pull

  echo ''
  echo '--- Restarting control server ---'
  sudo systemctl restart lighting-control.service
  sleep 2
  systemctl is-active lighting-control.service && echo '✓ lighting-control.service active' || echo '✗ lighting-control.service failed'
"

echo ""
echo "=== Done ==="
echo "Control server: http://${PI_HOST}:5000"
echo "QLC+ web:       http://${PI_HOST}:9999"
