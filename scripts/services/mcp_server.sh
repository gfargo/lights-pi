#!/usr/bin/env bash
# Install and configure the Lights MCP Server (Streamable HTTP)
#
# Mirrors scripts/services/control_server.sh — runs as a sibling systemd
# service ordered after lighting-control.service so the Flask backend is up
# before the MCP wrapper starts.

set -euo pipefail

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  MCP_DIR="${SCRIPT_DIR}/mcp-server"
  SERVICE_NAME="lighting-mcp.service"
  MCP_PORT="${MCP_PORT:-5001}"
  CONTROL_PORT="${CONTROL_PORT:-5000}"
else
  # Sourced by lightsctl.sh — REMOTE_CMD / SCP_CMD / PI_USER / PI_HOST set
  MCP_DIR="${SCRIPT_DIR}/mcp-server"
  SERVICE_NAME="lighting-mcp.service"
  MCP_PORT="${MCP_PORT:-5001}"
  CONTROL_PORT="${CONTROL_PORT:-5000}"
fi

function install_mcp_server() {
  echo "Installing Lights MCP Server on Pi..."

  echo "Checking Python dependencies..."
  if ! "${REMOTE_CMD[@]}" "which pip3 >/dev/null 2>&1"; then
    echo "Installing pip3..."
    run_sudo apt-get update -qq
    run_sudo apt-get install -y python3-pip python3-venv
  fi

  echo "Setting up Python virtual environment..."
  "${REMOTE_CMD[@]}" "python3 -m venv ~/mcp-server-venv || true"

  echo "Installing MCP server dependencies..."
  "${REMOTE_CMD[@]}" "~/mcp-server-venv/bin/pip install --upgrade pip"
  "${REMOTE_CMD[@]}" "~/mcp-server-venv/bin/pip install 'mcp[cli]>=1.2.0' 'httpx>=0.27.0'"

  echo "Copying MCP server files to Pi..."
  "${SCP_CMD[@]}" -r "${MCP_DIR}" "${PI_USER}@${PI_HOST}:/tmp/"
  "${REMOTE_CMD[@]}" "mkdir -p ~/mcp-server && cp -r /tmp/mcp-server/* ~/mcp-server/ && rm -rf /tmp/mcp-server"

  # Reuse the existing .env so MCP_BEARER_TOKEN / CONTROL_URL can be set there
  if [[ -f "${SCRIPT_DIR}/.env" ]]; then
    echo "Copying .env file..."
    "${SCP_CMD[@]}" "${SCRIPT_DIR}/.env" "${PI_USER}@${PI_HOST}:~/mcp-server/"
  fi

  echo "Creating systemd service..."
  cat > /tmp/${SERVICE_NAME} <<EOF
[Unit]
Description=Lights MCP Server (Streamable HTTP)
After=network.target lighting-control.service
Wants=lighting-control.service

[Service]
Type=simple
User=${PI_USER}
WorkingDirectory=/home/${PI_USER}/mcp-server
Environment="PATH=/home/${PI_USER}/mcp-server-venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="MCP_PORT=${MCP_PORT}"
Environment="CONTROL_URL=http://localhost:${CONTROL_PORT}"
EnvironmentFile=-/home/${PI_USER}/mcp-server/.env
ExecStart=/home/${PI_USER}/mcp-server-venv/bin/python /home/${PI_USER}/mcp-server/server.py
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

  echo "Configuring firewall..."
  run_sudo ufw allow ${MCP_PORT}/tcp comment "'Lights MCP Server'"

  echo "✓ MCP server installed and started"
  echo ""
  echo "Endpoint: http://lights.local:${MCP_PORT}/mcp"
}

function uninstall_mcp_server() {
  echo "Uninstalling Lights MCP Server..."

  run_sudo systemctl stop ${SERVICE_NAME} || true
  run_sudo systemctl disable ${SERVICE_NAME} || true
  run_sudo rm -f /etc/systemd/system/${SERVICE_NAME}
  run_sudo systemctl daemon-reload

  echo "Removing firewall rule..."
  run_sudo ufw delete allow ${MCP_PORT}/tcp || true

  echo "✓ MCP server uninstalled"
}

function status_mcp_server() {
  run_sudo systemctl status ${SERVICE_NAME} --no-pager
}

function logs_mcp_server() {
  run_sudo journalctl -u ${SERVICE_NAME} -n 50 --no-pager
}

function restart_mcp_server() {
  run_sudo systemctl restart ${SERVICE_NAME}
  echo "✓ MCP server restarted"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  case "${1:-}" in
    install)   install_mcp_server ;;
    uninstall) uninstall_mcp_server ;;
    status)    status_mcp_server ;;
    logs)      logs_mcp_server ;;
    restart)   restart_mcp_server ;;
    *)
      echo "Usage: $0 {install|uninstall|status|logs|restart}"
      exit 1
      ;;
  esac
fi
