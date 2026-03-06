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
  wifi-edit                     edit the Wi-Fi config in \$EDITOR
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

function command_doctor() {
  local issues=0
  local recommendations=()
  
  echo "=== System Doctor ==="
  echo "Running comprehensive health check..."
  echo ""
  
  # Run validate first
  echo "--- Configuration & Connectivity ---"
  if command_validate >/dev/null 2>&1; then
    echo "✓ Validation passed"
  else
    echo "✗ Validation found issues (run 'validate' for details)"
    ((issues++))
  fi
  
  echo ""
  echo "--- Service Health ---"
  
  # Check service restart count
  local restart_count
  restart_count=$(run_sudo systemctl show "${SERVICE}" -p NRestarts --value 2>/dev/null || echo "0")
  printf '%-30s' "Service restarts:"
  if [[ $restart_count -eq 0 ]]; then
    echo "✓ ${restart_count} (stable)"
  elif [[ $restart_count -lt 5 ]]; then
    echo "⚠ ${restart_count} (monitor for issues)"
    recommendations+=("Service has restarted ${restart_count} times - check logs for errors")
  else
    echo "✗ ${restart_count} (unstable)"
    ((issues++))
    recommendations+=("Service is unstable (${restart_count} restarts) - check logs and consider reinstalling")
  fi
  
  # Check service uptime
  local uptime_sec
  uptime_sec=$(run_sudo systemctl show "${SERVICE}" -p ActiveEnterTimestampMonotonic --value 2>/dev/null || echo "0")
  if [[ $uptime_sec -gt 0 ]]; then
    local current_sec
    current_sec=$(run cat /proc/uptime 2>/dev/null | awk '{print int($1)}' || echo "0")
    local service_uptime=$((current_sec - uptime_sec / 1000000))
    local uptime_hours=$((service_uptime / 3600))
    printf '%-30s' "Service uptime:"
    if [[ $uptime_hours -lt 1 ]]; then
      echo "⚠ ${uptime_hours}h (recently started)"
    else
      echo "✓ ${uptime_hours}h"
    fi
  fi
  
  # Check for errors in recent logs
  printf '%-30s' "Recent errors:"
  local error_count
  error_count=$(run_sudo journalctl -u "${SERVICE}" -n 100 --no-pager 2>/dev/null | grep -icE "error|fail|critical" || echo "0")
  if [[ $error_count -eq 0 ]]; then
    echo "✓ none in last 100 lines"
  elif [[ $error_count -lt 5 ]]; then
    echo "⚠ ${error_count} in last 100 lines"
    recommendations+=("Found ${error_count} errors in logs - run 'logs-errors' to review")
  else
    echo "✗ ${error_count} in last 100 lines"
    ((issues++))
    recommendations+=("High error count (${error_count}) - run 'logs-errors' and 'diagnose' for details")
  fi
  
  echo ""
  echo "--- System Resources ---"
  
  # Memory usage
  local mem_percent
  mem_percent=$(run free 2>/dev/null | awk '/^Mem:/{printf "%.0f", $3/$2*100}' || echo "0")
  printf '%-30s' "Memory usage:"
  if [[ $mem_percent -lt 80 ]]; then
    echo "✓ ${mem_percent}%"
  elif [[ $mem_percent -lt 90 ]]; then
    echo "⚠ ${mem_percent}%"
    recommendations+=("Memory usage is high (${mem_percent}%) - consider reboot if performance degrades")
  else
    echo "✗ ${mem_percent}%"
    ((issues++))
    recommendations+=("Memory critically high (${mem_percent}%) - reboot recommended")
  fi
  
  # CPU temperature
  local temp
  temp=$(run cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo "0")
  if [[ $temp -gt 0 ]]; then
    local temp_c=$((temp / 1000))
    printf '%-30s' "CPU temperature:"
    if [[ $temp_c -lt 70 ]]; then
      echo "✓ ${temp_c}°C"
    elif [[ $temp_c -lt 80 ]]; then
      echo "⚠ ${temp_c}°C (warm)"
      recommendations+=("CPU temperature is ${temp_c}°C - ensure adequate ventilation")
    else
      echo "✗ ${temp_c}°C (hot)"
      ((issues++))
      recommendations+=("CPU temperature is ${temp_c}°C - check cooling, may throttle performance")
    fi
  fi
  
  # Check for available updates
  printf '%-30s' "System updates:"
  local update_count
  update_count=$(run_sudo apt-get update -qq 2>/dev/null && run apt list --upgradable 2>/dev/null | grep -c upgradable || echo "0")
  if [[ $update_count -eq 0 ]]; then
    echo "✓ system up to date"
  else
    echo "⚠ ${update_count} packages can be upgraded"
    recommendations+=("${update_count} packages can be upgraded - run 'update' to apply")
  fi
  
  echo ""
  echo "--- Network ---"
  
  # Check WiFi signal strength
  if run command -v iwconfig >/dev/null 2>&1; then
    local signal
    signal=$(run iwconfig wlan0 2>/dev/null | grep -oP 'Signal level=\K-?\d+' || echo "-100")
    if [[ $signal -ne -100 ]]; then
      printf '%-30s' "WiFi signal:"
      if [[ $signal -gt -50 ]]; then
        echo "✓ ${signal} dBm (excellent)"
      elif [[ $signal -gt -70 ]]; then
        echo "✓ ${signal} dBm (good)"
      elif [[ $signal -gt -80 ]]; then
        echo "⚠ ${signal} dBm (fair)"
        recommendations+=("WiFi signal is weak (${signal} dBm) - consider moving Pi closer to router")
      else
        echo "✗ ${signal} dBm (poor)"
        ((issues++))
        recommendations+=("WiFi signal is poor (${signal} dBm) - connection may be unstable")
      fi
    fi
  fi
  
  echo ""
  echo "--- Backup Status ---"
  
  # Check when last backup was made
  if [[ -d "$BACKUP_STORAGE" ]]; then
    local latest_backup
    latest_backup=$(ls -t "${BACKUP_STORAGE}"/*.tar.gz 2>/dev/null | head -1)
    if [[ -n "$latest_backup" ]]; then
      local backup_age_days
      backup_age_days=$(( ($(date +%s) - $(stat -f %m "$latest_backup" 2>/dev/null || stat -c %Y "$latest_backup" 2>/dev/null || echo "0")) / 86400 ))
      printf '%-30s' "Last backup:"
      if [[ $backup_age_days -eq 0 ]]; then
        echo "✓ today"
      elif [[ $backup_age_days -lt 7 ]]; then
        echo "✓ ${backup_age_days} days ago"
      elif [[ $backup_age_days -lt 30 ]]; then
        echo "⚠ ${backup_age_days} days ago"
        recommendations+=("Last backup was ${backup_age_days} days ago - run 'backup' to create fresh backup")
      else
        echo "✗ ${backup_age_days} days ago"
        ((issues++))
        recommendations+=("Last backup was ${backup_age_days} days ago - backup immediately!")
      fi
    else
      echo "✗ No backups found"
      ((issues++))
      recommendations+=("No backups found - run 'backup' to create your first backup")
    fi
  else
    echo "⚠ Backup directory not found"
    recommendations+=("Backup directory doesn't exist - will be created on first backup")
  fi
  
  echo ""
  echo "=== Summary ==="
  if [[ $issues -eq 0 && ${#recommendations[@]} -eq 0 ]]; then
    echo "✓ System is healthy! No issues or recommendations."
  else
    if [[ $issues -gt 0 ]]; then
      echo "Found ${issues} issue(s) requiring attention"
    fi
    if [[ ${#recommendations[@]} -gt 0 ]]; then
      echo ""
      echo "Recommendations:"
      for rec in "${recommendations[@]}"; do
        echo "  • ${rec}"
      done
    fi
  fi
  
  return $issues
}

function command_perf() {
  local duration="${1:-10}"
  local interval=1
  
  echo "=== Performance Monitor ==="
  echo "Monitoring for ${duration} seconds (Ctrl+C to stop early)..."
  echo ""
  
  # Print header
  printf "%-8s %-10s %-10s %-10s %-10s %-12s %-12s\n" \
    "Time" "CPU%" "Mem%" "Temp°C" "Load" "RX KB/s" "TX KB/s"
  printf "%s\n" "--------------------------------------------------------------------------------"
  
  # Get initial network stats
  local rx_start tx_start
  rx_start=$(run cat /sys/class/net/wlan0/statistics/rx_bytes 2>/dev/null || echo "0")
  tx_start=$(run cat /sys/class/net/wlan0/statistics/tx_bytes 2>/dev/null || echo "0")
  
  local count=0
  while [[ $count -lt $duration ]]; do
    # Get current time
    local timestamp
    timestamp=$(date +%H:%M:%S)
    
    # CPU usage (100 - idle%)
    local cpu_usage
    cpu_usage=$(run top -bn1 2>/dev/null | grep "Cpu(s)" | awk '{print 100-$8}' || echo "0")
    
    # Memory usage
    local mem_usage
    mem_usage=$(run free 2>/dev/null | awk '/^Mem:/{printf "%.1f", $3/$2*100}' || echo "0")
    
    # Temperature
    local temp
    temp=$(run cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo "0")
    local temp_c=$((temp / 1000))
    
    # Load average (1 min)
    local load
    load=$(run uptime 2>/dev/null | awk -F'load average:' '{print $2}' | awk '{print $1}' | tr -d ',' || echo "0")
    
    # Network throughput
    local rx_current tx_current rx_rate tx_rate
    rx_current=$(run cat /sys/class/net/wlan0/statistics/rx_bytes 2>/dev/null || echo "0")
    tx_current=$(run cat /sys/class/net/wlan0/statistics/tx_bytes 2>/dev/null || echo "0")
    
    if [[ $count -gt 0 ]]; then
      rx_rate=$(( (rx_current - rx_start) / 1024 / interval ))
      tx_rate=$(( (tx_current - tx_start) / 1024 / interval ))
    else
      rx_rate=0
      tx_rate=0
    fi
    
    rx_start=$rx_current
    tx_start=$tx_current
    
    # Print stats
    printf "%-8s %-10s %-10s %-10s %-10s %-12s %-12s\n" \
      "$timestamp" "$cpu_usage" "$mem_usage" "$temp_c" "$load" "$rx_rate" "$tx_rate"
    
    ((count++))
    [[ $count -lt $duration ]] && sleep $interval
  done
  
  echo ""
  echo "Monitoring complete."
}

function command_benchmark() {
  echo "=== System Benchmark ==="
  echo "Testing system performance..."
  echo ""
  
  # Test 1: Network latency (ping)
  echo "--- Network Latency ---"
  printf '%-30s' "Ping test (10 packets):"
  local ping_result
  ping_result=$(ping -c10 -W2 "${PI_HOST}" 2>/dev/null | tail -1)
  if [[ -n "$ping_result" ]]; then
    local avg_latency
    avg_latency=$(echo "$ping_result" | awk -F'/' '{print $5}')
    echo "${avg_latency} ms avg"
  else
    echo "failed"
  fi
  
  # Test 2: SSH connection time
  printf '%-30s' "SSH connection time:"
  local ssh_start ssh_end ssh_time
  ssh_start=$(date +%s%N)
  if "${REMOTE_CMD[@]}" -o ConnectTimeout=5 true 2>/dev/null; then
    ssh_end=$(date +%s%N)
    ssh_time=$(( (ssh_end - ssh_start) / 1000000 ))
    echo "${ssh_time} ms"
  else
    echo "failed"
  fi
  
  # Test 3: Web UI response time
  echo ""
  echo "--- Web UI Performance ---"
  printf '%-30s' "Web UI response time:"
  local web_times=()
  for i in {1..5}; do
    local web_start web_end web_time
    web_start=$(date +%s%N)
    if run curl -sf --max-time 5 "http://127.0.0.1:${QLC_PORT}" >/dev/null 2>&1; then
      web_end=$(date +%s%N)
      web_time=$(( (web_end - web_start) / 1000000 ))
      web_times+=("$web_time")
    fi
  done
  
  if [[ ${#web_times[@]} -gt 0 ]]; then
    local sum=0
    for t in "${web_times[@]}"; do
      ((sum += t))
    done
    local avg=$((sum / ${#web_times[@]}))
    echo "${avg} ms avg (${#web_times[@]} samples)"
  else
    echo "failed"
  fi
  
  # Test 4: File transfer speed (small file)
  echo ""
  echo "--- File Transfer Speed ---"
  printf '%-30s' "Upload test (1KB):"
  local test_file
  test_file=$(mktemp)
  dd if=/dev/zero of="$test_file" bs=1024 count=1 2>/dev/null
  
  local upload_start upload_end upload_time
  upload_start=$(date +%s%N)
  if "${SCP_CMD[@]}" "$test_file" "${PI_USER}@${PI_HOST}:/tmp/benchmark-test" 2>/dev/null; then
    upload_end=$(date +%s%N)
    upload_time=$(( (upload_end - upload_start) / 1000000 ))
    echo "${upload_time} ms"
  else
    echo "failed"
  fi
  
  printf '%-30s' "Download test (1KB):"
  local download_start download_end download_time
  download_start=$(date +%s%N)
  if "${SCP_CMD[@]}" "${PI_USER}@${PI_HOST}:/tmp/benchmark-test" "${test_file}.download" 2>/dev/null; then
    download_end=$(date +%s%N)
    download_time=$(( (download_end - download_start) / 1000000 ))
    echo "${download_time} ms"
  else
    echo "failed"
  fi
  
  # Cleanup
  rm -f "$test_file" "${test_file}.download"
  run rm -f /tmp/benchmark-test 2>/dev/null || true
  
  # Test 5: System performance on Pi
  echo ""
  echo "--- Pi System Performance ---"
  printf '%-30s' "CPU speed:"
  local cpu_freq
  cpu_freq=$(run cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null || echo "0")
  if [[ $cpu_freq -gt 0 ]]; then
    local cpu_mhz=$((cpu_freq / 1000))
    echo "${cpu_mhz} MHz"
  else
    echo "n/a"
  fi
  
  printf '%-30s' "Memory speed test:"
  local mem_start mem_end mem_time
  mem_start=$(date +%s%N)
  run dd if=/dev/zero of=/dev/null bs=1M count=100 2>/dev/null || true
  mem_end=$(date +%s%N)
  mem_time=$(( (mem_end - mem_start) / 1000000 ))
  echo "${mem_time} ms (100MB)"
  
  printf '%-30s' "Disk write speed:"
  local disk_start disk_end disk_time
  disk_start=$(date +%s%N)
  run dd if=/dev/zero of=/tmp/benchmark-disk bs=1M count=10 conv=fsync 2>/dev/null || true
  disk_end=$(date +%s%N)
  disk_time=$(( (disk_end - disk_start) / 1000000 ))
  if [[ $disk_time -gt 0 ]]; then
    local disk_speed=$((10000 / disk_time))
    echo "${disk_speed} MB/s"
  else
    echo "n/a"
  fi
  run rm -f /tmp/benchmark-disk 2>/dev/null || true
  
  echo ""
  echo "Benchmark complete."
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

function command_install_fixture() {
  local fixture_file="${1:-}"
  if [[ -z "$fixture_file" ]]; then
    echo "Usage: install-fixture <path/to/fixture.qxf>" >&2
    return 1
  fi
  if [[ ! -f "$fixture_file" ]]; then
    echo "Fixture file not found: ${fixture_file}" >&2
    return 1
  fi
  
  # Validate it's a .qxf file
  if [[ ! "$fixture_file" =~ \.qxf$ ]]; then
    echo "Error: File must have .qxf extension" >&2
    return 1
  fi
  
  local filename remote_dir remote_path
  filename="$(basename "$fixture_file")"
  remote_dir="/home/${PI_USER}/.qlcplus/fixtures"
  remote_path="${remote_dir}/${filename}"
  
  echo "Installing fixture: ${filename}"
  
  # Create fixtures directory if it doesn't exist
  run mkdir -p "$remote_dir"
  
  # Upload the fixture file
  "${SCP_CMD[@]}" "$fixture_file" "${PI_USER}@${PI_HOST}:${remote_path}"
  
  # Fix ownership
  run chown "${PI_USER}:${PI_USER}" "$remote_path"
  run chmod 644 "$remote_path"
  
  echo "Fixture installed to: ${remote_path}"
  echo ""
  echo "Note: Restart QLC+ or reload the workspace to use the new fixture"
  echo "Run: ./lightsctl.sh restart"
}

function command_test_dmx() {
  echo "=== DMX Output Test ==="
  echo "Testing ENTTEC USB and DMX capability..."
  echo ""
  
  # Test 1: Check USB device
  echo "--- USB Device Detection ---"
  printf '%-30s' "ENTTEC USB Pro:"
  if run lsusb 2>/dev/null | grep -qi "FTDI\|0403:6001"; then
    echo "✓ detected"
    run lsusb 2>/dev/null | grep -iE "FTDI|0403:6001"
  else
    echo "✗ not found"
    echo ""
    echo "ENTTEC USB Pro not detected. Check:"
    echo "  • USB cable is connected"
    echo "  • Device is powered"
    echo "  • Try different USB port"
    return 1
  fi
  
  echo ""
  echo "--- Device Permissions ---"
  printf '%-30s' "User in dialout group:"
  if run groups "${PI_USER}" 2>/dev/null | grep -q dialout; then
    echo "✓ yes"
  else
    echo "✗ no"
    echo "Add user to dialout group: sudo usermod -a -G dialout ${PI_USER}"
    echo "Then reboot or log out/in"
  fi
  
  # Check for udev rule
  printf '%-30s' "udev rule for /dev/dmx0:"
  if run test -f /etc/udev/rules.d/99-dmx.rules 2>/dev/null; then
    echo "✓ exists"
    if run test -e /dev/dmx0 2>/dev/null; then
      echo "                              /dev/dmx0 symlink: ✓ present"
    else
      echo "                              /dev/dmx0 symlink: ✗ missing (replug USB)"
    fi
  else
    echo "⚠ not found"
    echo "Run 'harden' to create udev rule for stable /dev/dmx0 symlink"
  fi
  
  echo ""
  echo "--- QLC+ Configuration ---"
  printf '%-30s' "QLC+ service:"
  if run systemctl is-active --quiet "${SERVICE}" 2>/dev/null; then
    echo "✓ running"
  else
    echo "✗ not running"
    echo "Start with: ./lightsctl.sh restart"
  fi
  
  printf '%-30s' "DMX USB plugin:"
  if run test -f /usr/lib/*/qt5/plugins/qlcplus/libdmxusb.so 2>/dev/null; then
    echo "✓ installed"
  else
    echo "⚠ not found (may be in different location)"
  fi
  
  echo ""
  echo "--- DMX Output Test ---"
  echo "To verify DMX output is working:"
  echo ""
  echo "1. Open QLC+ web UI:"
  echo "   ./lightsctl.sh open-web"
  echo ""
  echo "2. Go to Inputs/Outputs tab"
  echo ""
  echo "3. Check that 'DMX USB' appears in the output list"
  echo ""
  echo "4. Enable output for Universe 1 (or your universe)"
  echo ""
  echo "5. In Simple Desk, move a slider and check if your fixture responds"
  echo ""
  echo "If fixtures don't respond:"
  echo "  • Verify fixture DMX address matches QLC+ configuration"
  echo "  • Check DMX cable connections"
  echo "  • Ensure fixtures are powered and in DMX mode"
  echo "  • Try different DMX cable (check for shorts/breaks)"
  echo "  • Verify DMX terminator on last fixture (120Ω resistor)"
  
  echo ""
  echo "Hardware test complete. Use web UI to test actual DMX output."
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

function command_scan() {
  local deep_scan=false
  if [[ "$1" == "--deep" ]]; then
    deep_scan=true
  fi
  
  echo "=== Network Scan for Raspberry Pi Devices ==="
  echo ""
  
  local found=0
  local found_devices=()
  
  # Step 1: Check known hostnames
  echo "--- Hostname Discovery ---"
  local hostnames=("lights.local" "raspberrypi.local")
  
  # Add numbered variants
  for i in {1..5}; do
    hostnames+=("lights${i}.local" "lights-${i}.local")
  done
  
  for hostname in "${hostnames[@]}"; do
    printf '%-25s' "${hostname}:"
    if ping -c1 -W1 "$hostname" >/dev/null 2>&1; then
      local ip
      ip=$(ping -c1 "$hostname" 2>/dev/null | grep -oE '\([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+\)' | head -1 | tr -d '()')
      echo "✓ found at ${ip}"
      found_devices+=("${hostname}|${ip}|hostname")
      ((found++))
    else
      echo "not found"
    fi
  done
  
  # Step 2: arp-scan for MAC addresses
  echo ""
  echo "--- MAC Address Scan ---"
  if command -v arp-scan >/dev/null 2>&1; then
    echo "Scanning local network for Raspberry Pi MAC addresses..."
    echo "(This requires sudo and may take 10-20 seconds)"
    echo ""
    
    # Raspberry Pi Foundation OUI prefixes
    local arp_result
    arp_result=$(sudo arp-scan --localnet 2>/dev/null | grep -iE "Raspberry Pi|b8:27:eb|dc:a6:32|e4:5f:01|28:cd:c1|2c:cf:67" || true)
    
    if [[ -n "$arp_result" ]]; then
      echo "Found Raspberry Pi devices by MAC:"
      echo "$arp_result"
      
      # Parse IPs from arp-scan results
      while IFS= read -r line; do
        local arp_ip
        arp_ip=$(echo "$line" | awk '{print $1}')
        if [[ -n "$arp_ip" && "$arp_ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
          # Check if not already found
          local already_found=false
          for device in "${found_devices[@]}"; do
            if [[ "$device" == *"|${arp_ip}|"* ]]; then
              already_found=true
              break
            fi
          done
          if [[ "$already_found" == false ]]; then
            found_devices+=("unknown|${arp_ip}|mac")
            ((found++))
          fi
        fi
      done <<< "$arp_result"
    else
      echo "No Raspberry Pi devices found via MAC scan"
    fi
  else
    echo "arp-scan not installed (optional but recommended)"
    echo "Install with: brew install arp-scan (macOS) or apt install arp-scan (Linux)"
  fi
  
  # Step 3: IP range scan (if --deep flag or no devices found)
  if [[ "$deep_scan" == true ]] || [[ $found -eq 0 ]]; then
    echo ""
    echo "--- IP Range Scan ---"
    
    # Detect local network range
    local local_ip local_subnet
    if command -v ipconfig >/dev/null 2>&1; then
      # macOS
      local_ip=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "")
    else
      # Linux
      local_ip=$(ip -4 addr show | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | grep -v '127.0.0.1' | head -1)
    fi
    
    if [[ -n "$local_ip" ]]; then
      local_subnet=$(echo "$local_ip" | cut -d. -f1-3)
      echo "Detected local network: ${local_subnet}.0/24"
      echo "Scanning common Pi IPs in ${local_subnet}.x range..."
      echo "(This may take 30-60 seconds)"
      echo ""
      
      # Common static IPs and DHCP range
      local test_ips=()
      for i in {1..20} {50..60} {100..110} {200..210}; do
        test_ips+=("${local_subnet}.${i}")
      done
      
      local scan_found=0
      for test_ip in "${test_ips[@]}"; do
        if ping -c1 -W1 "$test_ip" >/dev/null 2>&1; then
          # Try to identify if it's a Pi by attempting SSH
          local is_pi=false
          local hostname=""
          
          # Quick SSH banner check (non-interactive)
          if timeout 2 bash -c "echo | nc -w1 ${test_ip} 22 2>/dev/null" | grep -qi "raspbian\|raspberry"; then
            is_pi=true
          fi
          
          # Try to get hostname via SSH (if we have keys)
          if [[ -n "$SSH_KEY" ]] && timeout 2 ssh -i "$SSH_KEY" -o ConnectTimeout=1 -o BatchMode=yes -o StrictHostKeyChecking=no "${PI_USER}@${test_ip}" hostname 2>/dev/null | grep -qi "light\|rasp"; then
            is_pi=true
            hostname=$(timeout 2 ssh -i "$SSH_KEY" -o ConnectTimeout=1 -o BatchMode=yes -o StrictHostKeyChecking=no "${PI_USER}@${test_ip}" hostname 2>/dev/null || echo "")
          fi
          
          if [[ "$is_pi" == true ]]; then
            printf '%-20s' "${test_ip}:"
            if [[ -n "$hostname" ]]; then
              echo "✓ Raspberry Pi (${hostname})"
            else
              echo "✓ Likely Raspberry Pi"
            fi
            
            # Check if not already found
            local already_found=false
            for device in "${found_devices[@]}"; do
              if [[ "$device" == *"|${test_ip}|"* ]]; then
                already_found=true
                break
              fi
            done
            if [[ "$already_found" == false ]]; then
              found_devices+=("${hostname:-unknown}|${test_ip}|ipscan")
              ((found++))
              ((scan_found++))
            fi
          fi
        fi
      done
      
      if [[ $scan_found -eq 0 ]]; then
        echo "No Raspberry Pi devices found in IP scan"
      fi
    else
      echo "Could not detect local network range"
    fi
  fi
  
  # Summary
  echo ""
  echo "=== Summary ==="
  if [[ $found -eq 0 ]]; then
    echo "No devices found. Troubleshooting tips:"
    echo "  • Ensure Pi is powered on and connected to network"
    echo "  • Check Pi is on same network/VLAN as this machine"
    echo "  • Run with deep scan: ./lightsctl.sh scan --deep"
    echo "  • Check router's DHCP client list for the Pi"
    echo "  • Try connecting directly via ethernet"
  else
    echo "Found ${found} device(s):"
    echo ""
    printf '%-25s %-20s %-15s\n' "HOSTNAME" "IP ADDRESS" "FOUND VIA"
    printf '%s\n' "----------------------------------------------------------------"
    for device in "${found_devices[@]}"; do
      IFS='|' read -r hostname ip method <<< "$device"
      printf '%-25s %-20s %-15s\n' "$hostname" "$ip" "$method"
    done
    
    echo ""
    echo "To connect to a device:"
    echo "  PI_HOST=<ip_address> ./lightsctl.sh ssh"
    echo ""
    echo "To set as default, update .env file:"
    echo "  PI_HOST=<ip_address>"
  fi
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

function command_os_version() {
  echo "=== Raspberry Pi OS Information ==="
  echo ""
  
  printf '%-20s' "OS Release:"
  if run test -f /etc/os-release 2>/dev/null; then
    local os_name os_version
    os_name=$(run grep '^PRETTY_NAME=' /etc/os-release 2>/dev/null | cut -d'"' -f2)
    echo "${os_name}"
  else
    echo "unknown"
  fi
  
  printf '%-20s' "Kernel:"
  local kernel
  kernel=$(run uname -r 2>/dev/null || echo "unknown")
  echo "${kernel}"
  
  printf '%-20s' "Architecture:"
  local arch
  arch=$(run uname -m 2>/dev/null || echo "unknown")
  echo "${arch}"
  
  printf '%-20s' "Hostname:"
  local hostname
  hostname=$(run hostname 2>/dev/null || echo "unknown")
  echo "${hostname}"
  
  echo ""
  echo "--- Hardware ---"
  
  printf '%-20s' "Model:"
  if run test -f /proc/device-tree/model 2>/dev/null; then
    local model
    model=$(run cat /proc/device-tree/model 2>/dev/null | tr -d '\0')
    echo "${model}"
  else
    echo "unknown"
  fi
  
  printf '%-20s' "Serial:"
  if run test -f /proc/cpuinfo 2>/dev/null; then
    local serial
    serial=$(run grep '^Serial' /proc/cpuinfo 2>/dev/null | awk '{print $3}')
    if [[ -n "$serial" ]]; then
      echo "${serial}"
    else
      echo "unknown"
    fi
  else
    echo "unknown"
  fi
  
  printf '%-20s' "Revision:"
  if run test -f /proc/cpuinfo 2>/dev/null; then
    local revision
    revision=$(run grep '^Revision' /proc/cpuinfo 2>/dev/null | awk '{print $3}')
    if [[ -n "$revision" ]]; then
      echo "${revision}"
    else
      echo "unknown"
    fi
  else
    echo "unknown"
  fi
  
  echo ""
  echo "--- Software Versions ---"
  
  printf '%-20s' "QLC+:"
  if run command -v qlcplus >/dev/null 2>&1; then
    local qlc_version
    qlc_version=$(run qlcplus --version 2>&1 | head -1 || echo "unknown")
    echo "${qlc_version}"
  else
    echo "not installed"
  fi
  
  printf '%-20s' "Python:"
  if run command -v python3 >/dev/null 2>&1; then
    local python_version
    python_version=$(run python3 --version 2>&1 | awk '{print $2}')
    echo "${python_version}"
  else
    echo "not installed"
  fi
  
  printf '%-20s' "Firmware:"
  if run command -v vcgencmd >/dev/null 2>&1; then
    local firmware
    firmware=$(run vcgencmd version 2>/dev/null | head -1)
    echo "${firmware}"
  else
    echo "vcgencmd not available"
  fi
  
  echo ""
  echo "--- System Uptime ---"
  run uptime
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
  install-fixture) shift; command_install_fixture "$@" ;;
  test-dmx) command_test_dmx ;;
  wifi) command_wifi ;;
  wifi-reconf) command_wifi_reconf ;;
  wifi-status) command_wifi_status ;;
  scan) command_scan ;;
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
