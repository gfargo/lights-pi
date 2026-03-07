#!/usr/bin/env bash
# Installs nginx on the Pi and serves landing/index.html on port 80.
# Run via: ./lightsctl.sh landing-setup
# Update the page later with: ./lightsctl.sh landing-deploy
set -euo pipefail

PI_HOST="${PI_HOST:-}"
PI_USER="${PI_USER:-pi}"
QLC_PORT="${QLC_PORT:-9999}"
LANDING_SRC="${LANDING_SRC:-}"   # set by lightsctl.sh to SCRIPT_DIR/landing/index.html

if [[ -z "$PI_HOST" ]]; then
  echo "Set PI_HOST to the Pi's IP or hostname."
  exit 1
fi

if [[ -z "$LANDING_SRC" || ! -f "$LANDING_SRC" ]]; then
  echo "landing/index.html not found at ${LANDING_SRC}" >&2
  exit 1
fi

REMOTE_HTML="/var/www/html/index.html"
NGINX_SITE="/etc/nginx/sites-available/lights"

# Inject the real QLC+ URL into the HTML before uploading
RENDERED="$(mktemp /tmp/qlc-landing-XXXXXX.html)"
trap "rm -f '$RENDERED'" EXIT
sed "s|__QLC_URL__|http://${PI_HOST}:${QLC_PORT}|g" "$LANDING_SRC" > "$RENDERED"

# Install nginx if not already present
ssh "${PI_USER}@${PI_HOST}" "sudo bash -s" <<EOF
set -euo pipefail
if ! command -v nginx >/dev/null 2>&1; then
  apt-get update -q
  apt-get install -y -q nginx
fi
systemctl enable nginx
systemctl start nginx || true
EOF

# Push the rendered HTML
scp "$RENDERED" "${PI_USER}@${PI_HOST}:/tmp/qlc-landing.html"
ssh "${PI_USER}@${PI_HOST}" "sudo mv /tmp/qlc-landing.html ${REMOTE_HTML} && sudo chmod 644 ${REMOTE_HTML}"

# Write nginx site config (only once — skip if already configured)
ssh "${PI_USER}@${PI_HOST}" "sudo bash -s" <<EOF
set -euo pipefail
if [[ ! -f "${NGINX_SITE}" ]]; then
  cat > "${NGINX_SITE}" <<NGINX
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    root /var/www/html;
    index index.html;
    server_name _;
    location / { try_files \\\$uri \\\$uri/ =404; }
}
NGINX
  ln -sf "${NGINX_SITE}" /etc/nginx/sites-enabled/lights
  rm -f /etc/nginx/sites-enabled/default
  nginx -t && systemctl reload nginx
fi
EOF

# Open ufw port 80 if ufw is active
ssh "${PI_USER}@${PI_HOST}" "sudo bash -s" <<'EOF'
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp comment 'HTTP landing page' 2>/dev/null || true
fi
EOF

echo "Landing page live at http://${PI_HOST}"
echo "Button links to:   http://${PI_HOST}:${QLC_PORT}"
