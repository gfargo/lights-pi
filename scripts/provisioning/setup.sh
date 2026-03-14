#!/usr/bin/env bash
set -euo pipefail

PI_HOST="${PI_HOST:-}"
PI_USER="${PI_USER:-pi}"

# Setup wifi (the one you're on now) + Studio wifi (where it will live)
WIFI1_SSID="${WIFI1_SSID:-}"
WIFI1_PSK="${WIFI1_PSK:-}"
WIFI2_SSID="${WIFI2_SSID:-}"
WIFI2_PSK="${WIFI2_PSK:-}"

PI_HOSTNAME="${PI_HOSTNAME:-lights}"
QLC_PORT="${QLC_PORT:-9999}"
PI_MODEL="${PI_MODEL:-}"  # Can be set to "3" or "4" to skip detection

if [[ -z "$PI_HOST" ]]; then
  echo "Set PI_HOST to the Pi's IP or hostname (e.g., 192.168.1.50)."
  exit 1
fi

# Detect or prompt for Pi model if not set
if [[ -z "$PI_MODEL" ]]; then
  echo ""
  echo "=== Raspberry Pi Model Detection ==="
  echo ""
  echo "This script can optimize settings based on your Pi model."
  echo ""
  echo "  Pi 4: Uses 64-bit OS, standard configuration"
  echo "  Pi 3: Uses 32-bit OS, applies performance optimizations"
  echo ""
  read -p "Which Raspberry Pi model are you using? [3/4] (default: 4): " PI_MODEL
  PI_MODEL="${PI_MODEL:-4}"
  
  if [[ "$PI_MODEL" != "3" && "$PI_MODEL" != "4" ]]; then
    echo "Invalid model. Please enter 3 or 4."
    exit 1
  fi
  
  echo ""
  if [[ "$PI_MODEL" == "3" ]]; then
    echo "✓ Pi 3 selected - will apply performance optimizations"
  else
    echo "✓ Pi 4 selected - using standard configuration"
  fi
  echo ""
fi

ssh "${PI_USER}@${PI_HOST}" "sudo bash -s" <<EOF
set -euo pipefail

PI_MODEL="${PI_MODEL}"

echo "[1/9] Hostname"
hostnamectl set-hostname "${PI_HOSTNAME}"

echo "[2/9] Packages"
apt-get update
apt-get -y upgrade
apt-get install -y avahi-daemon tmux htop git curl ca-certificates usbutils wpasupplicant iw

systemctl enable avahi-daemon
systemctl start avahi-daemon

echo "[3/9] Configure Wi-Fi with two networks"
WPA_CONF="/etc/wpa_supplicant/wpa_supplicant.conf"
cp -a "\${WPA_CONF}" "\${WPA_CONF}.bak.\$(date +%s)" || true

cat > "\${WPA_CONF}" <<WPA
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country=US

# Prefer studio Wi-Fi when available
network={
  ssid="${WIFI2_SSID}"
  psk="${WIFI2_PSK}"
  priority=20
}

# Setup Wi-Fi (fallback)
network={
  ssid="${WIFI1_SSID}"
  psk="${WIFI1_PSK}"
  priority=10
}
WPA

chmod 600 "\${WPA_CONF}"
systemctl restart wpa_supplicant || true

echo "[4/9] Waiting for network after Wi-Fi reconfiguration"
_dns_ok=0
for _cnt in 1 2 3 4 5 6 7 8 9 10 11 12; do
  if getent hosts deb.debian.org >/dev/null 2>&1; then
    echo "  DNS OK"
    _dns_ok=1
    break
  fi
  echo "  Waiting for network... (\${_cnt}/12)"
  sleep 5
done
if [[ \${_dns_ok} -eq 0 ]]; then
  echo "  DNS still failing; injecting fallback nameserver 1.1.1.1"
  grep -q 'nameserver 1.1.1.1' /etc/resolv.conf || echo 'nameserver 1.1.1.1' >> /etc/resolv.conf
  apt-get update -q
fi

echo "[5/9] Install QLC+ (best effort)"
if apt-cache show qlcplus >/dev/null 2>&1; then
  apt-get install -y qlcplus || {
    echo "  Install failed; refreshing package lists and retrying..."
    apt-get update -q
    apt-get install -y qlcplus || echo "  QLC+ install failed. Run: sudo apt-get install -y qlcplus after network is stable."
  }
else
  echo "QLC+ not found via apt on this image."
  echo "We'll still set up the service assuming /usr/bin/qlcplus exists after you install it."
fi

echo "[6/9] Create QLC+ systemd service (headless web UI)"
SERVICE_FILE="/etc/systemd/system/qlcplus-web.service"

cat > "\${SERVICE_FILE}" <<SERVICE
[Unit]
Description=QLC+ Headless Web Interface
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User=${PI_USER}
Environment=HOME=/home/${PI_USER}
Environment=QT_QPA_PLATFORM=minimal
Environment=XDG_RUNTIME_DIR=/run/qlcplus
RuntimeDirectory=qlcplus
RuntimeDirectoryMode=0700
WorkingDirectory=/home/${PI_USER}
ExecStart=/usr/bin/qlcplus --nogui --web --web-port ${QLC_PORT} --open /home/${PI_USER}/.qlcplus/default.qxw
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable qlcplus-web.service

# Create autostart.qxw symlink so QLC+ loads the workspace automatically.
# QLC+ checks for ~/.qlcplus/autostart.qxw on startup in --nogui --web mode.
# This is more reliable than the --open flag on some Pi builds.
QLCPLUS_DIR="/home/${PI_USER}/.qlcplus"
mkdir -p "\${QLCPLUS_DIR}"
chown ${PI_USER}:${PI_USER} "\${QLCPLUS_DIR}"
if [[ ! -e "\${QLCPLUS_DIR}/autostart.qxw" ]]; then
  if [[ -f "\${QLCPLUS_DIR}/default.qxw" ]]; then
    ln -sf "\${QLCPLUS_DIR}/default.qxw" "\${QLCPLUS_DIR}/autostart.qxw"
    echo "  ✓ Created autostart.qxw symlink → default.qxw"
  else
    echo "  ℹ default.qxw not found yet; create it then run:"
    echo "    ln -sf ~/.qlcplus/default.qxw ~/.qlcplus/autostart.qxw"
  fi
else
  echo "  ✓ autostart.qxw already exists"
fi

systemctl restart qlcplus-web.service || true

echo "[7/9] System configuration"
# Persist journal logs across reboots
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/persistent.conf <<JCONF
[Journal]
Storage=persistent
JCONF
systemctl restart systemd-journald
echo "  Journal storage set to persistent."

# ENTTEC USB access without sudo
usermod -aG dialout ${PI_USER}
echo "  ${PI_USER} added to dialout group (takes effect on next login)."

echo "[8/9] Pi model-specific optimizations"
if [[ "\${PI_MODEL}" == "3" ]]; then
  echo "  Applying Pi 3 performance optimizations..."
  
  # Reduce GPU memory (we don't need graphics)
  CONFIG_TXT="/boot/config.txt"
  if [[ -f /boot/firmware/config.txt ]]; then
    CONFIG_TXT="/boot/firmware/config.txt"
  fi
  
  # GPU memory reduction
  if ! grep -q "^gpu_mem=" "\${CONFIG_TXT}"; then
    echo "gpu_mem=16" >> "\${CONFIG_TXT}"
    echo "    ✓ Reduced GPU memory to 16MB (more RAM for QLC+)"
  fi
  
  # Disable Bluetooth (frees up resources)
  if ! grep -q "^dtoverlay=disable-bt" "\${CONFIG_TXT}"; then
    echo "dtoverlay=disable-bt" >> "\${CONFIG_TXT}"
    systemctl disable hciuart.service 2>/dev/null || true
    systemctl disable bluetooth.service 2>/dev/null || true
    echo "    ✓ Disabled Bluetooth (saves CPU and memory)"
  fi
  
  # Disable HDMI (saves power and memory)
  if ! grep -q "^hdmi_blanking=2" "\${CONFIG_TXT}"; then
    echo "hdmi_blanking=2" >> "\${CONFIG_TXT}"
    echo "    ✓ Disabled HDMI output (saves power)"
  fi
  
  # Optimize swap for low memory
  if [[ -f /etc/dphys-swapfile ]]; then
    sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=512/' /etc/dphys-swapfile
    dphys-swapfile setup 2>/dev/null || true
    dphys-swapfile swapon 2>/dev/null || true
    echo "    ✓ Increased swap to 512MB"
  fi
  
  # Reduce journal size to save SD card writes
  mkdir -p /etc/systemd/journald.conf.d
  cat > /etc/systemd/journald.conf.d/pi3-optimize.conf <<JOPT
[Journal]
SystemMaxUse=50M
RuntimeMaxUse=50M
JOPT
  echo "    ✓ Limited journal size to 50MB (reduces SD card wear)"
  
  # Disable unnecessary services
  for service in triggerhappy.service avahi-daemon.socket cups.service; do
    if systemctl is-enabled "\${service}" >/dev/null 2>&1; then
      systemctl disable "\${service}" 2>/dev/null || true
    fi
  done
  echo "    ✓ Disabled unnecessary services"
  
  # Set CPU governor to performance (better for real-time lighting)
  if [[ -f /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor ]]; then
    echo "performance" > /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || true
    # Make it persistent
    cat > /etc/rc.local <<RCLOCAL
#!/bin/sh -e
echo performance > /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
exit 0
RCLOCAL
    chmod +x /etc/rc.local
    echo "    ✓ Set CPU governor to performance mode"
  fi
  
  echo "  Pi 3 optimizations complete. Reboot recommended for all changes to take effect."
else
  echo "  Pi 4 detected - using standard configuration (no special optimizations needed)"
fi

echo "[9/9] Done"
echo "  ENTTEC check:  lsusb"
echo "  QLC+ web:      http://${PI_HOSTNAME}.local:${QLC_PORT}"
systemctl status qlcplus-web.service --no-pager || true
EOF

echo ""
echo "Done. If you changed Wi-Fi settings, the Pi may switch networks when you move it."
if [[ "$PI_MODEL" == "3" ]]; then
  echo ""
  echo "⚠️  Pi 3 optimizations applied. Reboot recommended:"
  echo "   ./lightsctl.sh reboot"
  echo ""
  echo "Optimizations applied:"
  echo "  • Reduced GPU memory to 16MB"
  echo "  • Disabled Bluetooth"
  echo "  • Disabled HDMI output"
  echo "  • Increased swap to 512MB"
  echo "  • Limited journal size to 50MB"
  echo "  • Set CPU governor to performance mode"
fi