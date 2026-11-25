# Customization & Branding

Each deployment can carry its own name, colour and logo without modifying
tracked source files. Everything below lives on the Pi, outside git, and
survives `scripts/deploy.sh`.

## Brand the control UI in three steps

The control UI (port 5000, the app people use on their phones) reads its
identity from the Pi's `~/control-server/.env`, plus two optional files in
`~/control-server/static/`.

### 1. Name and colour

Edit the Pi's `.env` (`./lightsctl.sh ssh`, then `nano ~/control-server/.env`)
and add:

```bash
BRAND_NAME="Harbour Studio"     # header, browser tab, installed app name
BRAND_SHORT_NAME="Harbour"      # home-screen label, 12 characters max
BRAND_ACCENT="#1e50ff"          # the one action colour, #rgb or #rrggbb
```

Then restart the service: `sudo systemctl restart lighting-control`.

| Variable | Default | Notes |
|---|---|---|
| `BRAND_NAME` | `Lights Pi` | Trimmed, 40 characters max. |
| `BRAND_SHORT_NAME` | `BRAND_NAME` if it fits in 12 characters, otherwise its first word | Shown under the icon when the app is installed to a home screen. |
| `BRAND_ACCENT` | `#ff6a1f` | Marks primary actions and the current selection. An invalid value falls back to the default rather than breaking the page. |

A phone that already installed the app keeps the old name until the app is
removed and added to the home screen again.

### 2. Logo (optional)

Without a logo the header and the app icon show the swatch mark: eight
colour cells, one per fixture, the way the UI draws a scene. To use your own
mark instead, put a square image on the Pi:

```bash
ssh <user>@<pi-host> 'mkdir -p ~/control-server/static'
scp your-logo.webp <user>@<pi-host>:~/control-server/static/logo.webp
```

`.webp`, `.png`, `.svg` and `.jpg` are accepted, in that order of priority.
No restart is needed; hard-refresh the browser. A transparent or dark
background and at least 96×96px look best.

`scripts/deploy.sh` deliberately skips `static/logo.*`, so a deploy never
removes a Pi's logo. It also means copying a logo into your local checkout
does not put it on the Pi; copy it there directly, as above.

### 3. Anything else: `theme.css` (optional)

For changes beyond the name and accent, write plain CSS to
`~/control-server/static/theme.css` on the Pi. When the file exists it is
served at `/theme.css` and loaded after the built-in styles, so it wins:

```css
/* ~/control-server/static/theme.css */
:root {
  --ink: #050608;          /* page background */
  --paper: #f4f1ea;        /* text */
}
.blackout-btn { letter-spacing: 0; }
```

The design tokens are declared at the top of
`control-server/templates/index.html`; override those rather than individual
rules where you can, so upgrades keep working. Like the logo, `theme.css` is
gitignored and skipped by `deploy.sh`.

### How it works

- `_brand_config()` in `control-server/app.py` reads the three variables once
  at start-up, validates them and falls back to defaults on bad input. A
  context processor passes the result to every template as `brand`.
- `/manifest.json` uses the name and short name, so an installed app is
  called what the studio is called.
- `/icon.svg` (the app icon) is the swatch mark; `/logo` serves
  `static/logo.*` when present and the header falls back to the swatch mark
  when it is not.
- `/theme.css` serves `static/theme.css`, or 404s, in which case the page
  never links it.

The design rules behind the UI (type sizes, one accent, the phone layout)
are in [MOBILE_AUDIT.md](MOBILE_AUDIT.md).

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

These variables live in your workstation's `.env` (the one next to
`lightsctl.sh`), because `landing-deploy` runs from the workstation.

The landing page shows `logo.webp` from nginx's web root. `landing-deploy`
pushes the HTML only, so copy the logo across once yourself:

```bash
scp your-logo.webp <user>@<pi-host>:/tmp/logo.webp
ssh <user>@<pi-host> 'sudo mv /tmp/logo.webp /var/www/html/logo.webp'
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
./lightsctl.sh set-default-workspace studio.qxw
```

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
