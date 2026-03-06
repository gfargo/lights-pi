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

| Software             | Purpose                 |
| -------------------- | ----------------------- |
| Raspberry Pi OS Lite | Lightweight OS          |
| QLC+                 | Lighting control engine |
| Avahi                | mDNS (`lights.local`)   |
| systemd              | Autostart services      |
| wpa_supplicant       | WiFi management         |

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

---

## Quick Start

### 1. Configure `.env`

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

### 2. Provision the Pi

```bash
WIFI1_SSID="SetupNet" WIFI1_PSK="setup-pass" \
WIFI2_SSID="StudioNet" WIFI2_PSK="studio-pass" \
./lightsctl.sh setup
```

This runs `scripts/pi_lights_setup.sh` on the Pi, which:
- Sets the hostname and installs required packages
- Configures dual WiFi (studio network takes priority)
- Waits for DNS to recover after WiFi reconfiguration
- Installs QLC+ with automatic retry on network hiccups
- Creates and enables the `qlcplus-web.service` systemd unit with headless Qt configured

### 3. Open the web UI

```bash
./lightsctl.sh open-web
# → opens http://lights.local:9999 in your browser
```

From here, add your fixtures, map DMX universes, and build a Virtual Console layout for studio use.

---

## lightsctl.sh Reference

`lightsctl.sh` is the single entry point for all day-to-day Pi management.

```
./lightsctl.sh [command]

  setup                       first-time Pi provisioning
                              requires: WIFI1_SSID, WIFI1_PSK, WIFI2_SSID, WIFI2_PSK
  status                      systemd status for qlcplus-web.service
  restart                     restart qlcplus-web.service
  logs                        last 80 lines from the service journal
  tail                        follow service logs live
  health                      check service, web UI reachability, and ENTTEC USB
  lsusb                       show USB devices (ENTTEC should appear)
  qlc-version                 run qlcplus --version on the Pi
  qlc-headless                push Qt platform fix to Pi (sets QT_QPA_PLATFORM=minimal)
  wifi                        dump /etc/wpa_supplicant/wpa_supplicant.conf
  wifi-reconf                 run wpa_cli -i wlan0 reconfigure
  wifi-status                 show current SSID and wlan0 address
  update                      apt update && apt upgrade on the Pi
  backup                      pull QLC+ config dirs to BACKUP_STORAGE
  deploy-workspace <file.qxw> upload a workspace to the Pi and restart the service
  gen-cert [days]             generate a self-signed TLS cert/key in certs/ (default: 730 days)
  ssl-proxy [cert] [key]      install stunnel on Pi, redirect 443 → QLC_PORT
  hdmi-disable                append hdmi_blanking=2 to /boot/config.txt
  open-web                    open the web UI in the default browser
  ssh                         open an interactive shell on the Pi
  wifi-edit                   edit /etc/wpa_supplicant/wpa_supplicant.conf
  edit <path>                 edit an arbitrary file on the Pi
```

**Environment variables** (set in `.env` or exported):

| Variable         | Default              | Description                     |
| ---------------- | -------------------- | ------------------------------- |
| `PI_HOST`        | `lights.local`       | Pi hostname or IP               |
| `PI_USER`        | `pi`                 | SSH username                    |
| `HOSTNAME`       | `lights`             | mDNS hostname                   |
| `QLC_PORT`       | `9999`               | QLC+ web UI port                |
| `SSH_KEY`        | _(none)_             | Path to SSH private key         |
| `BACKUP_STORAGE` | `./backups`          | Local backup destination        |
| `SSL_CERT`       | `certs/qlc.crt`      | TLS certificate for ssl-proxy   |
| `SSL_KEY`        | `certs/qlc.key`      | TLS private key for ssl-proxy   |

---

## Connecting the ENTTEC DMX USB Pro

Plug into the Pi, then verify detection:

```bash
./lightsctl.sh lsusb
# expect: FTDI DMX USB PRO
```

QLC+ detects it automatically as a DMX output universe. Configure the universe in QLC+ under **Inputs/Outputs**.

---

## Configuring Fixtures

Fixture profiles, DMX addresses, and workspace layout are managed entirely within QLC+. Access the designer from any browser:

```
http://lights.local:9999
```

Once your workspace is ready, commit it to this repo under `workspaces/` and use `deploy-workspace` to push updates to the Pi:

```bash
./lightsctl.sh deploy-workspace workspaces/studio.qxw
```

---

## Recommended Improvements

### Static IP

Edit `/etc/dhcpcd.conf` on the Pi:

```
interface wlan0
static ip_address=192.168.1.50/24
static routers=192.168.1.1
static domain_name_servers=192.168.1.1
```

Then restart the DHCP client. On Raspberry Pi OS Lite the unit is `dhcpcd5`:

```bash
sudo systemctl restart dhcpcd5
# or bounce the interface:
sudo ip link set wlan0 down && sudo ip link set wlan0 up
```

### HTTPS

Generate a self-signed cert and install it on the Pi in one step:

```bash
./lightsctl.sh gen-cert        # writes certs/qlc.crt + certs/qlc.key
./lightsctl.sh ssl-proxy       # installs stunnel on Pi, proxies 443 → QLC_PORT
```

### Backups

```bash
./lightsctl.sh backup
```

Run after any workspace change. Pulls `.config/qlcplus` and `.qlcplus` from the Pi into `BACKUP_STORAGE`.

---

## Troubleshooting

### ENTTEC not detected

```bash
./lightsctl.sh lsusb
```

Check the USB cable and that QLC+ has access to `/dev/ttyUSB*`. The Pi user may need to be in the `dialout` group:

```bash
sudo usermod -aG dialout $USER
```

### QLC+ service fails to start

```bash
./lightsctl.sh logs
```

If logs show Qt platform errors, run `./lightsctl.sh qlc-headless` to apply the `QT_QPA_PLATFORM=minimal` drop-in.

### Lights not responding

- Confirm universe output is enabled in QLC+ under **Inputs/Outputs**
- Confirm ENTTEC is selected as the output plugin for the correct universe
- Verify fixture DMX addresses match their DIP switch or menu settings
- If using wireless DMX, confirm the transmitter is in **Transmit** mode and channels match

### DNS fails during setup

`pi_lights_setup.sh` automatically waits up to 60 s for DNS to recover after WiFi reconfiguration and injects `nameserver 1.1.1.1` if still failing. To fix manually:

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
