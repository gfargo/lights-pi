#!/usr/bin/env bash
# Helper to run common QLC+ service and Wi-Fi checks on lights.local from the workstation.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

function load_env_file() {
  local line key value
  while IFS= read -r line || [[ -n $line ]]; do
    line="${line%%#*}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z $line || $line != *"="* ]] && continue
    key="${line%%=*}"
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    [[ -z $key ]] && continue
    value="${line#*=}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    line="${key}=${value}"
    if [[ -v $key ]]; then
      continue
    fi
    eval "export $line"
  done < "$ENV_FILE"
}

if [[ -f "$ENV_FILE" ]]; then
  load_env_file
fi

PI_HOST="${PI_HOST:-lights.local}"
PI_USER="${PI_USER:-pi}"
PI_PI_HOSTNAME="${PI_PI_HOSTNAME:-lights}"
QLC_PORT="${QLC_PORT:-9999}"
SERVICE="qlcplus-web.service"
EDITOR="${EDITOR:-nano}"
SSH_KEY="${SSH_KEY:-}"
BACKUP_STORAGE="${BACKUP_STORAGE:-${SCRIPT_DIR}/backups}"
SSL_CERT="${SSL_CERT:-${SCRIPT_DIR}/certs/qlc.crt}"
SSL_KEY="${SSL_KEY:-${SCRIPT_DIR}/certs/qlc.key}"
SSH_OPTIONS=()
if [[ -n "$SSH_KEY" ]]; then
  SSH_OPTIONS+=("-i" "$SSH_KEY")
fi
REMOTE_CMD=(ssh "${SSH_OPTIONS[@]}" "${PI_USER}@${PI_HOST}")
SCP_CMD=(scp "${SSH_OPTIONS[@]}")

function usage() {
  cat <<EOF
lightsctl.sh [command]

Provisioning:
  setup-full                    full provision: setup then harden (recommended for new Pi)
  setup                         base install (requires: WIFI1_SSID/PSK + WIFI2_SSID/PSK)
  harden                        firewall, watchdog, unattended upgrades, udev rule
  add-key [pubkey]              install local SSH public key on the Pi
  disable-password-auth         disable SSH password login (run add-key first)
  static-ip <ip/prefix> <gw>   write static IP to /etc/dhcpcd.conf and restart
  update                        apt update && apt upgrade on the Pi
  update-qlc                    upgrade only the qlcplus package and restart service

Service:
  status                        systemd status for ${SERVICE}
  restart                       restart ${SERVICE}
  logs                          last 80 lines from service journal
  logs-errors                   show only ERROR and WARN lines from logs
  tail                          follow service logs live
  health                        service + web UI + USB + disk + memory + CPU temp
  diagnose                      full diagnostic dump (health + logs + wifi + uptime)
  check                         ping + SSH pre-flight connectivity check
  validate                      pre-flight validation (config, connectivity, dependencies)

QLC+:
  qlc-version                   run qlcplus --version on the Pi
  qlc-headless                  push Qt platform fix (sets QT_QPA_PLATFORM=minimal)
  deploy-workspace <file.qxw>   upload workspace to Pi and restart service
  pull-workspace [output.qxw]   download current workspace from Pi
  list-fixtures                 show installed fixture definitions
  open-web                      open the web UI in the default browser

Network / WiFi:
  wifi                          dump /etc/wpa_supplicant/wpa_supplicant.conf
  wifi-reconf                   run wpa_cli -i wlan0 reconfigure
  wifi-status                   show SSID and wlan0 address
  wifi-edit                     edit the Wi-Fi config in \$EDITOR

System:
  lsusb                         show USB devices (ENTTEC should appear)
  backup                        pull QLC+ config dirs to ${BACKUP_STORAGE}
  restore <backup.tar.gz>       restore QLC+ config from backup and restart service
  hdmi-disable                  disable HDMI to save power
  reboot                        reboot the Pi
  poweroff                      shut down the Pi
  ssh                           open an interactive shell on the Pi
  edit <path>                   edit an arbitrary file on the Pi

TLS:
  gen-cert [days]               generate self-signed cert/key in certs/ (default: 730 days)
  ssl-proxy [cert] [key]        install stunnel, redirect 443 → ${QLC_PORT}

Landing page (http://lights.local):
  landing-setup                 install nginx and deploy the landing page (first time)
  landing-deploy                push updated landing/index.html (no nginx reinstall)

Set env vars to override defaults: PI_HOST, PI_USER, PI_HOSTNAME, QLC_PORT, SSH_KEY, BACKUP_STORAGE, SSL_CERT, SSL_KEY
(Note: use PI_HOSTNAME not HOSTNAME — HOSTNAME is a macOS shell built-in)
EOF
}

function run() {
  "${REMOTE_CMD[@]}" "$@"
}

function run_sudo() {
  run sudo "$@"
}

function command_status() {
  run_sudo systemctl status "${SERVICE}" --no-pager
}

function command_restart() {
  run_sudo systemctl restart "${SERVICE}"
  command_status
}

function command_logs() {
  run_sudo journalctl -u "${SERVICE}" -n 80 --no-pager
}

function command_logs_errors() {
  run_sudo journalctl -u "${SERVICE}" -n 200 --no-pager | grep -iE "error|warn|fail|critical"
}

function command_tail() {
  "${REMOTE_CMD[@]}" sudo journalctl -u "${SERVICE}" -f
}

function command_lsusb() {
  run lsusb
}

function command_health() {
  printf '%-20s' "Service:"
  if run systemctl is-active --quiet "${SERVICE}" 2>/dev/null; then
    echo "running"
  else
    echo "NOT running"
  fi

  printf '%-20s' "Web UI:"
  if run curl -sf --max-time 5 "http://127.0.0.1:${QLC_PORT}" >/dev/null 2>&1; then
    echo "reachable (port ${QLC_PORT})"
  else
    echo "unreachable (port ${QLC_PORT})"
  fi

  printf '%-20s' "ENTTEC USB:"
  if run lsusb 2>/dev/null | grep -qi "FTDI\|0403:6001"; then
    echo "detected"
  else
    echo "not found"
  fi

  printf '%-20s' "Disk (/):"
  run df -h / 2>/dev/null | awk 'NR==2{print $5" used ("$3"/"$2")"}'

  printf '%-20s' "Memory:"
  run free -h 2>/dev/null | awk '/^Mem:/{print $3"/"$2}'

  printf '%-20s' "CPU temp:"
  local temp
  temp=$(run cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null) || true
  if [[ -n "$temp" ]]; then
    awk "BEGIN{printf \"%.1f°C\n\", ${temp}/1000}"
  else
    echo "n/a"
  fi
}

function command_diagnose() {
  echo "=== Diagnostic report: $(date -u '+%Y-%m-%d %H:%M:%S UTC') ==="
  echo "    Host: ${PI_HOST}  User: ${PI_USER}  Port: ${QLC_PORT}"
  echo ""
  echo "--- Health ---"
  command_health
  echo ""
  echo "--- Last 20 log lines ---"
  run_sudo journalctl -u "${SERVICE}" -n 20 --no-pager
  echo ""
  echo "--- WiFi ---"
  command_wifi_status
  echo ""
  echo "--- Uptime / load ---"
  run uptime
}

function command_check() {
  printf '%-20s' "Ping ${PI_HOST}:"
  if ping -c1 -W2 "${PI_HOST}" >/dev/null 2>&1; then
    echo "reachable"
  else
    echo "unreachable"
    return 1
  fi

  printf '%-20s' "SSH:"
  if "${REMOTE_CMD[@]}" -o ConnectTimeout=5 -o BatchMode=yes true 2>/dev/null; then
    echo "OK"
  else
    echo "failed (check credentials / firewall)"
    return 1
  fi
}

function command_validate() {
  local errors=0
  local warnings=0
  
  echo "=== Pre-flight Validation ==="
  echo ""
  
  # Check local environment
  echo "--- Local Environment ---"
  
  printf '%-30s' ".env file:"
  if [[ -f "$ENV_FILE" ]]; then
    echo "✓ exists"
  else
    echo "✗ missing (copy from .env.example)"
    ((errors++))
  fi
  
  printf '%-30s' "Required scripts:"
  local missing_scripts=()
  for script in "scripts/pi_lights_setup.sh" "scripts/pi_harden.sh" "scripts/pi_landing.sh" "scripts/configure_qlc_headless.sh"; do
    if [[ ! -f "${SCRIPT_DIR}/${script}" ]]; then
      missing_scripts+=("$script")
    fi
  done
  if [[ ${#missing_scripts[@]} -eq 0 ]]; then
    echo "✓ all present"
  else
    echo "✗ missing: ${missing_scripts[*]}"
    ((errors++))
  fi
  
  printf '%-30s' "Backup storage:"
  if [[ -d "$BACKUP_STORAGE" ]]; then
    echo "✓ ${BACKUP_STORAGE}"
  else
    echo "⚠ ${BACKUP_STORAGE} (will be created)"
    ((warnings++))
  fi
  
  printf '%-30s' "SSH key:"
  if [[ -n "$SSH_KEY" ]]; then
    if [[ -f "$SSH_KEY" ]]; then
      echo "✓ ${SSH_KEY}"
    else
      echo "✗ ${SSH_KEY} not found"
      ((errors++))
    fi
  else
    echo "⚠ not configured (using password auth)"
    ((warnings++))
  fi
  
  echo ""
  echo "--- Network Connectivity ---"
  
  printf '%-30s' "Ping ${PI_HOST}:"
  if ping -c1 -W2 "${PI_HOST}" >/dev/null 2>&1; then
    echo "✓ reachable"
  else
    echo "✗ unreachable"
    ((errors++))
  fi
  
  printf '%-30s' "SSH connection:"
  if "${REMOTE_CMD[@]}" -o ConnectTimeout=5 -o BatchMode=yes true 2>/dev/null; then
    echo "✓ connected as ${PI_USER}@${PI_HOST}"
  else
    echo "✗ failed (check credentials/firewall)"
    ((errors++))
  fi
  
  # Only check Pi if we can connect
  if "${REMOTE_CMD[@]}" -o ConnectTimeout=5 -o BatchMode=yes true 2>/dev/null; then
    echo ""
    echo "--- Pi System State ---"
    
    printf '%-30s' "QLC+ installed:"
    if run command -v qlcplus >/dev/null 2>&1; then
      local version
      version=$(run qlcplus --version 2>&1 | head -1 || echo "unknown")
      echo "✓ ${version}"
    else
      echo "✗ not installed"
      ((errors++))
    fi
    
    printf '%-30s' "Service status:"
    if run systemctl is-active --quiet "${SERVICE}" 2>/dev/null; then
      echo "✓ running"
    else
      echo "✗ not running"
      ((errors++))
    fi
    
    printf '%-30s' "Web UI:"
    if run curl -sf --max-time 5 "http://127.0.0.1:${QLC_PORT}" >/dev/null 2>&1; then
      echo "✓ responding on port ${QLC_PORT}"
    else
      echo "✗ not responding on port ${QLC_PORT}"
      ((errors++))
    fi
    
    printf '%-30s' "ENTTEC USB:"
    if run lsusb 2>/dev/null | grep -qi "FTDI\|0403:6001"; then
      echo "✓ detected"
    else
      echo "⚠ not detected"
      ((warnings++))
    fi
    
    printf '%-30s' "Disk space:"
    local disk_usage
    disk_usage=$(run df -h / 2>/dev/null | awk 'NR==2{print $5}' | tr -d '%')
    if [[ -n "$disk_usage" ]]; then
      if [[ $disk_usage -lt 80 ]]; then
        echo "✓ ${disk_usage}% used"
      elif [[ $disk_usage -lt 90 ]]; then
        echo "⚠ ${disk_usage}% used"
        ((warnings++))
      else
        echo "✗ ${disk_usage}% used (critically low)"
        ((errors++))
      fi
    else
      echo "⚠ unable to check"
      ((warnings++))
    fi
  fi
  
  echo ""
  echo "--- Summary ---"
  if [[ $errors -eq 0 && $warnings -eq 0 ]]; then
    echo "✓ All checks passed! System is ready."
    return 0
  elif [[ $errors -eq 0 ]]; then
    echo "⚠ ${warnings} warning(s) - system should work but review warnings above"
    return 0
  else
    echo "✗ ${errors} error(s), ${warnings} warning(s) - fix errors before proceeding"
    return 1
  fi
}

function command_add_key() {
  local pubkey="${1:-}"
  if [[ -z "$pubkey" ]]; then
    for k in ${SSH_KEY:+"${SSH_KEY}.pub"} \
              ~/.ssh/id_ed25519.pub \
              ~/.ssh/id_rsa.pub \
              ~/.ssh/id_ecdsa.pub; do
      [[ -f "$k" ]] && { pubkey="$k"; break; }
    done
  fi
  if [[ -z "$pubkey" || ! -f "$pubkey" ]]; then
    echo "No public key found. Pass one explicitly: add-key <path/to/key.pub>" >&2
    return 1
  fi
  echo "Installing ${pubkey} → ${PI_USER}@${PI_HOST}:~/.ssh/authorized_keys"
  cat "$pubkey" | "${REMOTE_CMD[@]}" \
    "mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && cat >> ~/.ssh/authorized_keys && sort -u ~/.ssh/authorized_keys -o ~/.ssh/authorized_keys"
  echo "Key installed. Test with: ./lightsctl.sh ssh"
}

function command_disable_password_auth() {
  local sshd_config="/etc/ssh/sshd_config"
  if ! run test -s ~/.ssh/authorized_keys 2>/dev/null; then
    echo "No authorized keys found on ${PI_HOST}. Run add-key first." >&2
    return 1
  fi
  run_sudo sed -i '/^#*PasswordAuthentication/d' "$sshd_config"
  run_sudo sed -i '/^#*KbdInteractiveAuthentication/d' "$sshd_config"
  run_sudo sed -i '/^#*ChallengeResponseAuthentication/d' "$sshd_config"
  echo 'PasswordAuthentication no' | run_sudo tee -a "$sshd_config" >/dev/null
  echo 'KbdInteractiveAuthentication no' | run_sudo tee -a "$sshd_config" >/dev/null
  run_sudo systemctl restart ssh
  echo "Password authentication disabled on ${PI_HOST}."
  echo "Verify your key works: ./lightsctl.sh ssh"
}

function command_update_qlc() {
  run_sudo apt-get update -q
  run_sudo apt-get install -y --only-upgrade qlcplus
  command_restart
}

function command_static_ip() {
  local ip="${1:-}"
  local gateway="${2:-}"
  local dns="${3:-}"
  if [[ -z "$ip" || -z "$gateway" ]]; then
    echo "Usage: static-ip <ip/prefix> <gateway> [dns]" >&2
    echo "Example: ./lightsctl.sh static-ip 192.168.1.50/24 192.168.1.1" >&2
    return 1
  fi
  dns="${dns:-${gateway}}"
  local dhcpcd_conf="/etc/dhcpcd.conf"
  run_sudo sed -i '/^# lightsctl static IP/,/^[[:space:]]*$/d' "$dhcpcd_conf" || true
  run_sudo tee -a "$dhcpcd_conf" >/dev/null <<DHCP

# lightsctl static IP
interface wlan0
static ip_address=${ip}
static routers=${gateway}
static domain_name_servers=${dns}
DHCP
  echo "Static IP configured: ${ip} (gateway: ${gateway}, DNS: ${dns})"
  echo "Restarting network..."
  run_sudo systemctl restart dhcpcd5 || run_sudo systemctl restart dhcpcd || true
  echo "Done. Pi should now be reachable at ${ip%/*}"
}

function command_qlc_version() {
  if run qlcplus --version; then
    :
  else
    echo "qlcplus not installed on ${PI_HOST}, install it manually or rerun the setup script."
  fi
}

function command_qlc_headless() {
  local script_local="${SCRIPT_DIR}/scripts/configure_qlc_headless.sh"
  local script_remote="/tmp/configure_qlc_headless.sh"
  if [[ ! -f "$script_local" ]]; then
    echo "configure_qlc_headless.sh not found at ${script_local}" >&2
    return 1
  fi
  "${SCP_CMD[@]}" "$script_local" "${PI_USER}@${PI_HOST}:${script_remote}"
  run sudo bash "${script_remote}"
  run rm -f "${script_remote}"
}

function command_list_fixtures() {
  echo "=== System Fixture Definitions ==="
  run find /usr/share/qlcplus/fixtures -name "*.qxf" -type f 2>/dev/null | run sort || echo "No system fixtures found"
  
  echo ""
  echo "=== User Fixture Definitions ==="
  local user_fixtures="/home/${PI_USER}/.qlcplus/fixtures"
  if run test -d "$user_fixtures" 2>/dev/null; then
    run find "$user_fixtures" -name "*.qxf" -type f 2>/dev/null | run sort || echo "No user fixtures found"
  else
    echo "User fixtures directory does not exist"
  fi
  
  echo ""
  echo "Total fixtures:"
  local system_count user_count
  system_count=$(run find /usr/share/qlcplus/fixtures -name "*.qxf" -type f 2>/dev/null | run wc -l || echo "0")
  user_count=$(run find "/home/${PI_USER}/.qlcplus/fixtures" -name "*.qxf" -type f 2>/dev/null | run wc -l || echo "0")
  echo "  System: ${system_count}"
  echo "  User:   ${user_count}"
}

function command_wifi() {
  run_sudo cat /etc/wpa_supplicant/wpa_supplicant.conf
}

function command_wifi_reconf() {
  run_sudo wpa_cli -i wlan0 reconfigure
}

function command_wifi_status() {
  run_sudo wpa_cli -i wlan0 status
  run ip -br a show wlan0
}

function command_update() {
  run_sudo apt update
  run_sudo apt -y upgrade
}

function command_backup() {
  local stamp remote_tmp local_target dirs
  stamp="$(date -u +"%Y%m%dT%H%M%SZ")"
  remote_tmp="/tmp/qlcplus-backup-${stamp}.tar.gz"
  local_target="${BACKUP_STORAGE}/qlcplus-backup-${stamp}.tar.gz"
  dirs=()
  for entry in ".config/qlcplus" ".qlcplus"; do
    if run_sudo test -e "/home/${PI_USER}/${entry}"; then
      dirs+=("${entry}")
    fi
  done
  if [[ ${#dirs[@]} -eq 0 ]]; then
    echo "No QLC+ configuration found under /home/${PI_USER} on ${PI_HOST}."
    return 0
  fi
  mkdir -p "${BACKUP_STORAGE}"
  run_sudo tar -czf "${remote_tmp}" -C "/home/${PI_USER}" "${dirs[@]}"
  "${SCP_CMD[@]}" "${PI_USER}@${PI_HOST}:${remote_tmp}" "${local_target}"
  run_sudo rm -f "${remote_tmp}"
  echo "Backup saved to ${local_target}"
}

function command_restore() {
  local backup_file="${1:-}"
  if [[ -z "$backup_file" ]]; then
    echo "Usage: restore <path/to/backup.tar.gz>" >&2
    echo "" >&2
    echo "Available backups in ${BACKUP_STORAGE}:" >&2
    if [[ -d "$BACKUP_STORAGE" ]]; then
      ls -1t "${BACKUP_STORAGE}"/*.tar.gz 2>/dev/null | head -5 || echo "  (none found)"
    else
      echo "  (backup directory does not exist)"
    fi
    return 1
  fi
  if [[ ! -f "$backup_file" ]]; then
    echo "Backup file not found: ${backup_file}" >&2
    return 1
  fi

  local remote_tmp="/tmp/qlcplus-restore-$$.tar.gz"
  
  echo "Stopping ${SERVICE}..."
  run_sudo systemctl stop "${SERVICE}"
  
  echo "Uploading backup to Pi..."
  "${SCP_CMD[@]}" "$backup_file" "${PI_USER}@${PI_HOST}:${remote_tmp}"
  
  echo "Backing up current config (just in case)..."
  run_sudo tar -czf "/tmp/qlcplus-pre-restore-backup.tar.gz" -C "/home/${PI_USER}" \
    ".config/qlcplus" ".qlcplus" 2>/dev/null || true
  
  echo "Removing existing QLC+ config..."
  run_sudo rm -rf "/home/${PI_USER}/.config/qlcplus" "/home/${PI_USER}/.qlcplus"
  
  echo "Extracting backup..."
  run_sudo tar -xzf "${remote_tmp}" -C "/home/${PI_USER}"
  
  echo "Fixing ownership..."
  run_sudo chown -R "${PI_USER}:${PI_USER}" "/home/${PI_USER}/.config/qlcplus" "/home/${PI_USER}/.qlcplus" 2>/dev/null || true
  
  echo "Cleaning up..."
  run_sudo rm -f "${remote_tmp}"
  
  echo "Restarting ${SERVICE}..."
  run_sudo systemctl start "${SERVICE}"
  
  echo ""
  echo "Restore complete! Pre-restore backup saved on Pi at:"
  echo "  /tmp/qlcplus-pre-restore-backup.tar.gz"
  echo ""
  command_status
}

function command_deploy_workspace() {
  local workspace="${1:-}"
  if [[ -z "$workspace" ]]; then
    echo "Usage: deploy-workspace <path/to/file.qxw>" >&2
    return 1
  fi
  if [[ ! -f "$workspace" ]]; then
    echo "Workspace file not found: ${workspace}" >&2
    return 1
  fi
  local filename remote_path service_file
  filename="$(basename "$workspace")"
  remote_path="/home/${PI_USER}/${filename}"
  service_file="/etc/systemd/system/qlcplus-web.service"

  "${SCP_CMD[@]}" "$workspace" "${PI_USER}@${PI_HOST}:${remote_path}"
  echo "Uploaded ${filename} → ${remote_path}"

  run_sudo sed -i "s|ExecStart=.*qlcplus.*|ExecStart=/usr/bin/qlcplus --nogui --web --web-port ${QLC_PORT} --operate --workspace ${remote_path}|" "$service_file"
  run_sudo systemctl daemon-reload
  run_sudo systemctl restart "${SERVICE}"
  echo "Service updated with --workspace ${remote_path} and restarted"
}

function command_pull_workspace() {
  local output="${1:-}"
  local service_file="/etc/systemd/system/qlcplus-web.service"
  
  # Get the current workspace path from the service file
  local remote_workspace
  remote_workspace=$(run_sudo grep "ExecStart=" "$service_file" | sed -n 's/.*--workspace \([^ ]*\).*/\1/p')
  
  if [[ -z "$remote_workspace" ]]; then
    echo "No workspace configured in service file" >&2
    echo "Service is running without a specific workspace" >&2
    return 1
  fi
  
  # Check if the workspace file exists on the Pi
  if ! run test -f "$remote_workspace" 2>/dev/null; then
    echo "Workspace file not found on Pi: ${remote_workspace}" >&2
    return 1
  fi
  
  # Determine output filename
  if [[ -z "$output" ]]; then
    local filename
    filename="$(basename "$remote_workspace")"
    output="${SCRIPT_DIR}/workspaces/${filename}"
  fi
  
  # Create workspaces directory if it doesn't exist
  mkdir -p "$(dirname "$output")"
  
  # Download the workspace
  echo "Downloading ${remote_workspace} from Pi..."
  "${SCP_CMD[@]}" "${PI_USER}@${PI_HOST}:${remote_workspace}" "$output"
  
  echo "Workspace saved to: ${output}"
  
  # Show file info
  if [[ -f "$output" ]]; then
    local size
    size=$(ls -lh "$output" | awk '{print $5}')
    echo "Size: ${size}"
  fi
}

function command_open_web() {
  local url="http://${PI_HOSTNAME}.local:${QLC_PORT}"
  echo "Headless UI: ${url}"
  echo "Direct IP:   http://${PI_HOST}:${QLC_PORT}"
  if command -v open >/dev/null 2>&1; then
    open "$url"
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url"
  fi
}

function command_ssh() {
  exec "${REMOTE_CMD[@]}"
}

function command_edit() {
  local target="${1:-/etc/wpa_supplicant/wpa_supplicant.conf}"
  run_sudo "${EDITOR}" "$target"
}

function command_wifi_edit() {
  command_edit
}

function command_hdmi_disable() {
  local config
  if run test -f /boot/firmware/config.txt 2>/dev/null; then
    config="/boot/firmware/config.txt"
  else
    config="/boot/config.txt"
  fi
  if run_sudo grep -qE '^[[:space:]]*hdmi_blanking=2' "$config"; then
    echo "hdmi_blanking=2 already present in ${config}"
    return 0
  fi
  run_sudo tee -a "$config" >/dev/null <<'EOF'
# disable HDMI to save power
hdmi_blanking=2
EOF
  echo "Appended hdmi_blanking=2 to ${config}"
}

function command_harden() {
  local script="${SCRIPT_DIR}/scripts/pi_harden.sh"
  if [[ ! -f "$script" ]]; then
    echo "scripts/pi_harden.sh not found at ${script}" >&2
    return 1
  fi
  PI_HOST="${PI_HOST}" \
  PI_USER="${PI_USER}" \
  QLC_PORT="${QLC_PORT}" \
  bash "$script"
}

function command_setup_full() {
  command_setup
  echo ""
  echo "Base setup complete. Running hardening..."
  echo ""
  command_harden
}

function command_reboot() {
  echo "Rebooting ${PI_HOST}..."
  run_sudo reboot || true
}

function command_poweroff() {
  echo "Powering off ${PI_HOST}..."
  run_sudo poweroff || true
}

function command_setup() {
  local script="${SCRIPT_DIR}/scripts/pi_lights_setup.sh"
  if [[ ! -f "$script" ]]; then
    echo "scripts/pi_lights_setup.sh not found at ${script}" >&2
    return 1
  fi
  PI_HOST="${PI_HOST}" \
  PI_USER="${PI_USER}" \
  PI_HOSTNAME="${PI_HOSTNAME}" \
  QLC_PORT="${QLC_PORT}" \
  bash "$script"
}

function command_landing_setup() {
  local script="${SCRIPT_DIR}/scripts/pi_landing.sh"
  if [[ ! -f "$script" ]]; then
    echo "scripts/pi_landing.sh not found at ${script}" >&2
    return 1
  fi
  PI_HOST="${PI_HOST}" \
  PI_USER="${PI_USER}" \
  QLC_PORT="${QLC_PORT}" \
  LANDING_SRC="${SCRIPT_DIR}/landing/index.html" \
  bash "$script"
}

function command_landing_deploy() {
  local landing_src="${SCRIPT_DIR}/landing/index.html"
  if [[ ! -f "$landing_src" ]]; then
    echo "landing/index.html not found at ${landing_src}" >&2
    return 1
  fi
  local rendered
  rendered="$(mktemp /tmp/qlc-landing-XXXXXX.html)"
  trap "rm -f '$rendered'" RETURN
  sed "s|__QLC_URL__|http://${PI_HOST}:${QLC_PORT}|g" "$landing_src" > "$rendered"
  "${SCP_CMD[@]}" "$rendered" "${PI_USER}@${PI_HOST}:/tmp/qlc-landing.html"
  run_sudo mv /tmp/qlc-landing.html /var/www/html/index.html
  run_sudo chmod 644 /var/www/html/index.html
  echo "Landing page updated at http://${PI_HOST}"
}

function command_gen_cert() {
  local days="${1:-730}"
  local cert_dir="${SCRIPT_DIR}/certs"
  local cert="${cert_dir}/qlc.crt"
  local key="${cert_dir}/qlc.key"

  if ! command -v openssl >/dev/null 2>&1; then
    echo "openssl not found. Install it with: brew install openssl" >&2
    return 1
  fi
  if [[ -f "$cert" || -f "$key" ]]; then
    echo "Certs already exist in ${cert_dir}/. Delete them first to regenerate." >&2
    return 1
  fi

  # Build SANs: always include hostname.local; add IP or extra DNS if PI_HOST is set
  local san="DNS:${PI_HOSTNAME}.local,DNS:localhost"
  if [[ -n "$PI_HOST" && "$PI_HOST" != "${PI_HOSTNAME}.local" ]]; then
    if [[ "$PI_HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      san="${san},IP:${PI_HOST}"
    else
      san="${san},DNS:${PI_HOST}"
    fi
  fi

  local tmpconf
  tmpconf="$(mktemp /tmp/qlc-openssl-XXXXXX.cnf)"
  # shellcheck disable=SC2064
  trap "rm -f '$tmpconf'" RETURN

  cat > "$tmpconf" <<CONF
[req]
distinguished_name = dn
x509_extensions    = san
prompt             = no

[dn]
CN = ${PI_HOSTNAME}.local

[san]
subjectAltName = ${san}
CONF

  mkdir -p "$cert_dir"
  openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout "$key" -out "$cert" \
    -days "$days" \
    -config "$tmpconf" 2>/dev/null
  chmod 600 "$key"

  echo "Certificate: ${cert}"
  echo "Private key: ${key}"
  echo "Valid for:   ${days} days"
  echo "SANs:        ${san}"
  echo ""
  echo "Run: ./lightsctl.sh ssl-proxy  to install on the Pi."
}

function command_ssl_proxy() {
  local cert_local="${1:-${SSL_CERT}}"
  local key_local="${2:-${SSL_KEY}}"
  if [[ ! -f "$cert_local" ]]; then
    echo "Certificate not found at ${cert_local}"
    return 1
  fi
  if [[ ! -f "$key_local" ]]; then
    echo "Private key not found at ${key_local}"
    return 1
  fi

  run_sudo apt-get update
  run_sudo apt-get install -y stunnel4 iptables-persistent

  local remote_dir="/etc/ssl/qlc"
  local remote_cert="${remote_dir}/qlc.crt"
  local remote_key="${remote_dir}/qlc.key"

  "${SCP_CMD[@]}" "$cert_local" "${PI_USER}@${PI_HOST}:/tmp/qlc.crt"
  "${SCP_CMD[@]}" "$key_local" "${PI_USER}@${PI_HOST}:/tmp/qlc.key"
  run_sudo mkdir -p "${remote_dir}"
  run_sudo mv /tmp/qlc.crt "${remote_cert}"
  run_sudo mv /tmp/qlc.key "${remote_key}"
  run_sudo chmod 644 "${remote_cert}"
  run_sudo chmod 600 "${remote_key}"

  run_sudo tee /etc/stunnel/qlc.conf >/dev/null <<EOF
[global]
cert = ${remote_cert}
key = ${remote_key}
pid = /var/run/stunnel4-qlc.pid
socket = l:TCP_NODELAY=1
socket = r:TCP_NODELAY=1

[qlc]
accept = 443
connect = 127.0.0.1:${QLC_PORT}
EOF

  run_sudo sed -i 's/^ENABLED=0/ENABLED=1/' /etc/default/stunnel4 || true
  run_sudo systemctl enable --now stunnel4

  if ! run_sudo iptables -t nat -C PREROUTING -p tcp --dport 443 -j REDIRECT --to-ports "${QLC_PORT}" >/dev/null 2>&1; then
    run_sudo iptables -t nat -A PREROUTING -p tcp --dport 443 -j REDIRECT --to-ports "${QLC_PORT}"
  fi
  run_sudo netfilter-persistent save >/dev/null 2>&1 || true
  echo "SSL proxy configured on 443 → ${QLC_PORT} using ${remote_cert}"
}

if [[ $# -eq 0 ]]; then
  usage
  exit 0
fi

case "$1" in
  help|-h|--help) usage ;;
  status) command_status ;;
  restart) command_restart ;;
  logs) command_logs ;;
  logs-errors) command_logs_errors ;;
  tail) command_tail ;;
  lsusb) command_lsusb ;;
  qlc-version) command_qlc_version ;;
  qlc-headless) command_qlc_headless ;;
  list-fixtures) command_list_fixtures ;;
  wifi) command_wifi ;;
  wifi-reconf) command_wifi_reconf ;;
  wifi-status) command_wifi_status ;;
  update) command_update ;;
  update-qlc) command_update_qlc ;;
  backup) command_backup ;;
  restore) shift; command_restore "$@" ;;
  check) command_check ;;
  validate) command_validate ;;
  diagnose) command_diagnose ;;
  add-key) shift; command_add_key "$@" ;;
  disable-password-auth) command_disable_password_auth ;;
  static-ip) shift; command_static_ip "$@" ;;
  hdmi-disable) command_hdmi_disable ;;
  setup) command_setup ;;
  harden) command_harden ;;
  setup-full) command_setup_full ;;
  reboot) command_reboot ;;
  poweroff) command_poweroff ;;
  gen-cert) shift; command_gen_cert "$@" ;;
  ssl-proxy) shift; command_ssl_proxy "$@" ;;
  health) command_health ;;
  deploy-workspace) shift; command_deploy_workspace "$@" ;;
  pull-workspace) shift; command_pull_workspace "$@" ;;
  open-web) command_open_web ;;
  landing-setup) command_landing_setup ;;
  landing-deploy) command_landing_deploy ;;
  ssh) command_ssh ;;
  wifi-edit) command_wifi_edit ;;
  edit)
    shift
    command_edit "$@"
    ;;
  *)
    echo "Unknown command: $1" >&2
    usage
    exit 2
    ;;
esac
