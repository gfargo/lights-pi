#!/usr/bin/env bash
# System utility functions for lightsctl.sh
set -euo pipefail

# Show system health
function system_health() {
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

# Full diagnostic report
function system_diagnose() {
  echo "=== Diagnostic report: $(date -u '+%Y-%m-%d %H:%M:%S UTC') ==="
  echo "    Host: ${PI_HOST}  User: ${PI_USER}  Port: ${QLC_PORT}"
  echo ""
  echo "--- Health ---"
  system_health
  echo ""
  echo "--- Last 20 log lines ---"
  run_sudo journalctl -u "${SERVICE}" -n 20 --no-pager
  echo ""
  echo "--- WiFi ---"
  source "${SCRIPT_DIR}/scripts/wifi_utils.sh"
  wifi_show_status
  echo ""
  echo "--- Uptime / load ---"
  run uptime
}

# Comprehensive system doctor
function system_doctor() {
  local issues=0
  local recommendations=()
  
  echo "=== System Doctor ==="
  echo "Running comprehensive health check..."
  echo ""
  
  # Run validate first
  echo "--- Configuration & Connectivity ---"
  if system_validate >/dev/null 2>&1; then
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

# Performance monitoring
function system_perf() {
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

# System benchmark
function system_benchmark() {
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

# Pre-flight validation
function system_validate() {
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

# Connectivity check
function system_check() {
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

# Show OS version information
function system_os_version() {
  echo "=== Raspberry Pi OS Information ==="
  echo ""
  
  printf '%-20s' "OS Release:"
  if run test -f /etc/os-release 2>/dev/null; then
    local os_name
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

# Export functions
export -f system_health
export -f system_diagnose
export -f system_doctor
export -f system_perf
export -f system_benchmark
export -f system_validate
export -f system_check
export -f system_os_version
