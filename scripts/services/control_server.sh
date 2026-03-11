#!/usr/bin/env bash
# Install and configure the Natural Language Control Server

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONTROL_DIR="${SCRIPT_DIR}/control-server"
SERVICE_NAME="lighting-control.service"
CONTROL_PORT="${CONTROL_PORT:-5000}"

function install_control_server() {
  echo "Installing Natural Language Control Server..."
  
  # Install Python dependencies
  echo "Installing Python dependencies..."
  if command -v pip3 >/dev/null 2>&1; then
    pip3 install -r "${CONTROL_DIR}/requirements.txt"
  else
    echo "Error: pip3 not found. Install Python 3 and pip first." >&2
    return 1
  fi
  
  # Create systemd service
  echo "Creating systemd service..."
  cat > /tmp/${SERVICE_NAME} <<EOF
[Unit]
Description=Lighting Control Server
After=network.target qlcplus-web.service

[Service]
Type=simple
User=${USER}
WorkingDirectory=${CONTROL_DIR}
Environment="PATH=/usr/local/bin:/usr/bin:/bin"
Environment="CONTROL_PORT=${CONTROL_PORT}"
EnvironmentFile=${SCRIPT_DIR}/.env
ExecStart=/usr/bin/python3 ${CONTROL_DIR}/app.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

  sudo mv /tmp/${SERVICE_NAME} /etc/systemd/system/${SERVICE_NAME}
  sudo systemctl daemon-reload
  sudo systemctl enable ${SERVICE_NAME}
  sudo systemctl start ${SERVICE_NAME}
  
  echo "✓ Control server installed and started"
  echo ""
  echo "Access at: http://$(hostname -I | awk '{print $1}'):${CONTROL_PORT}"
  echo "Or: http://lights.local:${CONTROL_PORT}"
}

function uninstall_control_server() {
  echo "Uninstalling Natural Language Control Server..."
  
  sudo systemctl stop ${SERVICE_NAME} || true
  sudo systemctl disable ${SERVICE_NAME} || true
  sudo rm -f /etc/systemd/system/${SERVICE_NAME}
  sudo systemctl daemon-reload
  
  echo "✓ Control server uninstalled"
}

function status_control_server() {
  sudo systemctl status ${SERVICE_NAME} --no-pager
}

function logs_control_server() {
  sudo journalctl -u ${SERVICE_NAME} -n 50 --no-pager
}

function restart_control_server() {
  sudo systemctl restart ${SERVICE_NAME}
  echo "✓ Control server restarted"
}

# Main command dispatcher
case "${1:-}" in
  install)
    install_control_server
    ;;
  uninstall)
    uninstall_control_server
    ;;
  status)
    status_control_server
    ;;
  logs)
    logs_control_server
    ;;
  restart)
    restart_control_server
    ;;
  *)
    echo "Usage: $0 {install|uninstall|status|logs|restart}"
    exit 1
    ;;
esac
