#!/usr/bin/env bash
# DMX output & system health monitor — long-running observability service.
#
# Installs scripts/services/dmx-monitor.py on the Pi as a systemd service
# that continuously polls channel_values (1s) and system diagnostics
# (15s), logging only changes/snapshots to the journal. Lets us look back
# over hours/days after something like an intermittent flicker to see
# whether QLC+'s own output moved, or system/USB state changed, at the
# time it happened.

# Install the monitor
function dmx_monitor_install() {
  local script="${SCRIPT_DIR}/scripts/services/dmx-monitor.py"
  if [[ ! -f "$script" ]]; then
    echo "Error: dmx-monitor.py not found at ${script}" >&2
    return 1
  fi

  echo "Installing DMX monitor on ${PI_HOST}..."

  "${SCP_CMD[@]}" "$script" "${PI_USER}@${PI_HOST}:/tmp/dmx-monitor.py"
  run_sudo mkdir -p /usr/local/bin
  run_sudo mv /tmp/dmx-monitor.py /usr/local/bin/dmx-monitor.py
  run_sudo chmod +x /usr/local/bin/dmx-monitor.py

  run_sudo tee /etc/systemd/system/dmx-monitor.service >/dev/null <<EOF
[Unit]
Description=DMX Output & System Health Monitor
After=network.target lighting-control.service
Wants=lighting-control.service

[Service]
Type=simple
User=${PI_USER}
ExecStart=/usr/bin/python3 /usr/local/bin/dmx-monitor.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  run_sudo systemctl daemon-reload
  run_sudo systemctl enable dmx-monitor.service
  # restart (not enable --now): picks up a new script when already running
  run_sudo systemctl restart dmx-monitor.service

  echo ""
  echo "✓ DMX monitor installed and running"
  echo "  Polls channel_values every 1s (logs only changes)"
  echo "  Polls system diagnostics every 15s (always logs a snapshot)"
  echo ""
  echo "  Status:  ./lightsctl.sh dmx-monitor-status"
  echo "  Logs:    ./lightsctl.sh dmx-monitor-logs"
  echo "  Remove:  ./lightsctl.sh dmx-monitor-uninstall"
}

# Show monitor status
function dmx_monitor_status() {
  echo "=== DMX Monitor Status ==="
  run_sudo systemctl status dmx-monitor.service --no-pager 2>/dev/null || echo "Not installed"
}

# Show monitor logs
function dmx_monitor_logs() {
  local n="${1:-100}"
  run_sudo journalctl -u dmx-monitor.service -n "$n" --no-pager
}

# Uninstall the monitor
function dmx_monitor_uninstall() {
  echo "Removing DMX monitor..."
  run_sudo systemctl disable --now dmx-monitor.service 2>/dev/null || true
  run_sudo rm -f /etc/systemd/system/dmx-monitor.service /usr/local/bin/dmx-monitor.py
  run_sudo systemctl daemon-reload
  echo "✓ DMX monitor removed"
}

export -f dmx_monitor_install
export -f dmx_monitor_status
export -f dmx_monitor_logs
export -f dmx_monitor_uninstall
