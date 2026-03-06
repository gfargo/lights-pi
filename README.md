# Raspberry Pi Studio Lighting Controller

## Overview

Headless Raspberry Pi lighting controller for a studio environment. Anyone on the network can control lights from a phone or browser via the QLC+ web UI hosted on the Pi.

**Core stack:**
- **QLC+** — open source lighting control software
- **ENTTEC DMX USB Pro** — USB → DMX interface
- **Wireless or wired DMX** — connect any QLC+-compatible fixtures

---

## System Architecture

```
Phones / Tablets / Laptops
            │
            │  WiFi
            ▼
     Raspberry Pi
     (QLC+ Web Server)
            │
            │ USB
            ▼
   ENTTEC DMX USB Pro
            │
            │ DMX
            ▼
  DMX Interface / Transmitter
   (wired or wireless)
            │
            ▼
      DMX Fixtures
```

---

## Hardware

| Device                          | Purpose                          |
| ------------------------------- | -------------------------------- |
| Raspberry Pi (3B+ or newer)     | Lighting controller host         |
| MicroSD Card (16–32 GB)         | OS and configuration             |
| ENTTEC DMX USB Pro              | USB → DMX interface              |
| DMX fixtures                    | Any QLC+-compatible fixture      |
| Wireless DMX system (optional)  | Cable-free fixture control       |
| DMX cables (110 Ω)              | Daisy-chaining wired fixtures    |

---

## Software Stack

| Software             | Purpose                          |
| -------------------- | -------------------------------- |
| Raspberry Pi OS Lite | Lightweight OS                   |
| QLC+                 | Lighting control engine          |
| Avahi                | mDNS (`lights.local`)            |
| nginx                | Landing page (port 80)           |
| systemd              | Autostart services               |
| wpa_supplicant       | WiFi management                  |

---

## Preparing the SD Card (macOS)

1. Download [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. Select **Raspberry Pi OS Lite (64-bit)**
3. Open **Advanced Settings** and configure:

   | Setting       | Value         |
   | ------------- | ------------- |
   | Hostname      | lights        |
   | Enable SSH    | Yes           |
   | Username      | pi            |
   | Password      | your-password |
   | WiFi SSID     | Setup network |
   | WiFi Password | Setup password|
   | Locale        | Your region   |

4. Write to SD card, insert into Pi, power on.
5. Confirm SSH works: `ssh pi@lights.local`

> **Important:** the hostname set in Raspberry Pi Imager must match `PI_HOSTNAME` in your `.env` (default: `lights`). A mismatch means `lights.local` won't resolve via mDNS even though SSH may still work via IP.

---

## Quick Start

### 1. Configure `.env`

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

### 2. Full provisioning

For a new Pi — runs base setup then security hardening in one go:

```bash
WIFI1_SSID="SetupNet" WIFI1_PSK="setup-pass" \
WIFI2_SSID="StudioNet" WIFI2_PSK="studio-pass" \
./lightsctl.sh setup-full
```

Or run them separately if you want to review between steps:

```bash
./lightsctl.sh setup     # installs packages, QLC+, systemd service
./lightsctl.sh harden    # firewall, watchdog, unattended upgrades, udev
```

**`setup` does:**
- Sets hostname and installs required packages
- Configures dual WiFi (studio network takes priority)
- Waits for DNS to recover after WiFi reconfiguration
- Installs QLC+ with automatic retry on network hiccups
- Creates and enables `qlcplus-web.service` with headless Qt configured
- Adds Pi user to `dialout` group for ENTTEC USB access
- Configures persistent systemd journal logs

**`harden` does:**
- Installs `ufw` and opens only SSH (22) and the QLC+ web port
- Configures unattended security upgrades
- Enables the hardware watchdog via systemd (auto-reboots on kernel hang)
- Creates a udev rule so ENTTEC always appears at `/dev/dmx0`

### 3. Open the web UI

```bash
./lightsctl.sh open-web
# → opens http://lights.local:9999 in your browser
```

From here, add your fixtures, map DMX universes, and build a Virtual Console layout for studio use.

### 4. Set up the landing page (optional)

Serves a simple branded page at `http://lights.local` (port 80) with a button linking to the QLC+ web UI:

```bash
./lightsctl.sh landing-setup
```

After editing `landing/index.html`, push updates without reinstalling nginx:

```bash
./lightsctl.sh landing-deploy
```

---

## lightsctl.sh Reference

`lightsctl.sh` is the single entry point for all Pi management.

```
./lightsctl.sh [command]

Provisioning:
  setup-full                    full provisioning: setup then harden (recommended for new Pi)
  setup                         base install (requires: WIFI1_SSID/PSK, WIFI2_SSID/PSK)
  harden                        firewall, watchdog, unattended upgrades, udev rule
  add-key [pubkey]              install local SSH public key on the Pi
  disable-password-auth         disable SSH password login (run add-key first)
  static-ip <ip/prefix> <gw>   write static IP to /etc/dhcpcd.conf and restart
  update                        apt update && apt upgrade on the Pi
  update-qlc                    upgrade only the qlcplus package and restart service

Service Management:
  status                        systemd status for qlcplus-web.service
  restart                       restart qlcplus-web.service
  logs                          last 80 lines from the service journal
  logs-errors                   show only ERROR and WARN lines from logs
  tail                          follow service logs live
  health                        service + web UI + USB + disk + memory + CPU temp
  diagnose                      full diagnostic dump (health + logs + wifi + uptime)
  check                         ping + SSH pre-flight connectivity check
  validate                      pre-flight validation (config, connectivity, dependencies)
  doctor                        comprehensive health check with recommendations
  perf [seconds]                monitor CPU, memory, network usage over time (default: 10s)
  benchmark                     test system performance (web UI latency, network speed)

QLC+:
  qlc-version                   run qlcplus --version on the Pi
  qlc-headless                  push Qt platform fix (sets QT_QPA_PLATFORM=minimal)
  deploy-workspace <file.qxw>   upload workspace to Pi and restart service
  pull-workspace [output.qxw]   download current workspace from Pi
  list-fixtures                 show installed fixture definitions
  install-fixture <file.qxf>    upload and install custom fixture definition
  test-dmx                      verify ENTTEC USB and DMX output capability
  open-web                      open the web UI in the default browser

Network / WiFi:
  wifi                          dump /etc/wpa_supplicant/wpa_supplicant.conf
  wifi-reconf                   run wpa_cli -i wlan0 reconfigure
  wifi-status                   show current SSID and wlan0 address
  wifi-edit                     edit the Wi-Fi config in $EDITOR
  scan                          scan network for Raspberry Pi devices (lights-*.local)

System:
  backup                        pull QLC+ config dirs to BACKUP_STORAGE
  restore <backup.tar.gz>       restore QLC+ config from backup and restart service
  lsusb                         show USB devices (ENTTEC should appear)
  os-version                    show Raspberry Pi OS and kernel version
  hdmi-disable                  disable HDMI output to save power
  reboot                        reboot the Pi
  poweroff                      shut down the Pi
  ssh                           open an interactive shell on the Pi
  edit <path>                   edit an arbitrary file on the Pi

TLS:
  gen-cert [days]               generate a self-signed cert/key in certs/ (default: 730 days)
  ssl-proxy [cert] [key]        install stunnel on Pi, redirect 443 → QLC_PORT

Landing Page (http://lights.local):
  landing-setup                 install nginx and deploy the landing page (first time)
  landing-deploy                push updated landing/index.html (no nginx reinstall)
```

**Environment variables** (set in `.env` or exported):

| Variable         | Default              | Description                     |
| ---------------- | -------------------- | ------------------------------- |
| `PI_HOST`        | `lights.local`       | Pi hostname or IP               |
| `PI_USER`        | `pi`                 | SSH username                    |
| `PI_HOSTNAME`    | `lights`             | mDNS hostname set on the Pi     |
| `QLC_PORT`       | `9999`               | QLC+ web UI port                |
| `SSH_KEY`        | _(none)_             | Path to SSH private key         |
| `BACKUP_STORAGE` | `./backups`          | Local backup destination        |
| `SSL_CERT`       | `certs/qlc.crt`      | TLS certificate for ssl-proxy   |
| `SSL_KEY`        | `certs/qlc.key`      | TLS private key for ssl-proxy   |

You can also use the `Makefile` as a shorthand for all commands:

```bash
# Provisioning
make setup-full
make setup
make harden
make add-key
make static-ip IP=192.168.1.50/24 GW=192.168.1.1

# Service Management
make status
make restart
make logs
make logs-errors
make health
make diagnose
make validate
make doctor
make perf [DURATION=30]
make benchmark

# QLC+
make deploy WS=workspaces/studio.qxw
make pull [OUTPUT=custom.qxw]
make list-fixtures
make install-fixture FIXTURE=path/to/fixture.qxf
make test-dmx
make open

# Network
make wifi-status
make scan

# System
make backup
make restore BACKUP=backups/qlcplus-backup-20260305T203838Z.tar.gz
make os-version
make reboot

# Landing Page
make landing-setup
make landing-deploy
```

---

## Workflow Examples

### Initial Setup
```bash
# 1. Validate your local environment
./lightsctl.sh validate

# 2. Run full provisioning
WIFI1_SSID="SetupNet" WIFI1_PSK="setup-pass" \
WIFI2_SSID="StudioNet" WIFI2_PSK="studio-pass" \
./lightsctl.sh setup-full

# 3. Verify everything is working
./lightsctl.sh doctor
./lightsctl.sh test-dmx
```

### Daily Operations
```bash
# Check system health
./lightsctl.sh health

# View recent errors
./lightsctl.sh logs-errors

# Deploy a workspace
./lightsctl.sh deploy-workspace workspaces/studio.qxw

# Pull workspace after making changes in web UI
./lightsctl.sh pull-workspace workspaces/studio-updated.qxw

# Create backup
./lightsctl.sh backup
```

### Troubleshooting
```bash
# Run comprehensive diagnostics
./lightsctl.sh doctor

# Monitor performance in real-time
./lightsctl.sh perf 30

# Test network and system performance
./lightsctl.sh benchmark

# Find Pi on network if hostname not resolving
./lightsctl.sh scan

# Check DMX hardware and configuration
./lightsctl.sh test-dmx
```

---

## Connecting the ENTTEC DMX USB Pro

Plug into the Pi, then verify detection:

```bash
./lightsctl.sh test-dmx    # comprehensive DMX hardware check
./lightsctl.sh lsusb       # expect: FTDI DMX USB PRO
./lightsctl.sh health      # confirms service + web UI + USB all green
```

After `harden` is run, the device gets a stable symlink at `/dev/dmx0` via udev, so QLC+ always finds it regardless of which USB port it's in.

---

## Configuring Fixtures

Fixture profiles, DMX addresses, and workspace layout are managed entirely within QLC+. Access the designer from any browser at `http://lights.local:9999`.

### Managing Fixtures
```bash
# List installed fixture definitions
./lightsctl.sh list-fixtures

# Install a custom fixture definition
./lightsctl.sh install-fixture path/to/custom-fixture.qxf
```

### Managing Workspaces
Once your workspace is ready, save it to `workspaces/` in this repo:

```bash
# Deploy workspace to Pi
./lightsctl.sh deploy-workspace workspaces/studio.qxw

# Pull current workspace from Pi (after making changes in web UI)
./lightsctl.sh pull-workspace workspaces/studio-updated.qxw
```

---

## Recommended Improvements

### Static IP

Use the built-in command:

```bash
./lightsctl.sh static-ip 192.168.1.50/24 192.168.1.1
# or: make static-ip IP=192.168.1.50/24 GW=192.168.1.1
```

This writes the static config to `/etc/dhcpcd.conf` and restarts the DHCP service automatically.

### HTTPS

Generate a self-signed cert and install it on the Pi:

```bash
./lightsctl.sh gen-cert        # writes certs/qlc.crt + certs/qlc.key
./lightsctl.sh ssl-proxy       # installs stunnel, proxies 443 → QLC_PORT
```

### Backups

Create and restore backups of your QLC+ configuration:

```bash
# Create backup
./lightsctl.sh backup

# Restore from backup
./lightsctl.sh restore backups/qlcplus-backup-20260305T203838Z.tar.gz

# List available backups
ls -lh backups/
```

Backups include `.config/qlcplus` and `.qlcplus` directories from the Pi.

---

## Troubleshooting

### Quick Diagnostics

Run comprehensive health checks:
```bash
./lightsctl.sh doctor      # full health check with recommendations
./lightsctl.sh validate    # pre-flight validation
./lightsctl.sh diagnose    # detailed diagnostic dump
```

### ENTTEC not detected

```bash
./lightsctl.sh test-dmx    # comprehensive DMX hardware check
./lightsctl.sh lsusb       # verify USB device detection
./lightsctl.sh health      # check overall system status
```

If `harden` has been run, the device should appear at `/dev/dmx0` — replug it to trigger the udev rule. If the Pi user can't access the device, confirm they are in the `dialout` group:

```bash
./lightsctl.sh ssh
groups $USER   # should include dialout
```

A logout/login (or reboot) is required for group changes to take effect.

### QLC+ service fails to start

```bash
./lightsctl.sh logs        # view recent logs
./lightsctl.sh logs-errors # filter for errors only
```

If logs show Qt platform errors, run `./lightsctl.sh qlc-headless` to apply the `QT_QPA_PLATFORM=minimal` drop-in. The service has a crash loop guard (`StartLimitBurst=5` in 60 s) — if it keeps restarting, check `logs` for the root cause before it stops trying.

### Performance Issues

Monitor system performance:
```bash
./lightsctl.sh perf 30     # real-time monitoring for 30 seconds
./lightsctl.sh benchmark   # run performance tests
```

### QLC+ web UI connects but browser hangs

If the port is open (TCP connects) but the browser never loads a page, the QLC+ web server thread has stalled — this can happen after a network disruption:

```bash
./lightsctl.sh restart
```

The service recovers cleanly on restart. If it keeps stalling, check `./lightsctl.sh logs-errors` for errors.

### Can't find Pi on network

```bash
./lightsctl.sh scan        # scan for Pi devices on network
./lightsctl.sh check       # test connectivity to configured host
```

Ensure the Pi is powered on, connected to the same network, and that the hostname in `.env` matches what was set during SD card preparation.

### Lights not responding

- Confirm universe output is enabled in QLC+ under **Inputs/Outputs**
- Confirm ENTTEC is selected as the output plugin for the correct universe
- Verify fixture DMX addresses match their DIP switch or menu settings
- If using wireless DMX, confirm the transmitter is in **Transmit** mode and channels match

### DNS fails during setup

`pi_lights_setup.sh` automatically waits up to 60 s for DNS to recover after WiFi reconfiguration and injects `nameserver 1.1.1.1` if still failing. To fix manually on the Pi:

```bash
echo 'nameserver 1.1.1.1' | sudo tee -a /etc/resolv.conf
sudo apt-get update
```

### Network interface commands not found

Raspberry Pi OS Lite does not include `ifup`/`ifdown` or the `networking` systemd unit. Use instead:

```bash
sudo ip link set wlan0 down && sudo ip link set wlan0 up
sudo systemctl restart dhcpcd5
```

---

## Future Enhancements

- ArtNet / sACN output support
- MIDI controller integration
- StreamDeck scene control
- Motion-triggered or scheduled scenes
- Custom web UI (React/Node)
- Dedicated WiFi VLAN for lighting traffic
