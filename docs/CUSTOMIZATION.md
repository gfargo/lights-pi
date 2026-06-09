# Customization & Branding

This project is designed as a generic lighting control platform. Each
deployment can be branded for a specific studio, venue, or production without
modifying tracked source files.

---

## Custom Logo

Both the landing page (port 80) and the control server (port 5000) support a
custom logo image. The logo file is **gitignored** so each deployment maintains
its own identity without polluting the shared repo.

### Convention

Drop a file named `logo.<ext>` into the appropriate directory:

| Interface | Directory | Served at |
|-----------|-----------|-----------|
| Control Server (`:5000`) | `control-server/static/logo.<ext>` | `/logo` |
| Landing Page (`:80`) | `landing/logo.<ext>` | `/logo.<ext>` (nginx static) |

**Supported formats** (in priority order): `.webp`, `.png`, `.svg`, `.jpg`

If no logo file is present, both interfaces fall back to the built-in SVG
light-bulb icon.

### Setup

```bash
# Copy your logo (any supported format) into both locations:
cp your-studio-logo.webp control-server/static/logo.webp
cp your-studio-logo.webp landing/logo.webp

# Deploy control server (includes the static/ directory)
bash scripts/deploy.sh

# Deploy landing page
./lightsctl.sh landing-deploy

# Push the logo to nginx's web root (landing page)
scp landing/logo.webp pi@lights.local:/tmp/logo.webp
ssh pi@lights.local 'sudo mv /tmp/logo.webp /var/www/html/logo.webp'
```

### How It Works

**Control Server** — The `/logo` route in `app.py` scans
`control-server/static/` for any file matching `logo.*` and serves the first
match with appropriate MIME type and cache headers. The HTML template uses an
`<img>` tag that falls back to an inline SVG on 404.

**Landing Page** — The `landing/index.html` template references `logo.webp`
directly. The `landing-deploy` command pushes the HTML via sed variable
substitution; the logo file must be copied to `/var/www/html/` separately (or
included in a custom deploy script).

### Recommendations

- **Format**: WebP for smallest file size with transparency support
- **Dimensions**: At least 96×96px for crisp display on retina screens
- **Shape**: Square or circular works best (displayed with `border-radius: 50%`)
- **Background**: Transparent or dark (matches the dark UI theme)

---

## Landing Page Branding

The landing page supports text customization through environment variables in
`.env`:

```bash
LANDING_TITLE="Lighting Controller"        # Browser tab title
LANDING_STUDIO_NAME="Your Studio"          # Main heading
LANDING_SUBTITLE="Lighting Controller"     # Subtitle text
LANDING_BUTTON_TEXT="Lighting Control"     # Primary button label
LANDING_FOOTER_TEXT="lights.local"         # Footer text
QLC_URL=http://lights.local:9999/          # QLC+ dashboard button URL
```

After editing `.env`, redeploy:

```bash
./lightsctl.sh landing-deploy
```

---

## QLC+ Workspace

The QLC+ workspace file (`studio.qxw`) defines your fixtures, scenes, and
virtual console layout. It lives on the Pi at `~/.qlcplus/default.qxw`.

### Version Compatibility

The Pi runs **QLC+ 4.14.1**. If you edit workspaces on a desktop running QLC+
5.x, the file format is incompatible. Key differences:

| Feature | QLC+ 4.x | QLC+ 5.x |
|---------|-----------|-----------|
| Version tag | `4.14.1` | `5.x.x` |
| `BeatGenerator` | Not supported | Included |
| `Palette` elements | Not supported | Included |
| Namespace prefixes | Bare elements | `ns0:` prefixes possible |
| Output UID | Device-specific string | May use `"None"` |

**If the web UI loads blank**: check the workspace version tag. Convert v5
workspaces by:
1. Changing `<Version>` to `4.14.1`
2. Removing `<BeatGenerator>` elements
3. Removing `<Palette>` elements
4. Adding a `<SimpleDesk><Engine/></SimpleDesk>` section before `</Workspace>`

The `scripts/debug/` directory has tools for workspace diagnostics:
- `fix_qlc_workspace_autoload.sh` — Ensures autostart.qxw is a real file
- `fix_qlc_service.sh` — Updates the service config for reliable loading
- `push_workspace.sh` — Deploys and verifies a workspace

### Deploying Workspaces

```bash
# From the repo root:
./lightsctl.sh deploy-workspace studio.qxw

# Or set as default (copies to ~/.qlcplus/default.qxw):
./lightsctl.sh set-default studio.qxw
```

---

## Control Server UI Theme

The control server UI uses CSS custom properties defined at the top of
`control-server/templates/index.html`. The default theme is dark with a
tungsten-amber accent. To customize colors without modifying the template,
you could override variables via a user stylesheet (future feature).

Current palette:

| Variable | Default | Purpose |
|----------|---------|---------|
| `--ink` | `#0a0a0a` | Page background |
| `--paper` | `#f0f0f0` | Primary text |
| `--amber-tungsten` | `#d97757` | Brand accent |
| `--arc-cyan` | `#76e8ff` | Live/streaming indicator |

---

## Pi Hostname

The Pi's hostname affects mDNS resolution and the Tailscale MagicDNS name. Set
it during provisioning via `PI_HOSTNAME` in `.env`:

```bash
PI_HOSTNAME=lights    # → lights.local on LAN, lights.<tailnet>.ts.net on Tailscale
```

To change after provisioning:

```bash
./lightsctl.sh ssh
sudo hostnamectl set-hostname lights
sudo reboot
```
