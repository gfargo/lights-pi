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
  echo "Run: ./lightsctl.sh ssl-nginx  to configure nginx with SSL (recommended)"
  echo "Or:  ./lightsctl.sh ssl-proxy  to use stunnel (simpler, less features)"
}

# Generate locally-trusted certificate using mkcert
function tls_gen_cert_mkcert() {
  local cert_dir="${SCRIPT_DIR}/certs"
  local cert="${cert_dir}/qlc.crt"
  local key="${cert_dir}/qlc.key"

  # Check if mkcert is installed
  if ! command -v mkcert >/dev/null 2>&1; then
    echo "mkcert not found. Installing via Homebrew..."
    if ! command -v brew >/dev/null 2>&1; then
      echo "Homebrew not found. Install from: https://brew.sh" >&2
      return 1
    fi
    brew install mkcert
  fi

  # Install local CA if not already installed
  if ! mkcert -CAROOT >/dev/null 2>&1 || [[ ! -f "$(mkcert -CAROOT)/rootCA.pem" ]]; then
    echo "Installing mkcert local CA..."
    mkcert -install
  fi

  # Remove old certs if they exist
  if [[ -f "$cert" || -f "$key" ]]; then
    echo "Removing existing certificates..."
    rm -f "$cert" "$key"
  fi

  mkdir -p "$cert_dir"
  
  # Generate certificate for lights.local and localhost
  echo "Generating locally-trusted certificate..."
  cd "$cert_dir"
  mkcert -cert-file qlc.crt -key-file qlc.key "${PI_HOSTNAME}.local" localhost 127.0.0.1
  cd - >/dev/null

  chmod 600 "$key"

  echo ""
  echo "✓ Locally-trusted certificate generated!"
  echo "  Certificate: ${cert}"
  echo "  Private key: ${key}"
  echo ""
  echo "This certificate is trusted by your system and won't show browser warnings."
}

# Complete SSL setup: generate mkcert cert + configure nginx
function tls_setup_ssl() {
  echo "=== Complete SSL Setup ==="
  echo ""
  
  # Step 1: Generate mkcert certificate
  echo "Step 1: Generating locally-trusted certificate..."
  tls_gen_cert_mkcert
  
  echo ""
  echo "Step 2: Configuring nginx with SSL on Pi..."
  tls_ssl_nginx
  
  echo ""
  echo "=== SSL Setup Complete! ==="
  echo ""
  echo "✓ Certificate generated and trusted by your system"
  echo "✓ Nginx configured with SSL + reverse proxy"
  echo ""
  echo "Access your lighting controller:"
  echo "  https://lights.local/      → Landing page"
  echo "  https://lights.local/qlc/  → QLC+ interface"
  echo ""
  echo "No browser warnings - the certificate is fully trusted!"
}

# Configure nginx with SSL and reverse proxy to QLC+
function tls_ssl_nginx() {
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

  local remote_dir="/etc/ssl/qlc"
  local remote_cert="${remote_dir}/qlc.crt"
  local remote_key="${remote_dir}/qlc.key"

  # Upload certs
  "${SCP_CMD[@]}" "$cert_local" "${PI_USER}@${PI_HOST}:/tmp/qlc.crt"
  "${SCP_CMD[@]}" "$key_local" "${PI_USER}@${PI_HOST}:/tmp/qlc.key"
  run_sudo mkdir -p "${remote_dir}"
  run_sudo mv /tmp/qlc.crt "${remote_cert}"
  run_sudo mv /tmp/qlc.key "${remote_key}"
  run_sudo chmod 644 "${remote_cert}"
  run_sudo chmod 600 "${remote_key}"

  # Configure nginx with SSL
  run_sudo tee /etc/nginx/sites-available/lights >/dev/null <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    root /var/www/html;
    index index.html;

    # Landing page
    location = / {
        try_files /index.html =404;
    }

    # Serve static files for landing page
    location / {
        try_files \$uri \$uri/ =404;
    }
}

server {
    listen 443 ssl default_server;
    listen [::]:443 ssl default_server;
    server_name _;

    ssl_certificate ${remote_cert};
    ssl_certificate_key ${remote_key};
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    root /var/www/html;
    index index.html;

    # Landing page
    location = / {
        try_files /index.html =404;
    }

    # Reverse proxy to QLC+ (strip /qlc prefix)
    location /qlc/ {
        rewrite ^/qlc/(.*)\$ /\$1 break;
        proxy_pass http://127.0.0.1:${QLC_PORT};
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_buffering off;
        proxy_read_timeout 86400;
    }

    # Serve static files for landing page
    location / {
        try_files \$uri \$uri/ =404;
    }
}
EOF

  run_sudo ln -sf /etc/nginx/sites-available/lights /etc/nginx/sites-enabled/lights
  run_sudo rm -f /etc/nginx/sites-enabled/default
  run_sudo nginx -t
  run_sudo systemctl reload nginx

  # Open firewall port 443
  if run command -v ufw >/dev/null 2>&1 && run sudo ufw status | grep -q "Status: active"; then
    run_sudo ufw allow 443/tcp comment 'HTTPS' 2>/dev/null || true
  fi

  echo "✓ SSL configured with nginx"
  echo ""
  echo "Access points:"
  echo "  http://lights.local/       → Landing page (HTTP)"
  echo "  https://lights.local/      → Landing page (HTTPS)"
  echo "  https://lights.local/qlc/  → QLC+ web interface"
  echo ""
  echo "Note: HTTP and HTTPS both work. No forced redirect."
  echo "      Users without the certificate installed can use HTTP."
}

# Install SSL proxy on Pi (stunnel - simpler but less flexible)
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

[https]
accept = 443
connect = 127.0.0.1:80
EOF

  run_sudo sed -i 's/^ENABLED=0/ENABLED=1/' /etc/default/stunnel4 || true
  run_sudo systemctl enable --now stunnel4

  if ! run_sudo iptables -t nat -C PREROUTING -p tcp --dport 443 -j REDIRECT --to-ports "${QLC_PORT}" >/dev/null 2>&1; then
    run_sudo iptables -t nat -A PREROUTING -p tcp --dport 443 -j REDIRECT --to-ports "${QLC_PORT}"
  fi
  run_sudo netfilter-persistent save >/dev/null 2>&1 || true
  echo "SSL proxy configured: https://lights.local/ → nginx (port 80)"
  echo "QLC+ remains accessible at: http://lights.local:${QLC_PORT}/"
}

# Export functions
export -f tls_gen_cert
export -f tls_gen_cert_mkcert
export -f tls_setup_ssl
export -f tls_ssl_nginx
export -f tls_ssl_proxy
