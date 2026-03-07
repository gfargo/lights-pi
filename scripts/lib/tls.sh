#!/usr/bin/env bash
# TLS/SSL certificate and proxy utility functions for lightsctl.sh
set -euo pipefail

# Generate self-signed certificate
function tls_gen_cert() {
  local days="${1:-730}"
  local cert_dir="${SCRIPT_DIR}/certs"
  local cert="${cert_dir}/qlc.crt"
  local key="${cert_dir}/qlc.key"

  if ! command -v openssl >/dev/null 2>&1; then
    echo "openssl not found. Install it with: brew install openssl" >&2
    return 1
  fi
  if [[ -f "$cert" || -f "$key" ]]; then
    echo "Certs already exist in ${cert_dir}/. Delete them first to regenerate." >&2
    return 1
  fi

  # Build SANs: always include hostname.local; add IP or extra DNS if PI_HOST is set
  local san="DNS:${PI_HOSTNAME}.local,DNS:localhost"
  if [[ -n "$PI_HOST" && "$PI_HOST" != "${PI_HOSTNAME}.local" ]]; then
    if [[ "$PI_HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      san="${san},IP:${PI_HOST}"
    else
      san="${san},DNS:${PI_HOST}"
    fi
  fi

  local tmpconf
  tmpconf="$(mktemp /tmp/qlc-openssl-XXXXXX.cnf)"
  # shellcheck disable=SC2064
  trap "rm -f '$tmpconf'" RETURN

  cat > "$tmpconf" <<CONF
[req]
distinguished_name = dn
x509_extensions    = san
prompt             = no

[dn]
CN = ${PI_HOSTNAME}.local

[san]
subjectAltName = ${san}
CONF

  mkdir -p "$cert_dir"
  openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout "$key" -out "$cert" \
    -days "$days" \
    -config "$tmpconf" 2>/dev/null
  chmod 600 "$key"

  echo "Certificate: ${cert}"
  echo "Private key: ${key}"
  echo "Valid for:   ${days} days"
  echo "SANs:        ${san}"
  echo ""
  echo "Run: ./lightsctl.sh ssl-proxy  to install on the Pi."
}

# Install SSL proxy on Pi
function tls_ssl_proxy() {
  local cert_local="${1:-${SSL_CERT}}"
  local key_local="${2:-${SSL_KEY}}"
  if [[ ! -f "$cert_local" ]]; then
    echo "Certificate not found at ${cert_local}"
    return 1
  fi
  if [[ ! -f "$key_local" ]]; then
    echo "Private key not found at ${key_local}"
    return 1
  fi

  run_sudo apt-get update
  run_sudo apt-get install -y stunnel4 iptables-persistent

  local remote_dir="/etc/ssl/qlc"
  local remote_cert="${remote_dir}/qlc.crt"
  local remote_key="${remote_dir}/qlc.key"

  "${SCP_CMD[@]}" "$cert_local" "${PI_USER}@${PI_HOST}:/tmp/qlc.crt"
  "${SCP_CMD[@]}" "$key_local" "${PI_USER}@${PI_HOST}:/tmp/qlc.key"
  run_sudo mkdir -p "${remote_dir}"
  run_sudo mv /tmp/qlc.crt "${remote_cert}"
  run_sudo mv /tmp/qlc.key "${remote_key}"
  run_sudo chmod 644 "${remote_cert}"
  run_sudo chmod 600 "${remote_key}"

  run_sudo tee /etc/stunnel/qlc.conf >/dev/null <<EOF
; Global options
cert = ${remote_cert}
key = ${remote_key}
pid = /var/run/stunnel4-qlc.pid
socket = l:TCP_NODELAY=1
socket = r:TCP_NODELAY=1

[qlc]
accept = 443
connect = 127.0.0.1:${QLC_PORT}
EOF

  run_sudo sed -i 's/^ENABLED=0/ENABLED=1/' /etc/default/stunnel4 || true
  run_sudo systemctl enable --now stunnel4

  if ! run_sudo iptables -t nat -C PREROUTING -p tcp --dport 443 -j REDIRECT --to-ports "${QLC_PORT}" >/dev/null 2>&1; then
    run_sudo iptables -t nat -A PREROUTING -p tcp --dport 443 -j REDIRECT --to-ports "${QLC_PORT}"
  fi
  run_sudo netfilter-persistent save >/dev/null 2>&1 || true
  echo "SSL proxy configured on 443 → ${QLC_PORT} using ${remote_cert}"
}

# Export functions
export -f tls_gen_cert
export -f tls_ssl_proxy
