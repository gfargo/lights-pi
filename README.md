# Raspberry Pi Studio Lighting Controller

## Overview

Headless Raspberry Pi lighting controller for a studio environment using:

- **QLC+** — open source lighting control software
- **ENTTEC DMX USB Pro** — USB → DMX interface
- **Chauvet D-Fi Hub 2** — wireless DMX transmitter
- **Chauvet SlimPAR fixtures** — wireless DMX receivers

Anyone in the studio can control lighting from a phone or browser via the QLC+ web UI hosted on the Pi.

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
      D-Fi Hub 2 (Transmit)
            │
        Wireless DMX
            ▼
    SlimPAR Pro (Receiver)
            │
         DMX OUT
            ▼
      SlimPAR 56
```

---

## Hardware

| Device                          | Purpose                  |
| ------------------------------- | ------------------------ |
| Raspberry Pi 4                  | Lighting controller host |
| MicroSD Card (16–32 GB)         | OS and configuration     |
| ENTTEC DMX USB Pro              | USB → DMX interface      |
| Chauvet D-Fi Hub 2              | Wireless DMX transmitter |
| Chauvet D-Fi USB receivers      | Wireless DMX receivers   |
| DMX cables (110 Ω)              | Daisy-chaining fixtures  |

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

Create `.env` next to `lightsctl.sh`:

```bash
PI_HOST=lights.local
PI_USER=pi
QLC_PORT=9999
SSH_KEY=~/.ssh/your_key
BACKUP_STORAGE=./backups
```

### 2. Provision the Pi

```bash
WIFI1_SSID="SetupNet" WIFI1_PSK="setup-pass" \
WIFI2_SSID="StudioNet" WIFI2_PSK="studio-pass" \
./lightsctl.sh setup
```

This runs `scripts/pi_lights_setup.sh` on the Pi, which:
- Sets the hostname
- Installs packages (avahi, tmux, htop, git, curl, usbutils, wpasupplicant)
- Configures dual WiFi (studio network takes priority)
- Waits for DNS to recover after WiFi reconfiguration
- Installs QLC+ (with automatic retry if the network hiccups)
- Creates and enables the `qlcplus-web.service` systemd unit

### 3. Fix headless Qt (first time only)

```bash
./lightsctl.sh qlc-headless
```

Sets `QT_QPA_PLATFORM=minimal` via a systemd drop-in so QLC+ runs without X11.

### 4. Open the web UI

```bash
./lightsctl.sh open-web
# → http://lights.local:9999
```

---

## lightsctl.sh Reference

`lightsctl.sh` is the single entry point for all day-to-day Pi management.

```
./lightsctl.sh [command]

Commands:
  setup               provision the Pi via scripts/pi_lights_setup.sh
  status              systemd status for qlcplus-web.service
  restart             restart qlcplus-web.service
  logs                last 80 lines from the service journal
  tail                follow service logs live
  lsusb               show USB devices (ENTTEC should appear)
  qlc-version         run qlcplus --version on the Pi
  qlc-headless        push configure_qlc_headless.sh and run it (sets QT_QPA_PLATFORM=minimal)
  wifi                dump /etc/wpa_supplicant/wpa_supplicant.conf
  wifi-reconf         run wpa_cli -i wlan0 reconfigure
  wifi-status         show current SSID and wlan0 address
  update              apt update && apt upgrade on the Pi
  backup              pull QLC+ config dirs to BACKUP_STORAGE
  hdmi-disable        append hdmi_blanking=2 to /boot/config.txt
  ssl-proxy <crt> <key>  install stunnel, redirect 443 → QLC_PORT
  open-web            print the web UI URLs
  ssh                 open an interactive shell on the Pi
  wifi-edit           edit /etc/wpa_supplicant/wpa_supplicant.conf
  edit <path>         edit an arbitrary file on the Pi
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

QLC+ detects it automatically as a DMX output universe.

---

## Fixture Addressing

| Fixture     | Mode | DMX Address |
| ----------- | ---- | ----------- |
| SlimPAR Pro | 7CH  | d001        |
| SlimPAR 56  | 3CH  | d008        |

| Channel | Function         |
| ------- | ---------------- |
| 1–7     | SlimPAR Pro      |
| 8       | SlimPAR 56 Red   |
| 9       | SlimPAR 56 Green |
| 10      | SlimPAR 56 Blue  |

---

## QLC+ Virtual Console

Create large buttons in the Virtual Console for studio use:

```
Warm White  |  Cool White  |  Blue Wash
Red Wash    |  Bright      |  Soft
            |   All Off    |
```

Access from any device on the network: `http://lights.local:9999`

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

Then restart the DHCP client. Note: on Raspberry Pi OS Lite the service name is `dhcpcd5`, not `dhcpcd`:

```bash
sudo systemctl restart dhcpcd5
# or bounce the interface:
sudo ip link set wlan0 down && sudo ip link set wlan0 up
```

### HTTPS

```bash
./lightsctl.sh ssl-proxy certs/qlc.crt certs/qlc.key
```

Installs `stunnel4`, proxies 443 → `QLC_PORT`, and saves iptables rules.

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

Check USB cable and permissions. QLC+ must have access to the `/dev/ttyUSB*` device.

### QLC+ service fails to start

```bash
./lightsctl.sh logs
```

If the log shows Qt platform errors, run `./lightsctl.sh qlc-headless` to apply the `QT_QPA_PLATFORM=minimal` fix.

### Lights not responding

- Confirm universe output is enabled in QLC+
- Confirm ENTTEC is selected as the output plugin
- Verify DMX addresses match fixture DIP switch settings
- Confirm D-Fi Hub 2 is in **Transmit** mode
- Confirm wireless channel on Hub 2 matches receivers

### DNS fails during setup

`pi_lights_setup.sh` automatically waits up to 60 s for DNS to recover after WiFi reconfiguration and injects `nameserver 1.1.1.1` if resolution is still failing. If you need to fix it manually:

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

- ArtNet support
- MIDI controller integration
- StreamDeck lighting control
- Motion-triggered scenes
- Scheduled scenes
- Custom React/Node lighting UI
- Dedicated WiFi VLAN for lights
