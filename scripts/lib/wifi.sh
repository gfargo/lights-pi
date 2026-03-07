#!/usr/bin/env bash
# WiFi utility functions for lightsctl.sh
set -euo pipefail

# Display WiFi configuration
function wifi_show_config() {
  run_sudo cat /etc/wpa_supplicant/wpa_supplicant.conf
}

# Reconfigure WiFi
function wifi_reconfigure() {
  echo "Reconfiguring WiFi..."
  echo ""
  
  echo "1. Reloading wpa_supplicant configuration..."
  run_sudo wpa_cli -i wlan0 reconfigure
  sleep 2
  
  echo "2. Checking loaded networks..."
  run_sudo wpa_cli -i wlan0 list_networks
  
  echo ""
  echo "If networks are not showing up, try:"
  echo "  ./lightsctl.sh wifi-restart"
}

# Show WiFi status
function wifi_show_status() {
  run_sudo wpa_cli -i wlan0 status
  run ip -br a show wlan0
}

# Comprehensive WiFi diagnostics
function wifi_diagnose() {
  echo "=== WiFi Diagnostics ==="
  echo ""
  
  echo "--- Current Connection ---"
  run_sudo wpa_cli -i wlan0 status
  
  echo ""
  echo "--- Network Interface ---"
  run ip addr show wlan0
  
  echo ""
  echo "--- Available Networks (scan) ---"
  run_sudo wpa_cli -i wlan0 scan
  sleep 2
  run_sudo wpa_cli -i wlan0 scan_results | head -20
  
  echo ""
  echo "--- Configured Networks ---"
  run_sudo wpa_cli -i wlan0 list_networks
  
  echo ""
  echo "--- Recent WiFi Logs ---"
  run_sudo journalctl -u wpa_supplicant -n 30 --no-pager
  
  echo ""
  echo "--- Network Manager Status ---"
  run systemctl status wpa_supplicant --no-pager || true
  
  echo ""
  echo "--- DNS Resolution ---"
  run cat /etc/resolv.conf
  
  echo ""
  echo "--- Routing Table ---"
  run ip route
  
  echo ""
  echo "=== Troubleshooting Tips ==="
  echo "If not connected:"
  echo "  1. Check if SSID is in range: look for it in 'Available Networks' above"
  echo "  2. Verify password is correct in wpa_supplicant.conf"
  echo "  3. Check if network is 2.4GHz (Pi 3 doesn't support 5GHz on some models)"
  echo "  4. Try: ./lightsctl.sh wifi-reconf"
  echo "  5. Check logs for authentication failures"
}

# Force WiFi to reconnect and select best network
function wifi_reconnect() {
  echo "Forcing WiFi reconnection..."
  echo ""
  
  echo "1. Disconnecting from current network..."
  run_sudo wpa_cli -i wlan0 disconnect
  sleep 2
  
  echo "2. Scanning for available networks..."
  run_sudo wpa_cli -i wlan0 scan
  sleep 3
  
  echo "3. Reconnecting (will select highest priority available network)..."
  run_sudo wpa_cli -i wlan0 reconnect
  sleep 3
  
  echo "4. Reconfiguring..."
  run_sudo wpa_cli -i wlan0 reconfigure
  sleep 2
  
  echo ""
  echo "Current status:"
  run_sudo wpa_cli -i wlan0 status
  
  echo ""
  echo "If still not connected, try:"
  echo "  ./lightsctl.sh wifi-diagnose"
}

# Restart wpa_supplicant service (reloads config from file)
function wifi_restart() {
  echo "Restarting wpa_supplicant service..."
  echo ""
  
  echo "This will:"
  echo "  • Reload /etc/wpa_supplicant/wpa_supplicant.conf"
  echo "  • Disconnect from current network"
  echo "  • Reconnect to highest priority available network"
  echo ""
  
  read -p "Continue? [y/N] " -n 1 -r
  echo
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    return 0
  fi
  
  echo "Restarting wpa_supplicant..."
  run_sudo systemctl restart wpa_supplicant
  sleep 3
  
  echo ""
  echo "Checking status..."
  run_sudo wpa_cli -i wlan0 status
  
  echo ""
  echo "Loaded networks:"
  run_sudo wpa_cli -i wlan0 list_networks
}

# Edit WiFi configuration
function wifi_edit_config() {
  local target="${1:-/etc/wpa_supplicant/wpa_supplicant.conf}"
  local editor="${EDITOR:-nano}"
  
  # Use ssh -t to allocate a pseudo-terminal for interactive editing
  ssh -t "${SSH_OPTIONS[@]}" "${PI_USER}@${PI_HOST}" sudo "$editor" "$target"
}

# Export functions
export -f wifi_show_config
export -f wifi_reconfigure
export -f wifi_show_status
export -f wifi_diagnose
export -f wifi_reconnect
export -f wifi_restart
export -f wifi_edit_config
