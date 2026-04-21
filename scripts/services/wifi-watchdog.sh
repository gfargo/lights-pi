#!/usr/bin/env bash
# WiFi Watchdog - monitors connectivity and auto-recovers
# Installed as a systemd timer on the Pi
# Runs every 2 minutes, checks if wlan0 has an IP and can reach the gateway.
# If not, cycles NetworkManager to reconnect.
set -euo pipefail

LOG_TAG="wifi-watchdog"
MAX_FAILURES=3
FAILURE_FILE="/tmp/wifi-watchdog-failures"

log() { logger -t "$LOG_TAG" "$*"; echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }

get_failures() {
  [[ -f "$FAILURE_FILE" ]] && cat "$FAILURE_FILE" || echo 0
}

set_failures() {
  echo "$1" > "$FAILURE_FILE"
}

# Check 1: Does wlan0 have an IP?
has_ip() {
  ip -4 addr show wlan0 2>/dev/null | grep -q 'inet '
}

# Check 2: Can we reach the default gateway?
can_reach_gateway() {
  local gw
  gw=$(ip route show default dev wlan0 2>/dev/null | awk '{print $3}' | head -1)
  [[ -z "$gw" ]] && return 1
  ping -c1 -W3 "$gw" >/dev/null 2>&1
}

# Recovery: cycle the wifi interface
recover() {
  local failures
  failures=$(get_failures)
  ((failures++))
  set_failures "$failures"

  if [[ $failures -ge $MAX_FAILURES ]]; then
    log "WARN: ${failures} consecutive failures — restarting NetworkManager"
    systemctl restart NetworkManager
    sleep 10
    set_failures 0
  else
    log "INFO: recovery attempt ${failures}/${MAX_FAILURES} — reconnecting wlan0"
    nmcli device disconnect wlan0 2>/dev/null || true
    sleep 3
    nmcli device connect wlan0 2>/dev/null || true
    sleep 5
  fi
}

# Main
if has_ip && can_reach_gateway; then
  set_failures 0
  log "OK: wlan0 connected and gateway reachable"
  exit 0
fi

if ! has_ip; then
  log "FAIL: wlan0 has no IP address"
elif ! can_reach_gateway; then
  log "FAIL: cannot reach gateway"
fi

recover
