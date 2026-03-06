---
inclusion: auto
---

# Technology Stack

## Core Technologies

- **OS**: Raspberry Pi OS Lite (64-bit, headless)
- **Lighting Engine**: QLC+ (open source DMX control)
- **Hardware Interface**: ENTTEC DMX USB Pro
- **Service Management**: systemd
- **Network**: Avahi (mDNS), wpa_supplicant (WiFi)
- **Web Server**: nginx (optional landing page)
- **Security**: ufw (firewall), stunnel4 (optional TLS)

## Build System

Bash-based provisioning and management via `lightsctl.sh` wrapper script. No compilation required.

## Common Commands

All commands use `./lightsctl.sh` or `make` shortcuts:

### Provisioning
```bash
./lightsctl.sh setup-full          # Full setup (base + hardening)
./lightsctl.sh setup               # Base install only
./lightsctl.sh harden              # Security hardening
```

### Service Management
```bash
./lightsctl.sh status              # Check service status
./lightsctl.sh restart             # Restart QLC+ service
./lightsctl.sh logs                # View recent logs
./lightsctl.sh health              # Full health check
```

### Deployment
```bash
./lightsctl.sh deploy-workspace workspaces/studio.qxw
./lightsctl.sh landing-deploy      # Update landing page
```

### Diagnostics
```bash
./lightsctl.sh diagnose            # Full diagnostic report
./lightsctl.sh lsusb               # Check USB devices
./lightsctl.sh wifi-status         # WiFi connection info
```

### System
```bash
./lightsctl.sh backup              # Backup QLC+ config
./lightsctl.sh update              # System updates
./lightsctl.sh ssh                 # Interactive shell
```

## Configuration

Environment variables in `.env` (copy from `.env.example`):
- `PI_HOST` - Pi hostname or IP (default: lights.local)
- `PI_USER` - SSH username (default: pi)
- `PI_HOSTNAME` - mDNS hostname (default: lights)
- `QLC_PORT` - Web UI port (default: 9999)
- `SSH_KEY` - Path to SSH private key (optional)
- `BACKUP_STORAGE` - Local backup directory (default: ./backups)

## Testing

No automated test suite. Manual verification via:
- `./lightsctl.sh health` - Service, web UI, USB, system resources
- `./lightsctl.sh check` - Connectivity pre-flight
- Browser access to `http://lights.local:9999`
