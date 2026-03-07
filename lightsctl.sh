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
PI_HOSTNAME="${PI_HOSTNAME:-lights}"
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

# Export variables for use in utility scripts
export SCRIPT_DIR PI_HOST PI_USER PI_HOSTNAME QLC_PORT SERVICE EDITOR SSH_KEY BACKUP_STORAGE SSL_CERT SSL_KEY ENV_FILE
export REMOTE_CMD SCP_CMD SSH_OPTIONS

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
  doctor                        comprehensive system health check with recommendations
  perf [seconds]                monitor CPU, memory, network usage over time (default: 10s)
  benchmark                     test system performance (web UI latency, network speed)

QLC+:
  qlc-version                   run qlcplus --version on the Pi
  qlc-headless                  push Qt platform fix (sets QT_QPA_PLATFORM=minimal)
  deploy-workspace <file.qxw>   upload workspace to Pi and restart service
  pull-workspace [output.qxw]   download current workspace from Pi
  list-fixtures                 show installed fixture definitions
  install-fixture <file.qxf>    upload and install custom fixture definition
  test-dmx                      verify ENTTEC USB and DMX output capability
  open-web                      open the web UI in the default browser

Network / WiFi:
  wifi                          dump /etc/wpa_supplicant/wpa_supplicant.conf
  wifi-reconf                   run wpa_cli -i wlan0 reconfigure
  wifi-status                   show SSID and wlan0 address
  wifi-edit                     edit the Wi-Fi config in \$EDITOR (defaults to nano)
  scan [--deep]                 scan network for Raspberry Pi devices (add --deep for IP range scan)

System:
  lsusb                         show USB devices (ENTTEC should appear)
  backup                        pull QLC+ config dirs to ${BACKUP_STORAGE}
  restore <backup.tar.gz>       restore QLC+ config from backup and restart service
  os-version                    show Raspberry Pi OS and kernel version
  hdmi-disable                  disable HDMI to save power
  reboot                        reboot the Pi
  poweroff                      shut down the Pi
  ssh                           open an interactive shell on the Pi
  edit <path>                   edit an arbitrary file on the Pi (uses nano by default)

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

# Export helper functions for utility scripts
export -f run
export -f run_sudo

# Service commands
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

# System commands (using lib/system.sh)
function command_health() {
  source "${SCRIPT_DIR}/scripts/lib/system.sh"
  system_health
}

function command_diagnose() {
  source "${SCRIPT_DIR}/scripts/lib/system.sh"
  system_diagnose
}

function command_doctor() {
  source "${SCRIPT_DIR}/scripts/lib/system.sh"
  system_doctor
}

function command_perf() {
  source "${SCRIPT_DIR}/scripts/lib/system.sh"
  system_perf "$@"
}

function command_benchmark() {
  source "${SCRIPT_DIR}/scripts/lib/system.sh"
  system_benchmark
}

function command_check() {
  source "${SCRIPT_DIR}/scripts/lib/system.sh"
  system_check
}

function command_validate() {
  source "${SCRIPT_DIR}/scripts/lib/system.sh"
  system_validate
}

function command_os_version() {
  source "${SCRIPT_DIR}/scripts/lib/system.sh"
  system_os_version
}

# QLC+ commands (using lib/qlc.sh)
function command_qlc_version() {
  source "${SCRIPT_DIR}/scripts/lib/qlc.sh"
  qlc_show_version
}

function command_qlc_headless() {
  source "${SCRIPT_DIR}/scripts/lib/qlc.sh"
  qlc_configure_headless
}

function command_list_fixtures() {
  source "${SCRIPT_DIR}/scripts/lib/qlc.sh"
  qlc_list_fixtures
}

function command_install_fixture() {
  source "${SCRIPT_DIR}/scripts/lib/qlc.sh"
  qlc_install_fixture "$@"
}

function command_test_dmx() {
  source "${SCRIPT_DIR}/scripts/lib/qlc.sh"
  qlc_test_dmx
}

function command_deploy_workspace() {
  source "${SCRIPT_DIR}/scripts/lib/qlc.sh"
  qlc_deploy_workspace "$@"
}

function command_pull_workspace() {
  source "${SCRIPT_DIR}/scripts/lib/qlc.sh"
  qlc_pull_workspace "$@"
}

function command_open_web() {
  source "${SCRIPT_DIR}/scripts/lib/qlc.sh"
  qlc_open_web
}

# WiFi commands (using lib/wifi.sh)
function command_wifi() {
  source "${SCRIPT_DIR}/scripts/lib/wifi.sh"
  wifi_show_config
}

function command_wifi_reconf() {
  source "${SCRIPT_DIR}/scripts/lib/wifi.sh"
  wifi_reconfigure
}

function command_wifi_status() {
  source "${SCRIPT_DIR}/scripts/lib/wifi.sh"
  wifi_show_status
}

function command_wifi_edit() {
  source "${SCRIPT_DIR}/scripts/lib/wifi.sh"
  wifi_edit_config "$@"
}

# Network commands (using lib/network.sh)
function command_scan() {
  source "${SCRIPT_DIR}/scripts/lib/network.sh"
  scan_network "$@"
}

# Backup/Restore commands
function command_update() {
  source "${SCRIPT_DIR}/scripts/lib/backup.sh"
  system_update
}

function command_backup() {
  source "${SCRIPT_DIR}/scripts/lib/backup.sh"
  backup_create
}

function command_restore() {
  source "${SCRIPT_DIR}/scripts/lib/backup.sh"
  backup_restore "$@"
}

# SSH/System commands
function command_ssh() {
  exec "${REMOTE_CMD[@]}"
}

function command_edit() {
  local target="${1:-/etc/wpa_supplicant/wpa_supplicant.conf}"
  local editor="${EDITOR:-nano}"
  
  # Use ssh -t to allocate a pseudo-terminal for interactive editing
  ssh -t "${SSH_OPTIONS[@]}" "${PI_USER}@${PI_HOST}" sudo "$editor" "$target"
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

function command_reboot() {
  echo "Rebooting ${PI_HOST}..."
  run_sudo reboot || true
}

function command_poweroff() {
  echo "Powering off ${PI_HOST}..."
  run_sudo poweroff || true
}

# Provisioning commands
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

function command_harden() {
  local script="${SCRIPT_DIR}/scripts/provisioning/harden.sh"
  if [[ ! -f "$script" ]]; then
    echo "scripts/provisioning/harden.sh not found at ${script}" >&2
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

function command_setup() {
  local script="${SCRIPT_DIR}/scripts/provisioning/setup.sh"
  if [[ ! -f "$script" ]]; then
    echo "scripts/provisioning/setup.sh not found at ${script}" >&2
    return 1
  fi
  PI_HOST="${PI_HOST}" \
  PI_USER="${PI_USER}" \
  PI_HOSTNAME="${PI_HOSTNAME}" \
  QLC_PORT="${QLC_PORT}" \
  bash "$script"
}

function command_landing_setup() {
  local script="${SCRIPT_DIR}/scripts/services/landing.sh"
  if [[ ! -f "$script" ]]; then
    echo "scripts/services/landing.sh not found at ${script}" >&2
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
  source "${SCRIPT_DIR}/scripts/lib/tls.sh"
  tls_gen_cert "$@"
}

function command_ssl_proxy() {
  source "${SCRIPT_DIR}/scripts/lib/tls.sh"
  tls_ssl_proxy "$@"
}

# Main command dispatcher
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
  install-fixture) shift; command_install_fixture "$@" ;;
  test-dmx) command_test_dmx ;;
  wifi) command_wifi ;;
  wifi-reconf) command_wifi_reconf ;;
  wifi-status) command_wifi_status ;;
  scan) shift; command_scan "$@" ;;
  update) command_update ;;
  update-qlc) command_update_qlc ;;
  backup) command_backup ;;
  restore) shift; command_restore "$@" ;;
  check) command_check ;;
  validate) command_validate ;;
  diagnose) command_diagnose ;;
  doctor) command_doctor ;;
  perf) shift; command_perf "$@" ;;
  benchmark) command_benchmark ;;
  add-key) shift; command_add_key "$@" ;;
  disable-password-auth) command_disable_password_auth ;;
  static-ip) shift; command_static_ip "$@" ;;
  hdmi-disable) command_hdmi_disable ;;
  os-version) command_os_version ;;
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
