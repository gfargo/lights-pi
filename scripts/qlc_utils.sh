#!/usr/bin/env bash
# QLC+ utility functions for lightsctl.sh
set -euo pipefail

# Show QLC+ version
function qlc_show_version() {
  if run qlcplus --version; then
    :
  else
    echo "qlcplus not installed on ${PI_HOST}, install it manually or rerun the setup script."
  fi
}

# Configure QLC+ for headless operation
function qlc_configure_headless() {
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

# List fixture definitions
function qlc_list_fixtures() {
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

# Install a fixture definition
function qlc_install_fixture() {
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

# Test DMX output capability
function qlc_test_dmx() {
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

# Deploy workspace to Pi
function qlc_deploy_workspace() {
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

# Pull workspace from Pi
function qlc_pull_workspace() {
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

# Open QLC+ web UI
function qlc_open_web() {
  local url="http://${PI_HOSTNAME}.local:${QLC_PORT}"
  echo "Headless UI: ${url}"
  echo "Direct IP:   http://${PI_HOST}:${QLC_PORT}"
  if command -v open >/dev/null 2>&1; then
    open "$url"
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url"
  fi
}

# Export functions
export -f qlc_show_version
export -f qlc_configure_headless
export -f qlc_list_fixtures
export -f qlc_install_fixture
export -f qlc_test_dmx
export -f qlc_deploy_workspace
export -f qlc_pull_workspace
export -f qlc_open_web
