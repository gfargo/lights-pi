#!/usr/bin/env bash
# Install and configure the Natural Language Control Server

set -euo pipefail

# Allow script to be sourced or run directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  CONTROL_DIR="${SCRIPT_DIR}/control-server"
  SERVICE_NAME="lighting-control.service"
  CONTROL_PORT="${CONTROL_PORT:-5000}"
else
  # Being sourced by lightsctl.sh - variables already set
  CONTROL_DIR="${SCRIPT_DIR}/control-server"
  SERVICE_NAME="lighting-control.service"
  CONTROL_PORT="${CONTROL_PORT:-5000}"
fi

function install_control_server() {
  echo "Installing Natural Language Control Server on Pi..."
  
  # Check if pip3 is installed, install if needed
  echo "Checking Python dependencies..."
  if ! "${REMOTE_CMD[@]}" "which pip3 >/dev/null 2>&1"; then
    echo "Installing pip3..."
    run_sudo apt-get update -qq
    run_sudo apt-get install -y python3-pip python3-venv
  fi
  
  # Create virtual environment on Pi
  echo "Setting up Python virtual environment..."
  "${REMOTE_CMD[@]}" "python3 -m venv ~/control-server-venv || true"
  
  # Install Python dependencies in venv
  echo "Installing Python dependencies..."
  "${REMOTE_CMD[@]}" "~/control-server-venv/bin/pip install Flask==3.0.0 flask-cors==4.0.0 requests==2.31.0"
  
  # Copy control server files to Pi
  echo "Copying control server files to Pi..."
  "${SCP_CMD[@]}" -r "${CONTROL_DIR}" "${PI_USER}@${PI_HOST}:/tmp/"
  "${REMOTE_CMD[@]}" "mkdir -p ~/control-server && cp -r /tmp/control-server/* ~/control-server/ && rm -rf /tmp/control-server"
  
  # Copy lightsctl.sh and scripts to Pi
  echo "Copying lightsctl.sh and scripts to Pi..."
  "${SCP_CMD[@]}" "${SCRIPT_DIR}/lightsctl.sh" "${PI_USER}@${PI_HOST}:~/"
  "${SCP_CMD[@]}" -r "${SCRIPT_DIR}/scripts" "${PI_USER}@${PI_HOST}:~/"
  "${REMOTE_CMD[@]}" "chmod +x ~/lightsctl.sh"
  
  # Copy .env file
  if [[ -f "${SCRIPT_DIR}/.env" ]]; then
    echo "Copying .env file..."
    "${SCP_CMD[@]}" "${SCRIPT_DIR}/.env" "${PI_USER}@${PI_HOST}:~/"
    "${SCP_CMD[@]}" "${SCRIPT_DIR}/.env" "${PI_USER}@${PI_HOST}:~/control-server/"
  fi
  
  # Create systemd service
  echo "Creating systemd service..."
  cat > /tmp/${SERVICE_NAME} <<EOF
[Unit]
Description=Lighting Control Server
After=network.target qlcplus-web.service

[Service]
Type=simple
User=${PI_USER}
WorkingDirectory=/home/${PI_USER}/control-server
Environment="PATH=/home/${PI_USER}/control-server-venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="CONTROL_PORT=${CONTROL_PORT}"
EnvironmentFile=/home/${PI_USER}/control-server/.env
ExecStart=/home/${PI_USER}/control-server-venv/bin/python /home/${PI_USER}/control-server/app.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

  "${SCP_CMD[@]}" /tmp/${SERVICE_NAME} "${PI_USER}@${PI_HOST}:/tmp/"
  rm /tmp/${SERVICE_NAME}
  
  run_sudo mv /tmp/${SERVICE_NAME} /etc/systemd/system/${SERVICE_NAME}
  run_sudo systemctl daemon-reload
  run_sudo systemctl enable ${SERVICE_NAME}
  run_sudo systemctl start ${SERVICE_NAME}
  
  # Add firewall rule for control server port
  echo "Configuring firewall..."
  run_sudo ufw allow ${CONTROL_PORT}/tcp comment "'Control Server'"
  
  echo "✓ Control server installed and started"
  echo ""
  echo "Access at: http://lights.local:${CONTROL_PORT}"
}

function uninstall_control_server() {
  echo "Uninstalling Natural Language Control Server..."
  
  run_sudo systemctl stop ${SERVICE_NAME} || true
  run_sudo systemctl disable ${SERVICE_NAME} || true
  run_sudo rm -f /etc/systemd/system/${SERVICE_NAME}
  run_sudo systemctl daemon-reload
  
  # Remove firewall rule
  echo "Removing firewall rule..."
  run_sudo ufw delete allow ${CONTROL_PORT}/tcp || true
  
  echo "✓ Control server uninstalled"
}

function status_control_server() {
  run_sudo systemctl status ${SERVICE_NAME} --no-pager
}

function logs_control_server() {
  run_sudo journalctl -u ${SERVICE_NAME} -n 50 --no-pager
}

function restart_control_server() {
  run_sudo systemctl restart ${SERVICE_NAME}
  echo "✓ Control server restarted"
}

# Main command dispatcher (only when run directly, not sourced)
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
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
fi
