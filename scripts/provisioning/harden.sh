#!/usr/bin/env bash
# Security hardening for the lights Pi.
# Run via: ./lightsctl.sh harden  (or as part of setup-full)
set -euo pipefail

PI_HOST="${PI_HOST:-}"
PI_USER="${PI_USER:-pi}"
QLC_PORT="${QLC_PORT:-9999}"

if [[ -z "$PI_HOST" ]]; then
  echo "Set PI_HOST to the Pi's IP or hostname (e.g., 192.168.1.50)."
  exit 1
fi

ssh "${PI_USER}@${PI_HOST}" "sudo bash -s" <<EOF
set -euo pipefail

echo "[1/4] Firewall (ufw)"
apt-get install -y -q ufw
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp   comment 'SSH'
ufw allow ${QLC_PORT}/tcp comment 'QLC+ web UI'
ufw --force enable
ufw status verbose

echo "[2/4] Unattended security upgrades"
apt-get install -y -q unattended-upgrades
DEBIAN_FRONTEND=noninteractive dpkg-reconfigure -plow unattended-upgrades
echo "  Security-only upgrades configured."

echo "[3/4] Hardware watchdog"
# Enable BCM watchdog in boot config (path varies by Pi OS version)
BOOT_CONFIG=""
if [[ -f /boot/firmware/config.txt ]]; then
  BOOT_CONFIG="/boot/firmware/config.txt"
elif [[ -f /boot/config.txt ]]; then
  BOOT_CONFIG="/boot/config.txt"
fi
if [[ -n "\${BOOT_CONFIG}" ]]; then
  if ! grep -q 'dtparam=watchdog=on' "\${BOOT_CONFIG}"; then
    echo 'dtparam=watchdog=on' >> "\${BOOT_CONFIG}"
    echo "  dtparam=watchdog=on added to \${BOOT_CONFIG}"
  else
    echo "  watchdog already enabled in \${BOOT_CONFIG}"
  fi
fi
# Have systemd kick the hardware watchdog — no extra daemon needed
if grep -q '^#RuntimeWatchdogSec' /etc/systemd/system.conf; then
  sed -i 's/^#RuntimeWatchdogSec=.*/RuntimeWatchdogSec=15/' /etc/systemd/system.conf
elif ! grep -q '^RuntimeWatchdogSec' /etc/systemd/system.conf; then
  echo 'RuntimeWatchdogSec=15' >> /etc/systemd/system.conf
fi
systemctl daemon-reload
echo "  systemd RuntimeWatchdogSec=15 set."

echo "[4/4] udev rule for ENTTEC DMX USB Pro"
cat > /etc/udev/rules.d/99-enttec-dmx.rules <<'UDEV'
# ENTTEC DMX USB Pro — stable symlink at /dev/dmx0
# Vendor 0403 = FTDI, Product 6001 = FT232 serial (used by ENTTEC Pro)
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6001", SYMLINK+="dmx0", MODE="0660", GROUP="dialout"
UDEV
udevadm control --reload-rules
udevadm trigger
echo "  udev rule installed. ENTTEC will appear at /dev/dmx0 after replug."

EOF

echo ""
echo "Hardening complete."
echo "A reboot is needed to activate the hardware watchdog: ./lightsctl.sh reboot"
