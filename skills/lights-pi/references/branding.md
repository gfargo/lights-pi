# Branding an install

Source of truth: `docs/CUSTOMIZATION.md`. Everything here lives on the Pi, outside git, and
survives `scripts/deploy.sh`.

## 1. Name and accent colour (control UI, port 5000)

Edit the Pi's `~/control-server/.env` (`./lightsctl.sh ssh`, then
`nano ~/control-server/.env`):

```bash
BRAND_NAME="Harbour Studio"     # header, browser tab, installed app name
BRAND_SHORT_NAME="Harbour"      # home-screen label, 12 characters max
BRAND_ACCENT="#1e50ff"          # the one action colour, #rgb or #rrggbb
```

Then on the Pi: `sudo systemctl restart lighting-control`.

| Variable | Default | Notes |
|---|---|---|
| `BRAND_NAME` | `Lights Pi` | Trimmed, 40 characters max |
| `BRAND_SHORT_NAME` | `BRAND_NAME` if it fits in 12 characters, else its first word | Home-screen label |
| `BRAND_ACCENT` | `#ff6a1f` | Invalid values fall back to the default |

- Pick an accent with enough contrast against the dark UI; it marks primary actions and the
  current selection.
- A phone that already installed the app keeps the old name until the app is removed and
  added to the home screen again.
- Do not set these via `./lightsctl.sh env-sync` unless the workstation `.env` also holds
  every Pi secret; it overwrites the Pi's file (see setup.md, section 3).

## 2. Logo (optional)

Without a logo, the header and app icon show the swatch mark. To use your own square image:

```bash
ssh <pi-user>@<pi-host> 'mkdir -p ~/control-server/static'
scp your-logo.webp <pi-user>@<pi-host>:~/control-server/static/logo.webp
```

- Accepted: `.webp`, `.png`, `.svg`, `.jpg`, in that priority order.
- No restart needed; hard-refresh the browser. Transparent or dark background, at least
  96x96 px.
- `deploy.sh` skips `static/logo.*`, so copying a logo into a local checkout does not put it
  on the Pi. Copy it to the Pi directly.

## 3. Anything else: `theme.css` (optional)

Write plain CSS to `~/control-server/static/theme.css` on the Pi. When present it is served
at `/theme.css` and loaded after the built-in styles:

```css
:root {
  --ink: #050608;     /* page background */
  --paper: #f4f1ea;   /* text */
}
```

Override the design tokens declared at the top of `control-server/templates/index.html`
(`--ink`, `--paper`, `--rule`, `--amber-tungsten`, `--signal-*`, ...) rather than individual
rules, so upgrades keep working. Read that file for the current token list before writing
overrides. Like the logo, `theme.css` is gitignored and skipped by `deploy.sh`.

## 4. Landing page (nginx, port 80/443)

Separate from the control UI. Set these in the **workstation** `.env` (next to
`lightsctl.sh`), then run `./lightsctl.sh landing-deploy`:

`LANDING_TITLE`, `LANDING_STUDIO_NAME`, `LANDING_SUBTITLE`, `LANDING_BUTTON_TEXT`,
`LANDING_FOOTER_TEXT`, `QLC_URL`.

The landing page logo is `/var/www/html/logo.webp` on the Pi; `landing-deploy` pushes HTML
only:

```bash
scp your-logo.webp <pi-user>@<pi-host>:/tmp/logo.webp
ssh <pi-user>@<pi-host> 'sudo mv /tmp/logo.webp /var/www/html/logo.webp'
```

## How it works (for code changes)

`_brand_config()` in `control-server/app.py` reads and validates the variables at start-up
and passes `brand` to every template. `/manifest.json` uses the names, `/icon.svg` is the
swatch mark, `/logo` serves `static/logo.*`, `/theme.css` serves `static/theme.css` or 404s.
