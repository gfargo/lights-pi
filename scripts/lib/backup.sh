#!/usr/bin/env bash
# Backup and restore utility functions for lightsctl.sh
set -euo pipefail

# Create backup of QLC+ configuration
# Pass --auto to also push the resulting archive to BACKUP_REMOTE (if set).
function backup_create() {
  local auto=false
  for arg in "$@"; do
    [[ "$arg" == "--auto" ]] && auto=true
  done

  local stamp remote_tmp local_target dirs
  stamp="$(date -u +"%Y%m%dT%H%M%SZ")"
  remote_tmp="/tmp/qlcplus-backup-${stamp}.tar.gz"
  local_target="${BACKUP_STORAGE}/qlcplus-backup-${stamp}.tar.gz"
  dirs=()
  for entry in ".config/qlcplus" ".qlcplus"; do
    if run_sudo test -e "/home/${PI_USER}/${entry}"; then
      dirs+=("${entry}")
    fi
  done
  if [[ ${#dirs[@]} -eq 0 ]]; then
    echo "No QLC+ configuration found under /home/${PI_USER} on ${PI_HOST}."
    return 0
  fi
  mkdir -p "${BACKUP_STORAGE}"
  run_sudo tar -czf "${remote_tmp}" -C "/home/${PI_USER}" "${dirs[@]}"
  "${SCP_CMD[@]}" "${PI_USER}@${PI_HOST}:${remote_tmp}" "${local_target}"
  run_sudo rm -f "${remote_tmp}"
  echo "Backup saved to ${local_target}"

  # --auto: push to BACKUP_REMOTE if set
  if [[ "$auto" == true ]]; then
    local backup_remote="${BACKUP_REMOTE:-}"
    if [[ -z "$backup_remote" ]]; then
      echo "BACKUP_REMOTE not set — skipping remote push"
      return 0
    fi
    echo "Pushing to remote: ${backup_remote}"
    _push_to_remote "${local_target}" "${backup_remote}"
    echo "Remote push complete"
  fi
}

# Push a local file to BACKUP_REMOTE (s3://, rclone:, or scp-style).
# Used by backup_create --auto (workstation side, not on-Pi side).
function _push_to_remote() {
  local file="$1" remote="$2"
  if [[ "$remote" == s3://* ]]; then
    aws s3 cp "${file}" "${remote%/}/$(basename "${file}")"
  elif [[ "$remote" == rclone:* ]]; then
    rclone copy "${file}" "${remote#rclone:}"
  else
    scp "${file}" "${remote%/}/$(basename "${file}")"
  fi
}

# Fetch a backup URI (s3://, rclone:, or user@host:/path/file.tar.gz) to a
# local tmp path and echo the resulting local path. Leaves non-URI args
# (plain local paths) untouched. Does not check existence — callers verify.
function _fetch_from_remote() {
  local backup_file="$1"
  local local_file="$backup_file"

  # Transfer commands' stdout is discarded below — this function's own stdout
  # is the caller's `local_file="$(_fetch_from_remote ...)"` capture, and e.g.
  # `aws s3 cp` prints a "download: ..." completion line to stdout by default,
  # which would otherwise get appended into the returned path.
  if [[ "$backup_file" == s3://* ]]; then
    local tmp_file="/tmp/restore-$(basename "${backup_file}")"
    echo "Downloading from S3: ${backup_file}" >&2
    aws s3 cp "${backup_file}" "${tmp_file}" >/dev/null
    local_file="$tmp_file"
  elif [[ "$backup_file" == rclone:* ]]; then
    local tmp_file="/tmp/restore-$(basename "${backup_file}")"
    echo "Downloading via rclone: ${backup_file#rclone:}" >&2
    rclone copyto "${backup_file#rclone:}" "$tmp_file" >/dev/null
    local_file="$tmp_file"
  elif [[ "$backup_file" == *@*:* ]]; then
    local tmp_file="/tmp/restore-$(basename "${backup_file##*:}")"
    echo "Downloading via scp: ${backup_file}" >&2
    scp "${backup_file}" "${tmp_file}" >/dev/null
    local_file="$tmp_file"
  fi

  echo "$local_file"
}

# Restore QLC+ configuration from backup
# Accepts a local path or a remote URI (s3://, rclone:, or user@host:/path/file.tar.gz).
function backup_restore() {
  local backup_file="${1:-}"
  if [[ -z "$backup_file" ]]; then
    echo "Usage: restore <path/to/backup.tar.gz>" >&2
    echo "" >&2
    echo "Available backups in ${BACKUP_STORAGE}:" >&2
    if [[ -d "$BACKUP_STORAGE" ]]; then
      ls -1t "${BACKUP_STORAGE}"/*.tar.gz 2>/dev/null | head -5 || echo "  (none found)"
    else
      echo "  (backup directory does not exist)"
    fi
    return 1
  fi

  local local_file
  local_file="$(_fetch_from_remote "$backup_file")"

  if [[ ! -f "$local_file" ]]; then
    echo "Backup file not found: ${local_file}" >&2
    return 1
  fi

  local remote_tmp="/tmp/qlcplus-restore-$$.tar.gz"
  local control_service="lighting-control.service"

  echo "Stopping services..."
  run_sudo systemctl stop "${SERVICE}"
  run_sudo systemctl stop "${control_service}" 2>/dev/null || true

  echo "Uploading backup to Pi..."
  "${SCP_CMD[@]}" "$local_file" "${PI_USER}@${PI_HOST}:${remote_tmp}"

  echo "Backing up current config (just in case)..."
  run_sudo tar -czf "/tmp/qlcplus-pre-restore-backup.tar.gz" -C "/home/${PI_USER}" \
    ".config/qlcplus" ".qlcplus" 2>/dev/null || true

  echo "Removing existing QLC+ config..."
  run_sudo rm -rf "/home/${PI_USER}/.config/qlcplus" "/home/${PI_USER}/.qlcplus"

  echo "Extracting backup..."
  run_sudo tar -xzf "${remote_tmp}" -C "/home/${PI_USER}"

  echo "Fixing ownership..."
  run_sudo chown -R "${PI_USER}:${PI_USER}" "/home/${PI_USER}/.config/qlcplus" "/home/${PI_USER}/.qlcplus" 2>/dev/null || true

  echo "Cleaning up..."
  run_sudo rm -f "${remote_tmp}"

  echo "Restarting services..."
  run_sudo systemctl start "${SERVICE}"
  run_sudo systemctl start "${control_service}" 2>/dev/null || true

  echo ""
  echo "Restore complete! Pre-restore backup saved on Pi at:"
  echo "  /tmp/qlcplus-pre-restore-backup.tar.gz"
  echo ""

  run_sudo systemctl status "${SERVICE}" --no-pager
}

# ── Automated backup timer install/manage ────────────────────────────────────

function backup_timer_install() {
  local backup_script="${SCRIPT_DIR}/scripts/services/lighting-backup.sh"
  if [[ ! -f "$backup_script" ]]; then
    echo "Error: lighting-backup.sh not found at ${backup_script}" >&2
    return 1
  fi

  echo "Installing automated backup timer on ${PI_HOST}..."

  "${SCP_CMD[@]}" "$backup_script" "${PI_USER}@${PI_HOST}:/tmp/lighting-backup.sh"
  run_sudo mv /tmp/lighting-backup.sh /usr/local/bin/lighting-backup.sh
  run_sudo chmod +x /usr/local/bin/lighting-backup.sh

  # systemd service (reads env from ~/.env on the Pi)
  run_sudo tee /etc/systemd/system/lighting-backup.service >/dev/null <<EOF
[Unit]
Description=Lighting controller — daily config backup
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${PI_USER}
EnvironmentFile=-/home/${PI_USER}/.env
ExecStart=/usr/local/bin/lighting-backup.sh
EOF

  # systemd timer — 4am daily, Persistent so a missed run fires on next boot
  run_sudo tee /etc/systemd/system/lighting-backup.timer >/dev/null <<'EOF'
[Unit]
Description=Run lighting-backup daily at 04:00

[Timer]
OnCalendar=*-*-* 04:00:00
Persistent=true
AccuracySec=60

[Install]
WantedBy=timers.target
EOF

  run_sudo systemctl daemon-reload
  run_sudo systemctl enable --now lighting-backup.timer

  echo ""
  echo "✓ Automated backup timer installed"
  echo "  Runs daily at 04:00 Pi local time"
  echo "  Snapshots stored in ~/lights-pi-backups/ on the Pi"
  echo "  Set BACKUP_REMOTE in ~/.env on the Pi for remote push"
  echo "  Snapshots exclude ~/.env by default — set BACKUP_INCLUDE_ENV=1 in"
  echo "  ~/.env to include it (secrets then travel unencrypted to BACKUP_REMOTE)"
  echo ""
  echo "  Status:  ./lightsctl.sh backup-timer-status"
  echo "  Logs:    ./lightsctl.sh backup-timer-logs"
  echo "  Remove:  ./lightsctl.sh backup-timer-uninstall"
}

function backup_timer_status() {
  echo "=== Backup Timer Status ==="
  if run_sudo systemctl cat lighting-backup.timer &>/dev/null; then
    run_sudo systemctl status lighting-backup.timer --no-pager || true
  else
    echo "Timer not installed"
  fi
  echo ""
  echo "--- Last run ---"
  if run_sudo systemctl cat lighting-backup.service &>/dev/null; then
    run_sudo systemctl status lighting-backup.service --no-pager || true
  else
    echo "Service not installed"
  fi
}

function backup_timer_logs() {
  run_sudo journalctl -u lighting-backup.service -n 50 --no-pager
}

function backup_timer_uninstall() {
  echo "Removing automated backup timer..."
  run_sudo systemctl disable --now lighting-backup.timer 2>/dev/null || true
  run_sudo rm -f \
    /etc/systemd/system/lighting-backup.service \
    /etc/systemd/system/lighting-backup.timer \
    /usr/local/bin/lighting-backup.sh
  run_sudo systemctl daemon-reload
  echo "✓ Automated backup timer removed"
}

# System update
function system_update() {
  run_sudo apt update
  run_sudo apt -y upgrade
}

# Export functions
export -f backup_create
export -f _fetch_from_remote
export -f backup_restore
export -f backup_timer_install
export -f backup_timer_status
export -f backup_timer_logs
export -f backup_timer_uninstall
export -f system_update
export -f _push_to_remote
