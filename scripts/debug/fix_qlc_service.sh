#!/usr/bin/env bash
# fix_qlc_service.sh
#
# Pushes an updated qlcplus-web.service to the Pi and restarts it.
# Fixes intermittent workspace-not-loading on reboot by:
#   1. Adding ExecStartPre sleep so USB DMX has time to enumerate
#   2. Switching Restart=always → Restart=on-failure (avoids restart loops)
#   3. Increasing RestartSec to 5s
#   4. Adding local-fs.target dependency
#
# Usage (run from repo root on your Mac):
#   PI_USER=riversway PI_HOST=lights.local bash scripts/debug/fix_qlc_service.sh

PI_USER="${PI_USER:-riversway}"
PI_HOST="${PI_HOST:-lights.local}"
QLC_PORT="${QLC_PORT:-9999}"

SERVICE_CONTENT="[Unit]
Description=QLC+ Headless Web Interface
After=network-online.target local-fs.target
Wants=network-online.target
After=dev-bus-usb.device
StartLimitIntervalSec=120
StartLimitBurst=5

[Service]
Type=simple
User=${PI_USER}
Environment=HOME=/home/${PI_USER}
Environment=QT_QPA_PLATFORM=minimal
Environment=XDG_RUNTIME_DIR=/run/qlcplus
RuntimeDirectory=qlcplus
RuntimeDirectoryMode=0700
WorkingDirectory=/home/${PI_USER}
ExecStartPre=/bin/sleep 3
ExecStart=/usr/bin/qlcplus --nogui --web --web-port ${QLC_PORT} --open /home/${PI_USER}/.qlcplus/default.qxw
Restart=on-failure
RestartSec=5
SuccessExitStatus=0

[Install]
WantedBy=multi-user.target"

echo "=== Updating qlcplus-web.service on ${PI_USER}@${PI_HOST} ==="

# Write service file and ensure autostart.qxw symlink exists
ssh "${PI_USER}@${PI_HOST}" "
  echo '${SERVICE_CONTENT}' | sudo tee /etc/systemd/system/qlcplus-web.service > /dev/null

  # Ensure autostart.qxw is a real file (not symlink) — QLC+ 4.14.1 ignores symlinks
  QLCDIR=\"\$HOME/.qlcplus\"
  if [ ! -e \"\$QLCDIR/autostart.qxw\" ] && [ -f \"\$QLCDIR/default.qxw\" ]; then
    cp \"\$QLCDIR/default.qxw\" \"\$QLCDIR/autostart.qxw\"
    echo '✓ Created autostart.qxw (real copy)'
  else
    echo '✓ autostart.qxw already present'
  fi

  sudo systemctl daemon-reload
  sudo systemctl restart qlcplus-web.service
  sleep 4
  systemctl is-active qlcplus-web.service && echo '✓ QLC+ service is active' || echo '✗ QLC+ service failed'
  systemctl status qlcplus-web.service --no-pager -l | tail -15

  echo ''
  echo '--- Restarting control server ---'
  sudo systemctl restart lighting-control.service
  sleep 2
  systemctl is-active lighting-control.service && echo '✓ Control server is active' || echo '✗ Control server failed'
"
