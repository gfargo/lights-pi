#!/usr/bin/env bash
# WiFi utility functions for lightsctl.sh
set -euo pipefail

# Display WiFi configuration
function wifi_show_config() {
  run_sudo cat /etc/wpa_supplicant/wpa_supplicant.conf
}

# Reconfigure WiFi
function wifi_reconfigure() {
  run_sudo wpa_cli -i wlan0 reconfigure
}

# Show WiFi status
function wifi_show_status() {
  run_sudo wpa_cli -i wlan0 status
  run ip -br a show wlan0
}

# Edit WiFi configuration
function wifi_edit_config() {
  local target="${1:-/etc/wpa_supplicant/wpa_supplicant.conf}"
  run_sudo "${EDITOR}" "$target"
}

# Export functions
export -f wifi_show_config
export -f wifi_reconfigure
export -f wifi_show_status
export -f wifi_edit_config
