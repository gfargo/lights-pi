# Tailscale — Remote Access to Your Lighting Pi

Access your lighting rig from anywhere using [Tailscale](https://tailscale.com),
a zero-config mesh VPN. Once set up, you can reach the Pi from any device on
your tailnet — no port forwarding, no dynamic DNS, no firewall holes.

---

## Why Tailscale?

- **Access from anywhere** — Control lights from your phone at home, a laptop
  at a café, or a tablet backstage at a different venue.
- **Zero network configuration** — No router port-forwarding or static IPs
  needed. Works through NATs and firewalls automatically.
- **Encrypted by default** — All traffic between devices is WireGuard-encrypted.
- **Free tier** — Tailscale's personal plan supports up to 100 devices.

---

## Installation

### Prerequisites

- A [Tailscale account](https://login.tailscale.com/start) (free)
- SSH access to your Pi (`./lightsctl.sh ssh`)
- Tailscale installed on at least one other device (phone, laptop, etc.)

### Install on the Pi

SSH into the Pi and run the official install script:

```bash
./lightsctl.sh ssh
# Then on the Pi:
curl -fsSL https://tailscale.com/install.sh | sh
```

This adds the Tailscale apt repo and installs the package. On Raspbian, it
automatically detects the correct architecture (armhf/arm64).

### Enable and Authenticate

```bash
# On the Pi:
sudo systemctl enable --now tailscaled
sudo tailscale up
```

This prints an authentication URL. Open it in your browser, log in to your
Tailscale account, and authorize the device. The Pi joins your tailnet
immediately.

### Verify

```bash
# On the Pi:
tailscale status
```

You should see your Pi listed with a `100.x.x.x` IP, along with your other
devices.

---

## Accessing Services Over Tailscale

Once the Pi is on your tailnet, all services are reachable via the Tailscale IP
or MagicDNS hostname:

| Service | Local URL | Tailscale URL |
|---------|-----------|---------------|
| Landing page | `http://lights.local` | `http://lights.<tailnet>.ts.net` |
| QLC+ Web UI | `http://lights.local:9999` | `http://lights.<tailnet>.ts.net:9999` |
| Control Server | `http://lights.local:5000` | `http://lights.<tailnet>.ts.net:5000` |
| MCP Server | `http://lights.local:5001` | `http://lights.<tailnet>.ts.net:5001` |

> **MagicDNS**: Tailscale automatically provides a DNS name based on the Pi's
> hostname. If your Pi is named `lights`, the MagicDNS name will be
> `lights.<your-tailnet>.ts.net`.

---

## Firewall Considerations

Tailscale traffic uses the `tailscale0` virtual interface and bypasses `ufw`
rules by default. Your existing firewall configuration (which only exposes SSH
and QLC+ on the local network) remains unchanged — Tailscale creates a
separate encrypted tunnel.

If you want to restrict which tailnet devices can access specific ports, use
[Tailscale ACLs](https://tailscale.com/kb/1018/acls) in your admin console.

---

## Optional: SSH Over Tailscale

You can also SSH to the Pi from anywhere on your tailnet:

```bash
ssh riversway@100.x.x.x
# or with MagicDNS:
ssh riversway@lights.<tailnet>.ts.net
```

This is useful when your Pi isn't on the same local network (e.g., it's at the
studio and you're at home).

---

## Optional: Tailscale Funnel (Public Access)

If you need to share access with someone NOT on your tailnet (e.g., a guest
lighting operator), you can temporarily expose a port publicly:

```bash
# On the Pi:
sudo tailscale funnel 5000
```

This gives a public HTTPS URL anyone can use. Remove it when done:

```bash
sudo tailscale funnel --remove 5000
```

> ⚠️ **Security**: Funnel exposes your service to the internet. Only use it
> temporarily and for non-sensitive services.

---

## Troubleshooting

### Pi not appearing in tailnet

```bash
# Check daemon status
sudo systemctl status tailscaled

# Re-authenticate if needed
sudo tailscale up --reset
```

### MagicDNS not resolving

Ensure MagicDNS is enabled in your [Tailscale admin console](https://login.tailscale.com/admin/dns).
The Pi's hostname in Tailscale matches whatever `hostname` returns on the Pi.

### High latency

Tailscale uses direct connections when possible (DERP relay as fallback).
Check if the connection is direct:

```bash
tailscale status
# Look for "direct" vs "relay" in the connection type
tailscale ping lights
```

If using a relay, ensure both devices allow UDP traffic on port 41641.

---

## Uninstalling

```bash
# On the Pi:
sudo tailscale down
sudo apt remove tailscale
sudo rm /etc/apt/sources.list.d/tailscale.list
```
