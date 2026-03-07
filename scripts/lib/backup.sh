#!/usr/bin/env bash
# Backup and restore utility functions for lightsctl.sh
set -euo pipefail

# Create backup of QLC+ configuration
function backup_create() {
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
}

# Restore QLC+ configuration from backup
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
  if [[ ! -f "$backup_file" ]]; then
    echo "Backup file not found: ${backup_file}" >&2
    return 1
  fi

  local remote_tmp="/tmp/qlcplus-restore-$$.tar.gz"
  
  echo "Stopping ${SERVICE}..."
  run_sudo systemctl stop "${SERVICE}"
  
  echo "Uploading backup to Pi..."
  "${SCP_CMD[@]}" "$backup_file" "${PI_USER}@${PI_HOST}:${remote_tmp}"
  
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
  
  echo "Restarting ${SERVICE}..."
  run_sudo systemctl start "${SERVICE}"
  
  echo ""
  echo "Restore complete! Pre-restore backup saved on Pi at:"
  echo "  /tmp/qlcplus-pre-restore-backup.tar.gz"
  echo ""
  
  # Show status
  run_sudo systemctl status "${SERVICE}" --no-pager
}

# System update
function system_update() {
  run_sudo apt update
  run_sudo apt -y upgrade
}

# Export functions
export -f backup_create
export -f backup_restore
export -f system_update
