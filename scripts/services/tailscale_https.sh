#!/usr/bin/env bash
# Trusted HTTPS for the studio Pi over Tailscale, WITHOUT touching the backends' own ports.
#
#   https://<pi>.<tailnet>.ts.net        (:443)   landing page, nginx + the Tailscale certificate
#   https://<pi>.<tailnet>.ts.net:5443            control server  (127.0.0.1:5000)
#   https://<pi>.<tailnet>.ts.net:9443            QLC+ web        (127.0.0.1:9998 -> 127.0.0.1:9999)
#
# Tailscale issues a real (Let's Encrypt) certificate for the node's MagicDNS name, so browsers show
# a padlock instead of "Not Secure". The plain-HTTP ports (:5000, :9999, :80) and the existing mkcert
# certificate for https://lights.local keep working exactly as before: nginx picks the certificate
# by server name (SNI), so only the *.ts.net name gets the Tailscale one. Purely additive;
# `remove` undoes all of it.
#
# Pieces:
#   * `tailscale serve` terminates TLS on :5443 / :9443.
#   * A loopback nginx shim in front of QLC+: its web page opens its live connection with a
#     hard-coded "ws://" (websocket.js, simpledesk.js), which browsers refuse from an HTTPS page.
#     The shim rewrites it to "wss://" in those scripts; the WebSocket itself passes through.
#   * An nginx server block for the ts.net name on :443 using the Tailscale certificate, plus a
#     weekly systemd timer that renews it (these certificates last 90 days) and reloads nginx.
#
# Usage: PI_HOST=... PI_USER=... tailscale_https.sh install|status|remove
set -euo pipefail

ACTION="${1:-install}"
PI_HOST="${PI_HOST:-}"
PI_USER="${PI_USER:-riversway}"

[[ -n "$PI_HOST" ]] || { echo "Set PI_HOST to the Pi's Tailscale or LAN name." >&2; exit 1; }

# The remote work is one quoted script (no local expansion inside it); settings travel as env vars.
run_remote() {
  ssh -o BatchMode=yes "${PI_USER}@${PI_HOST}" \
    "sudo env CONTROL_PORT='${CONTROL_PORT:-5000}' QLC_PORT='${QLC_PORT:-9999}' \
              CONTROL_TLS_PORT='${CONTROL_TLS_PORT:-5443}' QLC_TLS_PORT='${QLC_TLS_PORT:-9443}' \
              SHIM_PORT='${QLC_SHIM_PORT:-9998}' ACTION='$1' bash -s"
}

run_remote "$ACTION" <<'REMOTE'
set -euo pipefail
export PATH="$PATH:/usr/sbin"
SHIM_CONF=/etc/nginx/conf.d/qlc-wss-shim.conf
TS_CONF=/etc/nginx/conf.d/tailscale-443.conf
# nginx's default server-name table (bucket size 32) cannot hold a name as long as
# <host>.<tailnet>.ts.net, and refuses to start. http-level, so it lives in conf.d.
HASH_CONF=/etc/nginx/conf.d/00-server-names-hash.conf
BACKUP_DIR="$(mktemp -d)"
CERT_DIR=/etc/ssl/tailscale
RENEW_SVC=/etc/systemd/system/tailscale-cert-renew.service
RENEW_TIMER=/etc/systemd/system/tailscale-cert-renew.timer

ts_name() {
  tailscale status --json | python3 -c 'import sys,json; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))'
}

case "$ACTION" in
  install)
    command -v tailscale >/dev/null || { echo "tailscale is not installed on the Pi" >&2; exit 1; }
    command -v nginx >/dev/null || { echo "nginx is not installed on the Pi" >&2; exit 1; }
    NAME="$(ts_name)"

    # Keep whatever is there now so a rejected config restores it instead of deleting a working one.
    for f in "$SHIM_CONF" "$TS_CONF" "$HASH_CONF"; do
      [ -f "$f" ] && cp -p "$f" "$BACKUP_DIR/$(basename "$f")" || true
    done
    printf '# Managed by lightsctl tailscale-https.\nserver_names_hash_bucket_size 64;\n' > "$HASH_CONF"

    # 1. QLC+ wss shim (loopback only; reached solely through tailscale serve)
    cat > "$SHIM_CONF" <<NGINX
# Managed by lightsctl tailscale-https.
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

    # 2. Tailscale certificate for the ts.net name, used on :443 for that name only
    install -d -m 755 "$CERT_DIR"
    tailscale cert --cert-file "$CERT_DIR/$NAME.crt" --key-file "$CERT_DIR/$NAME.key" "$NAME"
    chmod 644 "$CERT_DIR/$NAME.crt"; chmod 600 "$CERT_DIR/$NAME.key"

    cat > "$TS_CONF" <<NGINX
# Managed by lightsctl tailscale-https. Selected by server name (SNI), so https://lights.local
# keeps using the default server's mkcert certificate.
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name ${NAME};
    ssl_certificate     ${CERT_DIR}/${NAME}.crt;
    ssl_certificate_key ${CERT_DIR}/${NAME}.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    root /var/www/html;
    index index.html;

    location = / { try_files /index.html =404; }
    # QLC+'s own page needs the wss shim, which lives on the dedicated HTTPS port
    location ~ ^/qlc(/|\$) { return 302 https://\$host:${QLC_TLS_PORT}/; }
    location / { try_files \$uri \$uri/ =404; }
}
NGINX

    # 3. Weekly renewal (tailscale cert only re-issues when the certificate is near expiry)
    cat > "$RENEW_SVC" <<UNIT
[Unit]
Description=Renew the Tailscale certificate used by nginx
After=tailscaled.service

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'tailscale cert --cert-file ${CERT_DIR}/${NAME}.crt --key-file ${CERT_DIR}/${NAME}.key ${NAME} && chmod 644 ${CERT_DIR}/${NAME}.crt && chmod 600 ${CERT_DIR}/${NAME}.key && systemctl reload nginx'
UNIT
    cat > "$RENEW_TIMER" <<UNIT
[Unit]
Description=Weekly Tailscale certificate renewal

[Timer]
OnCalendar=Sun 04:17
RandomizedDelaySec=30m
Persistent=true

[Install]
WantedBy=timers.target
UNIT
    systemctl daemon-reload
    systemctl enable --now tailscale-cert-renew.timer >/dev/null

    # Never leave a config nginx cannot load: it would break the next restart.
    if ! nginx -t; then
      for f in "$SHIM_CONF" "$TS_CONF" "$HASH_CONF"; do
        if [ -f "$BACKUP_DIR/$(basename "$f")" ]; then cp -p "$BACKUP_DIR/$(basename "$f")" "$f"; else rm -f "$f"; fi
      done
      echo "nginx rejected the new config; restored the previous files. Nothing was reloaded." >&2
      exit 1
    fi
    systemctl reload nginx

    # 4. TLS for the control server and QLC+ on their own HTTPS ports
    tailscale serve --bg --https="${CONTROL_TLS_PORT}" "http://127.0.0.1:${CONTROL_PORT}"
    tailscale serve --bg --https="${QLC_TLS_PORT}" "http://127.0.0.1:${SHIM_PORT}"
    echo "HTTPS enabled for ${NAME}"
    ;;
  status)
    NAME="$(ts_name)"
    tailscale serve status
    echo
    ls -l "$SHIM_CONF" "$TS_CONF" 2>&1
    echo
    if [ -f "$CERT_DIR/$NAME.crt" ]; then
      openssl x509 -in "$CERT_DIR/$NAME.crt" -noout -subject -issuer -enddate
    else
      echo "no Tailscale certificate at $CERT_DIR/$NAME.crt"
    fi
    systemctl list-timers tailscale-cert-renew.timer --no-pager 2>&1 | head -3
    ;;
  remove)
    tailscale serve --https="${CONTROL_TLS_PORT}" off || true
    tailscale serve --https="${QLC_TLS_PORT}" off || true
    systemctl disable --now tailscale-cert-renew.timer 2>/dev/null || true
    rm -f "$RENEW_SVC" "$RENEW_TIMER" "$SHIM_CONF" "$TS_CONF" "$HASH_CONF"
    systemctl daemon-reload
    rm -rf "$CERT_DIR"
    nginx -t && systemctl reload nginx
    echo "HTTPS endpoints removed; the plain ports and the mkcert certificate are untouched."
    ;;
  *)
    echo "Usage: tailscale_https.sh install|status|remove" >&2; exit 2 ;;
esac
REMOTE
