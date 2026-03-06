#!/usr/bin/env bash
set -euo pipefail

PI_HOST="${PI_HOST:-}"
PI_USER="${PI_USER:-pi}"

# Setup wifi (the one you're on now) + Studio wifi (where it will live)
WIFI1_SSID="${WIFI1_SSID:-}"
WIFI1_PSK="${WIFI1_PSK:-}"
WIFI2_SSID="${WIFI2_SSID:-}"
WIFI2_PSK="${WIFI2_PSK:-}"

HOSTNAME="${HOSTNAME:-lights}"
QLC_PORT="${QLC_PORT:-9999}"

if [[ -z "$PI_HOST" ]]; then
  echo "Set PI_HOST to the Pi's IP or hostname (e.g., 192.168.1.50)."
  exit 1
fi

ssh "${PI_USER}@${PI_HOST}" "sudo bash -s" <<EOF
set -euo pipefail

echo "[1/8] Hostname"
hostnamectl set-hostname "${HOSTNAME}"

echo "[2/8] Packages"
apt-get update
apt-get -y upgrade
apt-get install -y avahi-daemon tmux htop git curl ca-certificates usbutils wpasupplicant iw

systemctl enable avahi-daemon
systemctl start avahi-daemon

echo "[3/8] Configure Wi-Fi with two networks"
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

echo "[4/8] Waiting for network after Wi-Fi reconfiguration"
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

echo "[5/8] Install QLC+ (best effort)"
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

echo "[6/8] Create QLC+ systemd service (headless web UI)"
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
WorkingDirectory=/home/${PI_USER}
ExecStart=/usr/bin/qlcplus --nogui --web --web-port ${QLC_PORT} --operate
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable qlcplus-web.service
systemctl restart qlcplus-web.service || true

echo "[7/8] System configuration"
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

echo "[8/8] Done"
echo "  ENTTEC check:  lsusb"
echo "  QLC+ web:      http://${HOSTNAME}.local:${QLC_PORT}"
systemctl status qlcplus-web.service --no-pager || true
EOF

echo ""
echo "Done. If you changed Wi-Fi settings, the Pi may switch networks when you move it."