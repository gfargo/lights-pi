# WiFi Reliability & Troubleshooting

> Guide to keeping your Raspberry Pi lighting controller reliably connected to WiFi, especially when moving between networks (home ↔ studio).

---

## Quick Reference

| Command | Description |
|---------|-------------|
| `wifi-test` | End-to-end connectivity check (7 tests) |
| `wifi-watchdog-install` | Install auto-recovery watchdog |
| `wifi-watchdog-status` | Check watchdog timer status |
| `wifi-watchdog-logs` | View watchdog recovery history |
| `wifi-list` | List configured and available networks |
| `wifi-add-network <ssid> <pass> [priority]` | Add a new network |
| `wifi-reconnect` | Force reconnect to best available network |
| `wifi-diagnose` | Full WiFi diagnostics |

---

## WiFi Watchdog

The WiFi watchdog is a systemd timer that runs on the Pi every 2 minutes. It performs two checks:

1. Does `wlan0` have an IP address?
2. Can it reach the default gateway?

If either check fails, it attempts recovery:

- **Attempts 1–2:** Disconnects and reconnects `wlan0` via NetworkManager
- **Attempt 3:** Restarts NetworkManager entirely and resets the failure counter

### Install

```bash
./lightsctl.sh wifi-watchdog-install
```

### Monitor

```bash
./lightsctl.sh wifi-watchdog-status   # Timer status
./lightsctl.sh wifi-watchdog-logs     # Recovery history
```

### Remove

```bash
./lightsctl.sh wifi-watchdog-uninstall
```

---

## WiFi Connectivity Test

The `wifi-test` command runs 7 checks in a single SSH session:

1. **Interface up** — is `wlan0` in UP state?
2. **IPv4 address** — does it have an IP assigned?
3. **SSID connected** — which network is it on?
4. **Signal strength** — how strong is the connection?
5. **Gateway reachable** — can it ping the router?
6. **DNS resolution** — can it resolve hostnames?
7. **Internet reachable** — can it reach the outside world?

```bash
$ ./lightsctl.sh wifi-test
=== WiFi Connectivity Test ===

wlan0 interface up:                ✓
IPv4 address assigned:             ✓ 192.168.1.7
Connected to SSID:                 ✓ Turbo
Signal strength:                   ✓ 70% (good)
Default gateway reachable:         ✓ 192.168.1.1
DNS resolution:                    ✓
Internet reachable:                ✓

--- Result: 7 passed, 0 failed ---
```

---

## Network Priority Configuration

NetworkManager connects to the highest-priority available network. Set priorities so your preferred networks are tried first:

```bash
# Higher number = higher priority
# Example: home network preferred over studio
./lightsctl.sh wifi-add-network "HomeNet" "password" 100
./lightsctl.sh wifi-add-network "StudioNet" "password" 50
./lightsctl.sh wifi-add-network "StudioNet-5G" "password" 40
./lightsctl.sh wifi-add-network "StudioNet-2G" "password" 30
```

Or modify existing connections directly:
```bash
ssh pi@lights.local sudo nmcli connection modify "MyNetwork" connection.autoconnect-priority 100
```

---

## Multi-Band Network Setup

Many routers broadcast separate SSIDs for 2.4GHz and 5GHz bands (e.g., `MyNetwork-2G` and `MyNetwork-5G`).

**Important: Pi 3B is 2.4GHz only.** The BCM43438 WiFi chip on the Raspberry Pi 3 Model B does not support 5GHz. Do not add 5GHz-only SSIDs — NetworkManager will waste time trying to connect to them before falling back, adding 30-60 seconds of dead time.

For Pi 3, only add the base SSID and the explicit 2.4GHz variant:
```bash
./lightsctl.sh wifi-add-network "StudioNet" "password" 50
./lightsctl.sh wifi-add-network "StudioNet-2G" "password" 40
```

Pi 4 and newer have dual-band WiFi and can use both bands.

Your laptop on 5GHz and the Pi on 2.4GHz will still see each other — both bands share the same LAN on the router. The only exception is if the router has "AP isolation" or "client isolation" enabled.

---

## Pi 3 WiFi Tuning

The Pi 3's onboard BCM43438 WiFi is functional but needs tuning for reliable headless operation. These settings are applied on the Pi itself:

### Disable WiFi Power Save

The BCM43438 has a known issue where power save mode causes random disconnects. The chip goes to sleep and sometimes doesn't wake up properly.

```bash
# Create NetworkManager config to disable power save
echo -e '[connection]\nwifi.powersave = 2' | sudo tee /etc/NetworkManager/conf.d/wifi-powersave.conf
sudo systemctl restart NetworkManager
```

Value `2` means "disable". This is the single most impactful fix for Pi 3 WiFi reliability.

### Set Regulatory Domain

Without a regulatory domain, the radio uses conservative power and channel settings. Setting it to your country allows full transmit power.

```bash
sudo raspi-config nonint do_wifi_country US
```

### Disable IPv6 on WiFi Connections

IPv6 auto-configuration can stall connection setup on networks that don't properly support it, adding delays when switching networks.

```bash
sudo nmcli connection modify "MyNetwork" ipv6.method ignore
```

### Add Fallback DNS

If the router's DHCP is slow or doesn't provide DNS servers, add fallback resolvers:

```bash
sudo nmcli connection modify "MyNetwork" ipv4.dns "1.1.1.1 8.8.8.8"
```

### Increase DHCP Timeout

Studio/venue routers can be slow to hand out leases. The default timeout is too aggressive:

```bash
sudo nmcli connection modify "StudioNet" ipv4.dhcp-timeout 60
```

---

## Common Issues

### SSH "Too many authentication failures"

If your SSH agent has many keys loaded, the Pi's `MaxAuthTries` limit is hit before the correct key is tried.

**Fix:** Add `IdentitiesOnly yes` to `~/.ssh/config`:
```
Host lights.local
  User pi
  IdentityFile ~/.ssh/id_rsa
  IdentitiesOnly yes
```

Or the `lightsctl.sh` script handles this automatically when `SSH_KEY` is set in `.env`.

### Pi not found after moving to a new location

1. Ensure the new network is configured: `./lightsctl.sh wifi-list`
2. If not, add it: `./lightsctl.sh wifi-add-network "NewNet" "password"`
3. Scan for the Pi: `./lightsctl.sh scan --deep`
4. If mDNS isn't working, try the IP directly: `PI_HOST=192.168.x.x ./lightsctl.sh check`

### WiFi drops under load

The Pi's onboard WiFi can struggle with heavy traffic. Mitigations:
- Use 2.4GHz for better range and stability
- Reduce QLC+ web UI polling frequency
- Consider a USB WiFi adapter with external antenna for better reception
- Install the watchdog for automatic recovery

### Signal strength is weak

```bash
./lightsctl.sh wifi-test   # Check signal percentage
```

- **70%+** — Good, no action needed
- **40–70%** — Fair, may drop occasionally. Consider repositioning
- **Below 40%** — Weak, expect frequent drops. Move Pi closer to router or add a WiFi extender
