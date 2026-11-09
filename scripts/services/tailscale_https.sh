#!/usr/bin/env bash
# Trusted HTTPS for the control server and QLC+ over Tailscale, WITHOUT touching their own ports.
#
#   https://<pi>.<tailnet>.ts.net:5443  ->  control server   (127.0.0.1:5000)
#   https://<pi>.<tailnet>.ts.net:9443  ->  QLC+ web         (127.0.0.1:9998 -> 127.0.0.1:9999)
#
# Tailscale issues a real certificate for the node's MagicDNS name (no browser warning), and
# `tailscale serve` terminates TLS. The plain-HTTP ports (:5000 on the LAN, :9999) keep working
# exactly as before, so this is purely additive and reversible (`remove`).
#
# Why the small nginx shim in front of QLC+: its web page opens its live connection with a
# hard-coded "ws://" (websocket.js, simpledesk.js). A browser refuses that from an HTTPS page, so
# the dashboard would load but never connect. The shim rewrites "ws://" to "wss://" in those
# scripts; the WebSocket itself passes straight through.
#
# Usage: PI_HOST=... PI_USER=... tailscale_https.sh install|status|remove
set -euo pipefail

ACTION="${1:-install}"
PI_HOST="${PI_HOST:-}"
PI_USER="${PI_USER:-riversway}"
CONTROL_PORT="${CONTROL_PORT:-5000}"
QLC_PORT="${QLC_PORT:-9999}"
CONTROL_TLS_PORT="${CONTROL_TLS_PORT:-5443}"
QLC_TLS_PORT="${QLC_TLS_PORT:-9443}"
SHIM_PORT="${QLC_SHIM_PORT:-9998}"
CONF="/etc/nginx/conf.d/qlc-wss-shim.conf"

[[ -n "$PI_HOST" ]] || { echo "Set PI_HOST to the Pi's Tailscale or LAN name." >&2; exit 1; }

remote() { ssh -o BatchMode=yes "${PI_USER}@${PI_HOST}" "$@"; }

case "$ACTION" in
  install)
    remote "sudo bash -s" <<REMOTE
set -euo pipefail
command -v tailscale >/dev/null || { echo "tailscale is not installed on the Pi" >&2; exit 1; }
command -v nginx >/dev/null || [ -x /usr/sbin/nginx ] || { echo "nginx is not installed on the Pi" >&2; exit 1; }

cat > "${CONF}" <<'NGINX'
# Managed by lightsctl tailscale-https. Loopback only: reached solely through tailscale serve.
map \$http_upgrade \$qlc_connection_upgrade { default upgrade; '' close; }

server {
    listen 127.0.0.1:${SHIM_PORT};
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:${QLC_PORT};
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$qlc_connection_upgrade;
        proxy_set_header Host \$host;
        proxy_set_header Accept-Encoding "";      # sub_filter needs an uncompressed body
        proxy_read_timeout 86400;                 # long-lived WebSocket
        sub_filter_types text/javascript application/javascript;
        sub_filter '"ws://"' '"wss://"';
        sub_filter_once off;
    }
}
NGINX

PATH="\$PATH:/usr/sbin"
nginx -t
systemctl reload nginx

tailscale serve --bg --https=${CONTROL_TLS_PORT} http://127.0.0.1:${CONTROL_PORT}
tailscale serve --bg --https=${QLC_TLS_PORT} http://127.0.0.1:${SHIM_PORT}
REMOTE
    echo "HTTPS enabled:"
    remote "tailscale serve status"
    ;;
  status)
    remote "tailscale serve status; echo; ls -l ${CONF} 2>&1; ss -ltn 2>/dev/null | grep -E ':${SHIM_PORT}\b' || echo 'shim not listening'"
    ;;
  remove)
    remote "sudo bash -s" <<REMOTE
set -euo pipefail
tailscale serve --https=${CONTROL_TLS_PORT} off || true
tailscale serve --https=${QLC_TLS_PORT} off || true
rm -f "${CONF}"
PATH="\$PATH:/usr/sbin"
nginx -t && systemctl reload nginx
REMOTE
    echo "HTTPS endpoints removed; :${CONTROL_PORT} and :${QLC_PORT} are untouched."
    ;;
  *)
    echo "Usage: $0 install|status|remove" >&2; exit 2 ;;
esac
