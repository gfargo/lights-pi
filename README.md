# Raspberry Pi Studio Lighting Controller

> Headless Raspberry Pi lighting controller for studio environments. Control DMX fixtures from any device on your network via QLC+ web interface.

**Core Stack:** QLC+ • ENTTEC DMX USB Pro • Raspberry Pi OS Lite

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

---

## 📋 Table of Contents

- [Quick Start](#-quick-start)
- [System Architecture](#-system-architecture)
- [Hardware Requirements](#-hardware-requirements)
- [Command Reference](#-command-reference)
- [Workflow Examples](#-workflow-examples)
- [Configuration](#-configuration)
- [Project Structure](#-project-structure)
- [Troubleshooting](#-troubleshooting)

---

## 🚀 Quick Start

### 1. Prepare SD Card

Download [Raspberry Pi Imager](https://www.raspberrypi.com/software/) and configure:

| Setting       | Value              |
| ------------- | ------------------ |
| OS            | Raspberry Pi OS Lite (64-bit for Pi 4, 32-bit for Pi 3) |
| Hostname      | `lights`           |
| Enable SSH    | Yes                |
| Username      | `pi`               |
| WiFi          | Your network       |

> ⚠️ **Important:** 
> - Hostname must match `PI_HOSTNAME` in `.env` for mDNS to work
> - Use 64-bit OS for Pi 4, 32-bit OS for Pi 3 (better performance on older hardware)

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your settings
```

### 3. Provision Pi

```bash
# Full setup (recommended for new Pi)
# The script will prompt for Pi model (3 or 4) and apply appropriate optimizations
WIFI1_SSID="SetupNet" WIFI1_PSK="setup-pass" \
WIFI2_SSID="StudioNet" WIFI2_PSK="studio-pass" \
./lightsctl.sh setup-full

# Or specify Pi model explicitly to skip prompt
PI_MODEL=3 WIFI1_SSID="SetupNet" WIFI1_PSK="setup-pass" \
WIFI2_SSID="StudioNet" WIFI2_PSK="studio-pass" \
./lightsctl.sh setup-full

# Verify installation
./lightsctl.sh doctor
./lightsctl.sh test-dmx
```

### 4. Set Up HTTPS (Optional but Recommended)

```bash
# One-command SSL setup with locally-trusted certificates
./lightsctl.sh setup-ssl

# Access via HTTPS with no browser warnings
# https://lights.local/      → Landing page
# https://lights.local/qlc/  → QLC+ interface
```

### 5. Access Web UI

```bash
./lightsctl.sh open-web
# Opens http://lights.local:9999 (or https://lights.local/qlc/ if SSL configured)
```

---

## 🏗️ System Architecture

```
Phones / Tablets / Laptops
            │
            │  WiFi / HTTPS
            ▼
     Raspberry Pi
     (nginx + QLC+ Web Server)
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

<details>
<summary><b>Software Stack</b></summary>

| Software             | Purpose                          |
| -------------------- | -------------------------------- |
| Raspberry Pi OS Lite | Lightweight OS                   |
| QLC+                 | Lighting control engine          |
| Avahi                | mDNS (`lights.local`)            |
| nginx                | Landing page + SSL reverse proxy |
| stunnel (optional)   | SSL/TLS termination              |
| mkcert (optional)    | Locally-trusted certificates     |
| systemd              | Autostart services               |
| wpa_supplicant       | WiFi management                  |

</details>

---

## 🔧 Hardware Requirements

| Device                          | Purpose                          |
| ------------------------------- | -------------------------------- |
| Raspberry Pi 3B+ or 4           | Lighting controller host         |
| MicroSD Card (16–32 GB)         | OS and configuration             |
| ENTTEC DMX USB Pro              | USB → DMX interface              |
| DMX fixtures                    | Any QLC+-compatible fixture      |
| Wireless DMX system (optional)  | Cable-free fixture control       |
| DMX cables (110 Ω)              | Daisy-chaining wired fixtures    |

> **Note:** Pi 3 is supported with automatic performance optimizations. Pi 4 is recommended for larger setups with many fixtures.

---

## 📚 Command Reference

### Core Commands

| Command | Description |
|---------|-------------|
| `validate` | Pre-flight validation of config and connectivity |
| `doctor` | Comprehensive health check with recommendations |
| `health` | Quick status check (service, web UI, USB, resources) |
| `test-dmx` | Verify ENTTEC USB and DMX output capability |
| `backup` | Pull QLC+ config to local storage |
| `restore <file>` | Restore QLC+ config from backup |

<details>
<summary><b>📦 Provisioning Commands</b></summary>

```bash
setup-full                    # Full provisioning: setup then harden (recommended)
setup                         # Base install (requires WIFI1_SSID/PSK, WIFI2_SSID/PSK)
harden                        # Firewall, watchdog, unattended upgrades, udev rule
add-key [pubkey]              # Install local SSH public key on the Pi
disable-password-auth         # Disable SSH password login (run add-key first)
static-ip <ip/prefix> <gw>   # Write static IP to /etc/dhcpcd.conf and restart
update                        # apt update && apt upgrade on the Pi
update-qlc                    # Upgrade only the qlcplus package and restart service
```

**What `setup` does:**
- Detects or prompts for Pi model (3 or 4)
- Sets hostname and installs required packages
- Configures dual WiFi (studio network takes priority)
- Installs QLC+ with automatic retry on network hiccups
- Creates and enables `qlcplus-web.service` with headless Qt
- Adds Pi user to `dialout` group for ENTTEC USB access
- Configures persistent systemd journal logs
- **Pi 3 only:** Applies performance optimizations (see below)

**Pi 3 Performance Optimizations:**
When Pi 3 is detected or selected, the following optimizations are automatically applied:
- Reduces GPU memory to 16MB (more RAM for QLC+)
- Disables Bluetooth (saves CPU and memory)
- Disables HDMI output (saves power)
- Increases swap to 512MB (better for low memory)
- Limits journal size to 50MB (reduces SD card wear)
- Sets CPU governor to performance mode (better for real-time lighting)
- Disables unnecessary services

> **Note:** A reboot is recommended after setup to apply all Pi 3 optimizations.

**What `harden` does:**
- Installs `ufw` and opens only SSH (22) and QLC+ web port
- Configures unattended security upgrades
- Enables hardware watchdog (auto-reboots on kernel hang)
- Creates udev rule so ENTTEC always appears at `/dev/dmx0`

</details>

<details>
<summary><b>🔍 Service Management Commands</b></summary>

```bash
status                        # systemd status for qlcplus-web.service
restart                       # Restart qlcplus-web.service
logs                          # Last 80 lines from the service journal
logs-errors                   # Show only ERROR and WARN lines from logs
tail                          # Follow service logs live
health                        # Service + web UI + USB + disk + memory + CPU temp
diagnose                      # Full diagnostic dump (health + logs + wifi + uptime)
check                         # Ping + SSH pre-flight connectivity check
validate                      # Pre-flight validation (config, connectivity, dependencies)
doctor                        # Comprehensive health check with recommendations
perf [seconds]                # Monitor CPU, memory, network usage over time (default: 10s)
benchmark                     # Test system performance (web UI latency, network speed)
```

</details>

<details>
<summary><b>💡 QLC+ Commands</b></summary>

```bash
qlc-version                   # Run qlcplus --version on the Pi
qlc-headless                  # Push Qt platform fix (sets QT_QPA_PLATFORM=minimal)
deploy-workspace <file.qxw>   # Upload workspace to Pi and restart service
pull-workspace [output.qxw]   # Download current workspace from Pi
list-fixtures                 # Show installed fixture definitions
install-fixture <file.qxf>    # Upload and install custom fixture definition
test-dmx                      # Verify ENTTEC USB and DMX output capability
open-web                      # Open the web UI in the default browser
```

</details>

<details>
<summary><b>🌐 Network / WiFi Commands</b></summary>

```bash
wifi                          # Dump /etc/wpa_supplicant/wpa_supplicant.conf
wifi-reconf                   # Run wpa_cli -i wlan0 reconfigure
wifi-status                   # Show current SSID and wlan0 address
wifi-edit                     # Edit the Wi-Fi config in $EDITOR
scan                          # Scan network for Raspberry Pi devices (lights-*.local)
```

</details>

<details>
<summary><b>🖥️ System Commands</b></summary>

```bash
backup                        # Pull QLC+ config dirs to BACKUP_STORAGE
restore <backup.tar.gz>       # Restore QLC+ config from backup and restart service
lsusb                         # Show USB devices (ENTTEC should appear)
os-version                    # Show Raspberry Pi OS and kernel version
hdmi-disable                  # Disable HDMI output to save power
reboot                        # Reboot the Pi
poweroff                      # Shut down the Pi
ssh                           # Open an interactive shell on the Pi
edit <path>                   # Edit an arbitrary file on the Pi
```

</details>

<details>
<summary><b>🔒 TLS/SSL Commands</b></summary>

```bash
setup-ssl                     # Complete SSL setup: mkcert cert + nginx config (recommended!)
gen-cert [days]               # Generate self-signed cert/key in certs/ (default: 730 days)
gen-cert-mkcert               # Generate locally-trusted cert using mkcert (no browser warnings)
ssl-nginx [cert] [key]        # Configure nginx with SSL + reverse proxy to QLC+
ssl-proxy [cert] [key]        # Install stunnel, redirect 443 → QLC_PORT (simpler alternative)
```

**SSL Setup Options:**

1. **Recommended: One-command setup with mkcert**
   ```bash
   ./lightsctl.sh setup-ssl
   ```
   - Installs mkcert (via Homebrew) if needed
   - Generates locally-trusted certificate
   - Configures nginx with SSL + reverse proxy
   - No browser warnings!
   - Access: `https://lights.local/` and `https://lights.local/qlc/`

2. **Manual: Self-signed certificate**
   ```bash
   ./lightsctl.sh gen-cert 365
   ./lightsctl.sh ssl-nginx
   ```
   - Creates self-signed certificate
   - Browser will show security warning (expected)
   - Still encrypted, just not trusted by default

3. **Alternative: stunnel (simpler)**
   ```bash
   ./lightsctl.sh gen-cert
   ./lightsctl.sh ssl-proxy
   ```
   - Uses stunnel instead of nginx
   - Less flexible but easier setup
   - Landing page only (QLC+ stays on port 9999)

</details>

<details>
<summary><b>🌐 Landing Page Commands</b></summary>

```bash
landing-setup                 # Install nginx and deploy the landing page (first time)
landing-deploy                # Push updated landing/index.html (no nginx reinstall)
```

Serves a simple branded page at `http://lights.local` (or `https://lights.local` if SSL configured) with a button linking to the QLC+ web UI.

**Customization:**
Set these variables in `.env` to customize the landing page:
- `LANDING_TITLE` - Browser title
- `LANDING_STUDIO_NAME` - Studio name displayed
- `LANDING_SUBTITLE` - Subtitle text
- `LANDING_BUTTON_TEXT` - Button text
- `LANDING_FOOTER_TEXT` - Footer text
- `QLC_URL` - Button destination (e.g., `https://lights.local/qlc/` or `http://lights.local:9999/`)

</details>

### Makefile Shortcuts

All commands have `make` shortcuts for convenience:

<details>
<summary><b>View Makefile Examples</b></summary>

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
make perf DURATION=30
make benchmark

# QLC+
make deploy WS=workspaces/studio.qxw
make set-default WS=workspaces/studio.qxw
make pull OUTPUT=custom.qxw
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

</details>

---

## 💼 Workflow Examples

### Initial Setup
```bash
# 1. Validate your local environment
./lightsctl.sh validate

# 2. Run full provisioning
WIFI1_SSID="SetupNet" WIFI1_PSK="setup-pass" \
WIFI2_SSID="StudioNet" WIFI2_PSK="studio-pass" \
./lightsctl.sh setup-full

# 3. Set up HTTPS (recommended)
./lightsctl.sh setup-ssl

# 4. Deploy landing page
./lightsctl.sh landing-setup

# 5. Verify everything is working
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

## ⚙️ Configuration

### Environment Variables

Create `.env` from `.env.example`:

```bash
cp .env.example .env
```

<details>
<summary><b>View Configuration Options</b></summary>

| Variable                | Default                  | Description                          |
| ----------------------- | ------------------------ | ------------------------------------ |
| `PI_HOST`               | `lights.local`           | Pi hostname or IP                    |
| `PI_USER`               | `pi`                     | SSH username                         |
| `PI_HOSTNAME`           | `lights`                 | mDNS hostname set on the Pi          |
| `QLC_PORT`              | `9999`                   | QLC+ web UI port                     |
| `SSH_KEY`               | _(none)_                 | Path to SSH private key              |
| `BACKUP_STORAGE`        | `./backups`              | Local backup destination             |
| `SSL_CERT`              | `certs/qlc.crt`          | TLS certificate for ssl-proxy        |
| `SSL_KEY`               | `certs/qlc.key`          | TLS private key for ssl-proxy        |
| `QLC_URL`               | `http://lights.local:9999/` | Landing page button destination   |
| `LANDING_TITLE`         | `Lighting Controller`    | Landing page browser title           |
| `LANDING_STUDIO_NAME`   | `Your Studio`            | Studio name displayed on landing page|
| `LANDING_SUBTITLE`      | `Lighting Controller`    | Subtitle text on landing page        |
| `LANDING_BUTTON_TEXT`   | `Lighting Control`       | Button text on landing page          |
| `LANDING_FOOTER_TEXT`   | `lights.local`           | Footer text on landing page          |

</details>

### Static IP Configuration

```bash
./lightsctl.sh static-ip 192.168.1.50/24 192.168.1.1
# or: make static-ip IP=192.168.1.50/24 GW=192.168.1.1
```

### HTTPS Setup

**Recommended: One-command setup with mkcert**
```bash
./lightsctl.sh setup-ssl
```

This will:
1. Install mkcert (via Homebrew) if not already installed
2. Install the mkcert local CA (makes your system trust the certs)
3. Generate a locally-trusted certificate for `lights.local`
4. Upload the certificate to your Pi
5. Configure nginx with SSL and reverse proxy to QLC+

After setup, access your lighting controller securely:
- `https://lights.local/` → Landing page
- `https://lights.local/qlc/` → QLC+ web interface

No browser warnings - the certificate is fully trusted!

**Alternative: Self-signed certificate**
```bash
./lightsctl.sh gen-cert        # Generate self-signed certificate
./lightsctl.sh ssl-nginx       # Configure nginx with SSL
```

**Simple option: stunnel**
```bash
./lightsctl.sh gen-cert        # Generate certificate
./lightsctl.sh ssl-proxy       # Install stunnel
```

### Landing Page Button URL

Control where the landing page button links by setting `QLC_URL` in `.env`:

```bash
# For SSL reverse proxy (after running setup-ssl)
QLC_URL=https://lights.local/qlc/

# For direct port access
QLC_URL=http://lights.local:9999/
```

Then redeploy the landing page:
```bash
./lightsctl.sh landing-deploy
```

---

## 🔌 Hardware Setup

### Connecting ENTTEC DMX USB Pro

Plug into the Pi, then verify detection:

```bash
./lightsctl.sh test-dmx    # Comprehensive DMX hardware check
./lightsctl.sh lsusb       # Expect: FTDI DMX USB PRO
./lightsctl.sh health      # Confirms service + web UI + USB all green
```

After `harden` is run, the device gets a stable symlink at `/dev/dmx0` via udev, so QLC+ always finds it regardless of which USB port it's in.

---

## 🎛️ Fixture & Workspace Management

### Managing Fixtures

```bash
# List installed fixture definitions
./lightsctl.sh list-fixtures

# Install a custom fixture definition
./lightsctl.sh install-fixture path/to/custom-fixture.qxf
```

### Managing Workspaces

Access the QLC+ designer at `http://lights.local:9999` to configure fixtures, DMX addresses, and workspace layout.

```bash
# Deploy workspace to Pi
./lightsctl.sh deploy-workspace workspaces/studio.qxw

# Pull current workspace from Pi (after making changes in web UI)
./lightsctl.sh pull-workspace workspaces/studio-updated.qxw
```

### Backups

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

## 📁 Project Structure

The project uses a modular architecture for maintainability and clear separation of concerns:

```
lights-pi/
├── lightsctl.sh              # Main CLI interface
├── scripts/
│   ├── lib/                  # Utility libraries (sourced by lightsctl.sh)
│   │   ├── backup.sh         # Backup/restore and system updates
│   │   ├── network.sh        # Network scanning and Pi discovery
│   │   ├── qlc.sh            # QLC+ operations (workspace, fixtures, DMX)
│   │   ├── system.sh         # System monitoring and diagnostics
│   │   ├── tls.sh            # Certificate generation and SSL proxy
│   │   └── wifi.sh           # WiFi configuration management
│   ├── provisioning/         # One-time setup scripts
│   │   ├── setup.sh          # Base installation (formerly pi_lights_setup.sh)
│   │   ├── harden.sh         # Security hardening (formerly pi_harden.sh)
│   │   └── configure_qlc_headless.sh  # Qt platform configuration
│   └── services/             # Service-specific deployment
│       └── landing.sh        # Landing page setup (formerly pi_landing.sh)
├── landing/                  # Landing page HTML
├── workspaces/               # QLC+ workspace files (.qxw)
├── backups/                  # QLC+ configuration backups
└── .env                      # Environment configuration
```

### Script Organization

**Utility Libraries (`scripts/lib/`):**
- Contain reusable functions sourced by `lightsctl.sh`
- Each module focuses on a single domain (networking, QLC+, system, etc.)
- Functions are exported for cross-module use

**Provisioning Scripts (`scripts/provisioning/`):**
- Large, standalone scripts for initial Pi setup
- Run once during initial provisioning or updates
- Called by `lightsctl.sh` provisioning commands

**Service Scripts (`scripts/services/`):**
- Service-specific deployment and configuration
- Currently contains landing page setup

This modular structure makes it easy to:
- Locate and modify specific functionality
- Add new features without touching unrelated code
- Test individual components independently
- Understand the codebase quickly

---

## 🔧 Troubleshooting

### Quick Diagnostics

```bash
./lightsctl.sh doctor      # Full health check with recommendations
./lightsctl.sh validate    # Pre-flight validation
./lightsctl.sh diagnose    # Detailed diagnostic dump
```

<details>
<summary><b>ENTTEC not detected</b></summary>

```bash
./lightsctl.sh test-dmx    # Comprehensive DMX hardware check
./lightsctl.sh lsusb       # Verify USB device detection
./lightsctl.sh health      # Check overall system status
```

If `harden` has been run, the device should appear at `/dev/dmx0` — replug it to trigger the udev rule. If the Pi user can't access the device, confirm they are in the `dialout` group:

```bash
./lightsctl.sh ssh
groups $USER   # should include dialout
```

A logout/login (or reboot) is required for group changes to take effect.

</details>

<details>
<summary><b>QLC+ service fails to start</b></summary>

```bash
./lightsctl.sh logs        # View recent logs
./lightsctl.sh logs-errors # Filter for errors only
```

If logs show Qt platform errors, run `./lightsctl.sh qlc-headless` to apply the `QT_QPA_PLATFORM=minimal` drop-in. The service has a crash loop guard (`StartLimitBurst=5` in 60 s) — if it keeps restarting, check `logs` for the root cause before it stops trying.

</details>

<details>
<summary><b>Performance Issues</b></summary>

Monitor system performance:
```bash
./lightsctl.sh perf 30     # Real-time monitoring for 30 seconds
./lightsctl.sh benchmark   # Run performance tests
```

</details>

<details>
<summary><b>QLC+ web interface hangs or doesn't respond</b></summary>

If the web interface connects but never loads (spinning wheel), the service may have the `--operate` flag enabled which causes it to hang:

```bash
./lightsctl.sh ssh
sudo sed -i 's/--operate//' /etc/systemd/system/qlcplus-web.service
sudo systemctl daemon-reload
sudo systemctl restart qlcplus-web
```

The `--operate` flag puts QLC+ into operate mode immediately, which can make the web interface unresponsive. Remove it to allow normal web UI access.

</details>

<details>
<summary><b>HTTPS not working or certificate warnings</b></summary>

**If using mkcert:**
```bash
# Reinstall the local CA
mkcert -install

# Regenerate certificates
rm certs/qlc.crt certs/qlc.key
./lightsctl.sh gen-cert-mkcert
./lightsctl.sh ssl-nginx
```

**If using self-signed certificates:**
Browser warnings are expected. Click "Advanced" and "Proceed to lights.local" to accept the certificate.

**Check nginx configuration:**
```bash
./lightsctl.sh ssh
sudo nginx -t                          # Test config syntax
sudo systemctl status nginx            # Check if nginx is running
sudo tail -f /var/log/nginx/error.log  # View error logs
```

**Verify certificate files:**
```bash
./lightsctl.sh ssh
ls -la /etc/ssl/qlc/
openssl x509 -in /etc/ssl/qlc/qlc.crt -text -noout  # View cert details
```

</details>

<details>
<summary><b>Reverse proxy /qlc/ path not working</b></summary>

Check nginx configuration and logs:
```bash
./lightsctl.sh ssh
cat /etc/nginx/sites-available/lights   # View config
sudo nginx -t                            # Test config
sudo systemctl reload nginx              # Reload config
sudo tail -f /var/log/nginx/error.log   # Watch for errors
```

Verify QLC+ is running and accessible locally:
```bash
./lightsctl.sh ssh
curl -I http://127.0.0.1:9999/          # Should connect
sudo systemctl status qlcplus-web       # Check service status
```

</details>

<details>
<summary><b>Web UI connects but browser hangs</b></summary>

If the port is open (TCP connects) but the browser never loads a page, the QLC+ web server thread has stalled:

```bash
./lightsctl.sh restart
```

The service recovers cleanly on restart. If it keeps stalling, check `./lightsctl.sh logs-errors` for errors.

</details>

<details>
<summary><b>Can't find Pi on network</b></summary>

```bash
./lightsctl.sh scan        # Scan for Pi devices on network
./lightsctl.sh check       # Test connectivity to configured host
```

Ensure the Pi is powered on, connected to the same network, and that the hostname in `.env` matches what was set during SD card preparation.

</details>

<details>
<summary><b>Lights not responding</b></summary>

- Confirm universe output is enabled in QLC+ under **Inputs/Outputs**
- Confirm ENTTEC is selected as the output plugin for the correct universe
- Verify fixture DMX addresses match their DIP switch or menu settings
- If using wireless DMX, confirm the transmitter is in **Transmit** mode and channels match

</details>

<details>
<summary><b>DNS fails during setup</b></summary>

`pi_lights_setup.sh` automatically waits up to 60 s for DNS to recover after WiFi reconfiguration and injects `nameserver 1.1.1.1` if still failing. To fix manually on the Pi:

```bash
echo 'nameserver 1.1.1.1' | sudo tee -a /etc/resolv.conf
sudo apt-get update
```

</details>

<details>
<summary><b>Network interface commands not found</b></summary>

Raspberry Pi OS Lite does not include `ifup`/`ifdown` or the `networking` systemd unit. Use instead:

```bash
sudo ip link set wlan0 down && sudo ip link set wlan0 up
sudo systemctl restart dhcpcd5
```

</details>

---

## 🚀 Future Enhancements

See [docs/ROADMAP.md](docs/ROADMAP.md) for the complete product roadmap.

**Immediate priorities:**
- ✅ Auto-load default workspace (v1.1)
- Marketing website with interactive demo (v1.2)
- AI scene generation with modular/complete styles (v1.2)
- Scene library and marketplace (v1.3)

**Planned features:**
- Multi-device fleet management
- Studio ecosystem integration (camera, video, audio)
- Advanced AI features (scene evolution, video analysis)
- Professional show management tools
- Plugin system and API access

For detailed information on AI scene generation, see [docs/AI_SCENE_GENERATION.md](docs/AI_SCENE_GENERATION.md).

---

## 🤖 AI Scene Generation (Beta)

Generate QLC+ scenes from natural language descriptions using AI. The system understands your fixture inventory and creates appropriate DMX values to match your desired mood or effect.

```bash
# Generate a scene
./lightsctl.sh generate-scene "warm sunset ambiance" --preview

# Generate with specific style
./lightsctl.sh generate-scene "party mode" --style modular --add-to-workspace

# Generate multiple variations and choose the best one
./lightsctl.sh generate-scene "dramatic spotlight" --variations 3 --preview

# Save to file
./lightsctl.sh generate-scene "dramatic spotlight" --output scenes/dramatic.xml
```

**Supported Styles:**
- **Complete:** Self-contained, ready-to-use scenes
- **Modular:** Composable layers (color, intensity, position)
- **Timeline:** Time-based sequences with keyframes
- **Reactive:** Audio/sensor-responsive scenes

**Scene Variations:**
Generate multiple variations of a scene and interactively select the best one:
```bash
# Generate 3 variations with interactive selection
./lightsctl.sh generate-scene "warm sunset" --variations 3

# Works with all options
./lightsctl.sh generate-scene "party lights" --variations 5 --style modular --add-to-workspace
```

**Scene Templates:**
Fast, predictable scenes from pre-defined templates (no AI required):
```bash
# List available templates
./lightsctl.sh list-templates

# Generate from template
./lightsctl.sh generate-from-template youtube-studio --preview
./lightsctl.sh generate-from-template party --add-to-workspace
./lightsctl.sh generate-from-template warm-white --output scenes/warm.xml
```

Available templates:
- `youtube-studio` - Bright neutral white for video recording
- `party` - Vibrant alternating colors with fast transitions
- `ambient` - Soft warm glow at low intensity
- `spotlight` - Single fixture at full, others off
- `work-light` - Bright neutral white for task lighting
- `warm-white` - Warm white (2700K-3000K color temperature)
- `cool-white` - Cool white (5000K-6500K color temperature)

**Configuration:**
Add to your `.env` file:
```bash
AI_PROVIDER=anthropic          # anthropic, openai, or ollama
AI_API_KEY=sk-ant-...          # Your API key (not needed for ollama)
AI_MODEL=claude-3-5-sonnet-20241022
AI_SCENE_STYLE=complete        # Default style
AI_SCENE_VARIATIONS=1          # Default number of variations
```

See [docs/AI_SCENE_GENERATION.md](docs/AI_SCENE_GENERATION.md) for complete documentation.

---

## 📄 License

MIT License - see [LICENSE](LICENSE) for details
