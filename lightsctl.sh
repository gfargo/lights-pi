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
HOSTNAME="${HOSTNAME:-lights}"
QLC_PORT="${QLC_PORT:-9999}"
SERVICE="qlcplus-web.service"
EDITOR="${EDITOR:-nano}"
SSH_KEY="${SSH_KEY:-}"
BACKUP_STORAGE="${BACKUP_STORAGE:-${SCRIPT_DIR}/backups}"
SSL_CERT="${SSL_CERT:-certs/qlc.crt}"
SSL_KEY="${SSL_KEY:-certs/qlc.key}"
SSH_OPTIONS=()
if [[ -n "$SSH_KEY" ]]; then
  SSH_OPTIONS+=("-i" "$SSH_KEY")
fi
REMOTE_CMD=(ssh "${SSH_OPTIONS[@]}" "${PI_USER}@${PI_HOST}")
SCP_CMD=(scp "${SSH_OPTIONS[@]}")

function usage() {
  cat <<EOF
lightsctl.sh [command]

Commands:
  help            Show this summary
  status          systemd status for ${SERVICE}
  restart         restart ${SERVICE}
  logs            last 80 lines from ${SERVICE}
  tail            follow ${SERVICE} logs
  lsusb           show connected USB devices (ENTTEC should appear)
  qlc-version     run qlcplus --version on the Pi
  qlc-headless    push configure_qlc_headless.sh to the Pi and run it (sets QT_QPA_PLATFORM=minimal)
  wifi            dump /etc/wpa_supplicant/wpa_supplicant.conf
  wifi-reconf     run sudo wpa_cli -i wlan0 reconfigure
  wifi-status     run wpa_cli status and ip a show wlan0
  update          sudo apt update && sudo apt -y upgrade
  open-web        print the URLs for the headless UI
  ssh             open an interactive shell on the Pi
  wifi-edit       open the Pi's Wi-Fi config in `$EDITOR`
  edit <path>     open an arbitrary file on the Pi (defaults to the Wi-Fi config)
  backup          pull .config/qlcplus and .qlcplus from ${PI_USER} home to ${BACKUP_STORAGE}
  hdmi-disable    append `hdmi_blanking=2` to `/boot/config.txt`
  ssl-proxy <cert> <key>  install SSL cert, run stunnel, redirect 443 → ${QLC_PORT}
  setup               run scripts/pi_lights_setup.sh (first-time Pi provisioning)
                      requires: WIFI1_SSID, WIFI1_PSK, WIFI2_SSID, WIFI2_PSK

Set env vars to override defaults (PI_HOST, PI_USER, HOSTNAME, QLC_PORT, SSH_KEY, BACKUP_STORAGE, SSL_CERT, SSL_KEY).
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

function command_tail() {
  "${REMOTE_CMD[@]}" sudo journalctl -u "${SERVICE}" -f
}

function command_lsusb() {
  run lsusb
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

function command_open_web() {
  echo "Headless UI: http://${HOSTNAME}.local:${QLC_PORT}"
  echo "Direct IP: http://${PI_HOST}:${QLC_PORT}"
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
  local config="/boot/config.txt"
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

function command_setup() {
  local script="${SCRIPT_DIR}/scripts/pi_lights_setup.sh"
  if [[ ! -f "$script" ]]; then
    echo "scripts/pi_lights_setup.sh not found at ${script}" >&2
    return 1
  fi
  PI_HOST="${PI_HOST}" \
  PI_USER="${PI_USER}" \
  HOSTNAME="${HOSTNAME}" \
  QLC_PORT="${QLC_PORT}" \
  bash "$script"
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
  help) usage ;;
  status) command_status ;;
  restart) command_restart ;;
  logs) command_logs ;;
  tail) command_tail ;;
  lsusb) command_lsusb ;;
  qlc-version) command_qlc_version ;;
  qlc-headless) command_qlc_headless ;;
  wifi) command_wifi ;;
  wifi-reconf) command_wifi_reconf ;;
  wifi-status) command_wifi_status ;;
  update) command_update ;;
  backup) command_backup ;;
  hdmi-disable) command_hdmi_disable ;;
  setup) command_setup ;;
  ssl-proxy) shift; command_ssl_proxy "$@" ;;
  open-web) command_open_web ;;
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
