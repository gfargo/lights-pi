#!/usr/bin/env bash
# Ensures the QLC+ headless service picks a minimal Qt platform plugin so it can run without X11.
set -euo pipefail

PLATFORM="${PLATFORM:-minimal}"
SERVICE_DIR="/etc/systemd/system/qlcplus-web.service.d"
OVERRIDE_FILE="${SERVICE_DIR}/override.conf"

cat <<EOF
Setting QT_QPA_PLATFORM=${PLATFORM} for qlcplus-web.service
EOF

sudo mkdir -p "${SERVICE_DIR}"
cat <<EOF | sudo tee "${OVERRIDE_FILE}" >/dev/null
[Service]
Environment=QT_QPA_PLATFORM=${PLATFORM}
EOF

sudo systemctl daemon-reload
sudo systemctl restart qlcplus-web.service
sudo systemctl status qlcplus-web.service --no-pager
