#!/usr/bin/env bash
# WiFi utility functions for lightsctl.sh
set -euo pipefail

# Detect which network manager is in use
function detect_network_manager() {
  # Check if NetworkManager is active
  if run systemctl is-active NetworkManager >/dev/null 2>&1; then
    echo "networkmanager"
    return 0
  fi
  
  # Check if wpa_supplicant is managing WiFi directly
  if run systemctl is-active wpa_supplicant >/dev/null 2>&1; then
    # Check if it's being used by NetworkManager or standalone
    if run_sudo wpa_cli -i wlan0 status >/dev/null 2>&1; then
      echo "wpa_supplicant"
      return 0
    fi
  fi
  
  echo "unknown"
  return 1
}

# Display WiFi configuration (adapts to network manager)
function wifi_show_config() {
  local manager
  manager=$(detect_network_manager)
  
  echo "Network Manager: ${manager}"
  echo ""
  
  case "$manager" in
    networkmanager)
      echo "=== NetworkManager Connections ==="
      run_sudo nmcli connection show | grep -E "NAME|wifi"
      echo ""
      echo "=== wpa_supplicant.conf (not used by NetworkManager) ==="
      run_sudo cat /etc/wpa_supplicant/wpa_supplicant.conf
      ;;
    wpa_supplicant)
      echo "=== wpa_supplicant.conf ==="
      run_sudo cat /etc/wpa_supplicant/wpa_supplicant.conf
      ;;
    *)
      echo "⚠️  Could not detect network manager"
      echo "Showing wpa_supplicant.conf:"
      run_sudo cat /etc/wpa_supplicant/wpa_supplicant.conf
      ;;
  esac
}

# Reconfigure WiFi (adapts to network manager)
function wifi_reconfigure() {
  local manager
  manager=$(detect_network_manager)
  
  echo "Reconfiguring WiFi (using ${manager})..."
  echo ""
  
  case "$manager" in
    networkmanager)
      echo "Reloading NetworkManager connections..."
      run_sudo nmcli connection reload
      sleep 2
      
      echo ""
      echo "Configured networks:"
      run_sudo nmcli connection show | grep wifi
      ;;
    wpa_supplicant)
      echo "1. Reloading wpa_supplicant configuration..."
      run_sudo wpa_cli -i wlan0 reconfigure
      sleep 2
      
      echo "2. Checking loaded networks..."
      run_sudo wpa_cli -i wlan0 list_networks
      ;;
    *)
      echo "⚠️  Could not detect network manager"
      echo "Trying wpa_supplicant method..."
      run_sudo wpa_cli -i wlan0 reconfigure || echo "Failed"
      ;;
  esac
  
  echo ""
  echo "If networks are not showing up, try:"
  echo "  ./lightsctl.sh wifi-restart"
}

# Show WiFi status (adapts to network manager)
function wifi_show_status() {
  local manager
  manager=$(detect_network_manager)
  
  echo "Network Manager: ${manager}"
  echo ""
  
  case "$manager" in
    networkmanager)
      echo "=== NetworkManager Status ==="
      run_sudo nmcli device status | grep -E "DEVICE|wlan0"
      echo ""
      echo "=== Current Connection ==="
      run_sudo nmcli connection show --active | grep wifi
      echo ""
      echo "=== Interface Details ==="
      run ip -br a show wlan0
      ;;
    wpa_supplicant)
      echo "=== wpa_supplicant Status ==="
      run_sudo wpa_cli -i wlan0 status
      echo ""
      echo "=== Interface Details ==="
      run ip -br a show wlan0
      ;;
    *)
      echo "⚠️  Could not detect network manager"
      echo "Showing interface details:"
      run ip -br a show wlan0
      ;;
  esac
}

# Comprehensive WiFi diagnostics (adapts to network manager)
function wifi_diagnose() {
  local manager
  manager=$(detect_network_manager)
  
  echo "=== WiFi Diagnostics ==="
  echo "Network Manager: ${manager}"
  echo ""
  
  case "$manager" in
    networkmanager)
      echo "--- NetworkManager Status ---"
      run systemctl status NetworkManager --no-pager || true
      
      echo ""
      echo "--- Current Connection ---"
      run_sudo nmcli device status
      
      echo ""
      echo "--- Active Connections ---"
      run_sudo nmcli connection show --active
      
      echo ""
      echo "--- Configured Networks ---"
      run_sudo nmcli connection show | grep wifi
      
      echo ""
      echo "--- Available Networks (scan) ---"
      run_sudo nmcli device wifi list
      
      echo ""
      echo "--- Recent NetworkManager Logs ---"
      run_sudo journalctl -u NetworkManager -n 30 --no-pager
      ;;
    wpa_supplicant)
      echo "--- wpa_supplicant Status ---"
      run systemctl status wpa_supplicant --no-pager || true
      
      echo ""
      echo "--- Current Connection ---"
      run_sudo wpa_cli -i wlan0 status
      
      echo ""
      echo "--- Available Networks (scan) ---"
      run_sudo wpa_cli -i wlan0 scan
      sleep 2
      run_sudo wpa_cli -i wlan0 scan_results | head -20
      
      echo ""
      echo "--- Configured Networks ---"
      run_sudo wpa_cli -i wlan0 list_networks
      
      echo ""
      echo "--- Recent wpa_supplicant Logs ---"
      run_sudo journalctl -u wpa_supplicant -n 30 --no-pager
      ;;
    *)
      echo "⚠️  Could not detect network manager"
      echo "Showing basic network information..."
      ;;
  esac
  
  echo ""
  echo "--- Network Interface ---"
  run ip addr show wlan0
  
  echo ""
  echo "--- DNS Resolution ---"
  run cat /etc/resolv.conf
  
  echo ""
  echo "--- Routing Table ---"
  run ip route
  
  echo ""
  echo "=== Troubleshooting Tips ==="
  case "$manager" in
    networkmanager)
      echo "Using NetworkManager:"
      echo "  1. Add networks: ./lightsctl.sh wifi-add-network <SSID> <password> [priority]"
      echo "  2. List networks: ./lightsctl.sh wifi-list"
      echo "  3. Connect: ./lightsctl.sh wifi-connect <SSID>"
      echo "  4. Reconnect: ./lightsctl.sh wifi-reconnect"
      ;;
    wpa_supplicant)
      echo "Using wpa_supplicant:"
      echo "  1. Edit config: ./lightsctl.sh wifi-edit"
      echo "  2. Reload: ./lightsctl.sh wifi-reconf"
      echo "  3. Restart: ./lightsctl.sh wifi-restart"
      echo "  4. Check if network is 2.4GHz (Pi 3 doesn't support 5GHz on some models)"
      ;;
    *)
      echo "  1. Check which network manager is installed"
      echo "  2. Verify WiFi hardware is working: ip link show wlan0"
      ;;
  esac
}

# Force WiFi to reconnect and select best network (adapts to network manager)
function wifi_reconnect() {
  local manager
  manager=$(detect_network_manager)
  
  echo "Forcing WiFi reconnection (using ${manager})..."
  echo ""
  
  case "$manager" in
    networkmanager)
      echo "1. Disconnecting from current network..."
      run_sudo nmcli device disconnect wlan0
      sleep 2
      
      echo "2. Reconnecting (will select highest priority available network)..."
      run_sudo nmcli device connect wlan0
      sleep 3
      
      echo ""
      echo "Current status:"
      run_sudo nmcli device status | grep wlan0
      ;;
    wpa_supplicant)
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
      ;;
    *)
      echo "⚠️  Could not detect network manager"
      echo "Cannot reconnect automatically"
      return 1
      ;;
  esac
  
  echo ""
  echo "If still not connected, try:"
  echo "  ./lightsctl.sh wifi-diagnose"
}

# Restart network service (adapts to network manager)
function wifi_restart() {
  local manager
  manager=$(detect_network_manager)
  
  echo "Restarting network service (using ${manager})..."
  echo ""
  
  case "$manager" in
    networkmanager)
      echo "This will:"
      echo "  • Restart NetworkManager service"
      echo "  • Reload all network configurations"
      echo "  • Reconnect to highest priority available network"
      ;;
    wpa_supplicant)
      echo "This will:"
      echo "  • Reload /etc/wpa_supplicant/wpa_supplicant.conf"
      echo "  • Disconnect from current network"
      echo "  • Reconnect to highest priority available network"
      ;;
    *)
      echo "⚠️  Could not detect network manager"
      return 1
      ;;
  esac
  
  echo ""
  read -p "Continue? [y/N] " -n 1 -r
  echo
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    return 0
  fi
  
  case "$manager" in
    networkmanager)
      echo "Restarting NetworkManager..."
      run_sudo systemctl restart NetworkManager
      sleep 5
      
      echo ""
      echo "Checking status..."
      run_sudo nmcli device status
      
      echo ""
      echo "Active connections:"
      run_sudo nmcli connection show --active
      ;;
    wpa_supplicant)
      echo "Restarting wpa_supplicant..."
      run_sudo systemctl restart wpa_supplicant
      sleep 3
      
      echo ""
      echo "Checking status..."
      run_sudo wpa_cli -i wlan0 status
      
      echo ""
      echo "Loaded networks:"
      run_sudo wpa_cli -i wlan0 list_networks
      ;;
  esac
}

# Edit WiFi configuration (adapts to network manager)
function wifi_edit_config() {
  local manager
  manager=$(detect_network_manager)
  local target="${1:-/etc/wpa_supplicant/wpa_supplicant.conf}"
  local editor="${EDITOR:-nano}"
  
  case "$manager" in
    networkmanager)
      echo "⚠️  This system uses NetworkManager, not wpa_supplicant directly."
      echo ""
      echo "Editing wpa_supplicant.conf won't affect NetworkManager."
      echo "Use 'wifi-add-network' to add networks properly."
      echo ""
      read -p "Continue editing wpa_supplicant.conf anyway? [y/N] " -n 1 -r
      echo
      if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Cancelled. Use: ./lightsctl.sh wifi-add-network"
        return 0
      fi
      ;;
    wpa_supplicant)
      echo "Editing wpa_supplicant configuration..."
      echo "After editing, run: ./lightsctl.sh wifi-restart"
      echo ""
      ;;
    *)
      echo "⚠️  Could not detect network manager"
      echo "Editing wpa_supplicant.conf (may not take effect)"
      echo ""
      ;;
  esac
  
  # Use ssh -t to allocate a pseudo-terminal for interactive editing
  ssh -t "${SSH_OPTIONS[@]}" "${PI_USER}@${PI_HOST}" sudo "$editor" "$target"
}

# Add a WiFi network using NetworkManager
function wifi_add_network() {
  local ssid="${1:-}"
  local password="${2:-}"
  local priority="${3:-0}"
  
  if [[ -z "$ssid" ]]; then
    echo "Usage: wifi-add-network <SSID> <password> [priority]"
    echo ""
    echo "Example:"
    echo "  ./lightsctl.sh wifi-add-network \"CBCI-F2B8\" \"eagle4691buckle\" 30"
    echo ""
    echo "Priority: Higher number = higher priority (default: 0)"
    return 1
  fi
  
  if [[ -z "$password" ]]; then
    echo "Error: Password is required"
    return 1
  fi
  
  echo "Adding WiFi network: ${ssid}"
  echo "Priority: ${priority}"
  echo ""
  
  # Add the network using nmcli
  run_sudo nmcli connection add \
    type wifi \
    con-name "netplan-wlan0-${ssid}" \
    ifname wlan0 \
    ssid "${ssid}" \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.psk "${password}" \
    connection.autoconnect-priority "${priority}"
  
  echo ""
  echo "Network added successfully!"
  echo ""
  echo "To connect now:"
  echo "  ./lightsctl.sh wifi-connect \"${ssid}\""
  echo ""
  echo "To see all networks:"
  echo "  ./lightsctl.sh wifi-list"
}

# List all configured WiFi networks
function wifi_list() {
  echo "=== Configured WiFi Networks ==="
  run_sudo nmcli connection show | grep wifi
  
  echo ""
  echo "=== Available WiFi Networks ==="
  run_sudo nmcli device wifi list
}

# Connect to a specific WiFi network
function wifi_connect() {
  local ssid="${1:-}"
  
  if [[ -z "$ssid" ]]; then
    echo "Usage: wifi-connect <SSID>"
    echo ""
    echo "Available networks:"
    run_sudo nmcli connection show | grep wifi
    return 1
  fi
  
  echo "Connecting to: ${ssid}"
  run_sudo nmcli connection up "netplan-wlan0-${ssid}"
  
  echo ""
  echo "Current status:"
  run_sudo nmcli device status
}

# Export functions
export -f detect_network_manager
export -f wifi_show_config
export -f wifi_reconfigure
export -f wifi_show_status
export -f wifi_diagnose
export -f wifi_reconnect
export -f wifi_restart
export -f wifi_edit_config
export -f wifi_add_network
export -f wifi_list
export -f wifi_connect
