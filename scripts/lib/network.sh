#!/usr/bin/env bash
# Network utility functions for lightsctl.sh
set -euo pipefail

# Scan for Raspberry Pi devices on the network
function scan_network() {
  local deep_scan=false
  if [[ "${1:-}" == "--deep" ]]; then
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
    
    # Try ping first (most reliable)
    local ip=""
    if ping -c1 -W2 "$hostname" >/dev/null 2>&1; then
      ip=$(ping -c1 -W2 "$hostname" 2>/dev/null | grep -oE '\([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+\)' | head -1 | tr -d '()')
    fi
    
    # Try DNS lookup if ping failed
    if [[ -z "$ip" ]] && command -v dig >/dev/null 2>&1; then
      ip=$(timeout 2 dig +short +time=1 +tries=1 "$hostname" 2>/dev/null | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | head -1 || true)
    fi
    
    if [[ -n "$ip" ]]; then
      echo "✓ found at ${ip}"
      found_devices+=("${hostname}|${ip}|hostname")
      ((found++))
    else
      echo "not found"
    fi
  done
  
  # Step 2: Check ARP cache (fast, no sudo needed)
  echo ""
  echo "--- ARP Cache Check ---"
  if command -v arp >/dev/null 2>&1; then
    local arp_cache
    arp_cache=$(arp -a 2>/dev/null | grep -E '\([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+\)' || true)
    if [[ -n "$arp_cache" ]]; then
      echo "Checking ARP cache for Pi devices..."
      local arp_found=0
      while IFS= read -r line; do
        local cache_hostname cache_ip
        cache_hostname=$(echo "$line" | awk '{print $1}')
        cache_ip=$(echo "$line" | grep -oE '\([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+\)' | tr -d '()')
        
        # Check if hostname suggests it's a Pi
        if [[ "$cache_hostname" =~ (light|rasp|pi) ]]; then
          # Check if not already found
          local already_found=false
          for device in "${found_devices[@]}"; do
            if [[ "$device" == *"|${cache_ip}|"* ]]; then
              already_found=true
              break
            fi
          done
          if [[ "$already_found" == false ]]; then
            echo "  ${cache_hostname} at ${cache_ip}"
            found_devices+=("${cache_hostname}|${cache_ip}|arp-cache")
            ((found++))
            ((arp_found++))
          fi
        fi
      done <<< "$arp_cache"
      
      if [[ $arp_found -eq 0 ]]; then
        echo "No Pi-like devices in ARP cache"
      fi
    else
      echo "ARP cache is empty"
    fi
  fi
  
  # Step 3: arp-scan for MAC addresses (requires sudo)
  echo ""
  echo "--- MAC Address Scan ---"
  if command -v arp-scan >/dev/null 2>&1; then
    echo "Scanning local network for Raspberry Pi MAC addresses..."
    echo "(This requires sudo and may take 10-20 seconds)"
    echo ""
    
    # Raspberry Pi Foundation OUI prefixes
    local arp_result
    arp_result=$(sudo arp-scan --localnet --retry=3 --timeout=500 2>/dev/null | grep -iE "Raspberry Pi|b8:27:eb|dc:a6:32|e4:5f:01|28:cd:c1|2c:cf:67|d8:3a:dd" || true)
    
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
  
  # Step 4: Check for SSH port 22 on found IPs
  if [[ $found -gt 0 ]]; then
    echo ""
    echo "--- SSH Connectivity Check ---"
    for device in "${found_devices[@]}"; do
      IFS='|' read -r hostname ip method <<< "$device"
      printf '%-25s' "${ip}:"
      if timeout 2 bash -c "echo | nc -w1 ${ip} 22 2>/dev/null" | grep -q "SSH"; then
        echo "✓ SSH port open"
      else
        echo "⚠ SSH not responding"
      fi
    done
  fi
  
  # Step 5: IP range scan (if --deep flag or no devices found)
  if [[ "$deep_scan" == true ]] || [[ $found -eq 0 ]]; then
    echo ""
    echo "--- IP Range Scan ---"
    
    # Detect local network range
    local local_ip local_subnet
    if command -v ipconfig >/dev/null 2>&1; then
      # macOS - try all common interfaces
      for iface in en0 en1 en2 en3; do
        local_ip=$(ipconfig getifaddr "$iface" 2>/dev/null || true)
        [[ -n "$local_ip" ]] && break
      done
    else
      # Linux
      local_ip=$(ip -4 addr show 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | grep -v '127.0.0.1' | head -1 || true)
    fi
    
    if [[ -n "$local_ip" ]]; then
      local_subnet=$(echo "$local_ip" | cut -d. -f1-3)
      echo "Detected local network: ${local_subnet}.0/24 (your IP: ${local_ip})"
      echo "Scanning common Pi IPs in ${local_subnet}.x range..."
      echo "(This may take 30-60 seconds - checking ~80 IPs)"
      echo ""
      
      # Common static IPs and DHCP range
      local test_ips=()
      for i in {1..20} {50..60} {100..110} {200..210}; do
        test_ips+=("${local_subnet}.${i}")
      done
      
      local scan_found=0
      local checked=0
      for test_ip in "${test_ips[@]}"; do
        ((checked++))
        # Show progress every 20 IPs
        if (( checked % 20 == 0 )); then
          echo "  Checked ${checked}/${#test_ips[@]} IPs..."
        fi
        
        # Quick ping check first
        if ping -c1 -W1 "$test_ip" >/dev/null 2>&1; then
          # Try to identify if it's a Pi by attempting SSH
          local is_pi=false
          local pi_hostname=""
          
          # Quick SSH banner check (non-interactive)
          local ssh_banner
          ssh_banner=$(timeout 2 bash -c "echo | nc -w1 ${test_ip} 22 2>/dev/null" || true)
          if echo "$ssh_banner" | grep -qi "raspbian\|raspberry\|debian"; then
            is_pi=true
          fi
          
          # Try to get hostname via reverse DNS
          if command -v host >/dev/null 2>&1; then
            pi_hostname=$(timeout 2 host "$test_ip" 2>/dev/null | awk '/domain name pointer/{print $NF}' | sed 's/\.$//' || true)
          fi
          
          # If hostname suggests Pi, mark as Pi
          if [[ "$pi_hostname" =~ (light|rasp|pi) ]]; then
            is_pi=true
          fi
          
          if [[ "$is_pi" == true ]]; then
            printf '%-20s' "${test_ip}:"
            if [[ -n "$pi_hostname" ]]; then
              echo "✓ Raspberry Pi (${pi_hostname})"
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
              found_devices+=("${pi_hostname:-unknown}|${test_ip}|ipscan")
              ((found++))
              ((scan_found++))
            fi
          fi
        fi
      done
      
      echo ""
      if [[ $scan_found -eq 0 ]]; then
        echo "No Raspberry Pi devices found in IP scan"
      else
        echo "Found ${scan_found} device(s) via IP scan"
      fi
    else
      echo "Could not detect local network range"
      echo "Your machine may not be connected to a network"
    fi
  fi
  
  # Summary
  echo ""
  echo "=== Summary ==="
  if [[ $found -eq 0 ]]; then
    echo "No devices found."
    echo ""
    echo "=== Diagnostics ==="
    
    # Check network connectivity
    printf '%-30s' "Internet connectivity:"
    if ping -c1 -W2 8.8.8.8 >/dev/null 2>&1; then
      echo "✓ OK"
    else
      echo "✗ No internet (check network connection)"
    fi
    
    # Check mDNS
    printf '%-30s' "mDNS (Bonjour) service:"
    if command -v dns-sd >/dev/null 2>&1; then
      echo "✓ Available"
    else
      echo "⚠ Not available (hostname.local may not work)"
    fi
    
    # Check if on VPN
    printf '%-30s' "VPN detected:"
    if ifconfig 2>/dev/null | grep -q "utun\|tun\|ppp"; then
      echo "⚠ Yes (may block local network access)"
    else
      echo "✓ No"
    fi
    
    # Show active network interfaces
    echo ""
    echo "Active network interfaces:"
    if command -v ifconfig >/dev/null 2>&1; then
      ifconfig 2>/dev/null | grep -E "^[a-z]|inet " | grep -v "127.0.0.1" | head -10
    fi
    
    echo ""
    echo "=== Troubleshooting Tips ==="
    echo "  1. Ensure Pi is powered on (check for LED activity)"
    echo "  2. Verify Pi is connected to WiFi (check router's client list)"
    echo "  3. Ensure you're on the same network as the Pi"
    echo "  4. If on VPN, try disconnecting temporarily"
    echo "  5. Try deep scan: ./lightsctl.sh scan --deep"
    echo "  6. Check router's DHCP client list for 'raspberrypi' or 'lights'"
    echo "  7. Connect Pi via ethernet cable for initial setup"
    echo "  8. If you know the IP, connect directly:"
    echo "     PI_HOST=<ip_address> ./lightsctl.sh ssh"
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
    echo "=== Next Steps ==="
    echo "To connect to a device:"
    echo "  PI_HOST=<ip_address> ./lightsctl.sh ssh"
    echo ""
    echo "To set as default, update .env file:"
    echo "  PI_HOST=<ip_address>"
    echo ""
    echo "To check device health:"
    echo "  PI_HOST=<ip_address> ./lightsctl.sh health"
  fi
}

# Export functions
export -f scan_network
