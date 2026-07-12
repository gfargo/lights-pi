#!/usr/bin/env bash
# lighting-backup.sh — on-Pi backup script run by lighting-backup.timer
# Creates a daily snapshot of QLC+ + control-server config, prunes old
# snapshots (7 daily / 4 weekly / 6 monthly), and optionally pushes to a
# remote (s3://, user@host:/path, or rclone:remote/path).
set -euo pipefail

LOG_TAG="lighting-backup"
BACKUP_DIR="${BACKUP_DIR:-${HOME}/lights-pi-backups}"
BACKUP_REMOTE="${BACKUP_REMOTE:-}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"

log() { logger -t "$LOG_TAG" "$*"; echo "$(date '+%Y-%m-%d %H:%M:%S') [$LOG_TAG] $*" >&2; }

# ---------------------------------------------------------------------------
# create_snapshot — tar the QLC+ dirs + control-server config into BACKUP_DIR
# ---------------------------------------------------------------------------
create_snapshot() {
  local date_str
  date_str="$(date +%F)"
  local snapshot="${BACKUP_DIR}/lights-pi-backup-${date_str}.tar.gz"

  mkdir -p "$BACKUP_DIR"

  # Collect directories that exist
  local dirs=()
  for d in ".config/qlcplus" ".qlcplus"; do
    [[ -e "${HOME}/${d}" ]] && dirs+=("${d}")
  done
  # Control-server config (scenes / groups / cue lists live here)
  # NOTE: .env is intentionally excluded — it contains production secrets
  # (ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.) that must not leave the Pi.
  for d in "control-server"; do
    [[ -e "${HOME}/${d}" ]] && dirs+=("${d}")
  done

  if [[ ${#dirs[@]} -eq 0 ]]; then
    log "WARN: nothing to back up under ${HOME}"
    return 0
  fi

  log "INFO: creating snapshot ${snapshot}"
  tar -czf "${snapshot}" -C "${HOME}" "${dirs[@]}"
  log "INFO: snapshot created ($(du -sh "${snapshot}" | cut -f1))"
  echo "${snapshot}"
}

# ---------------------------------------------------------------------------
# prune_retention — keep last N daily, 4 weekly (Sun), 6 monthly (1st)
# ---------------------------------------------------------------------------
prune_retention() {
  local daily_keep="${BACKUP_RETENTION_DAYS}"
  local weekly_keep=4
  local monthly_keep=6

  # List snapshots oldest-first
  local -a snapshots
  mapfile -t snapshots < <(ls -1 "${BACKUP_DIR}"/lights-pi-backup-*.tar.gz 2>/dev/null | sort)
  local total=${#snapshots[@]}
  local max_keep=$(( daily_keep + weekly_keep + monthly_keep ))
  [[ $total -le $max_keep ]] && return 0

  local -a keep=()

  # Always keep the most recent N daily
  for (( i = total - 1; i >= 0 && ${#keep[@]} < daily_keep; i-- )); do
    keep+=("${snapshots[$i]}")
  done

  # Weekly: keep one per week (Sunday or oldest that week), up to weekly_keep
  local weekly_count=0
  for f in "${snapshots[@]}"; do
    local fname
    fname="$(basename "$f")"
    local snap_date="${fname#lights-pi-backup-}"
    snap_date="${snap_date%.tar.gz}"
    local dow
    dow="$(date -d "$snap_date" +%u 2>/dev/null || date -j -f '%Y-%m-%d' "$snap_date" '+%u' 2>/dev/null || echo 0)"
    if [[ "$dow" == "7" ]] && [[ $weekly_count -lt $weekly_keep ]]; then
      keep+=("$f")
      (( weekly_count++ )) || true
    fi
  done

  # Monthly: keep one per month (day == 01), up to monthly_keep
  local monthly_count=0
  for f in "${snapshots[@]}"; do
    local fname
    fname="$(basename "$f")"
    local snap_date="${fname#lights-pi-backup-}"
    snap_date="${snap_date%.tar.gz}"
    local dom="${snap_date##*-}"
    if [[ "$dom" == "01" ]] && [[ $monthly_count -lt $monthly_keep ]]; then
      keep+=("$f")
      (( monthly_count++ )) || true
    fi
  done

  # Delete anything not in keep list
  for f in "${snapshots[@]}"; do
    local should_keep=false
    for k in "${keep[@]}"; do
      [[ "$f" == "$k" ]] && should_keep=true && break
    done
    if [[ "$should_keep" == false ]]; then
      log "INFO: pruning ${f}"
      rm -f "$f"
    fi
  done
}

# ---------------------------------------------------------------------------
# push_remote — push a local snapshot file to the configured remote
# ---------------------------------------------------------------------------
push_remote() {
  local snapshot="$1"
  [[ -z "$BACKUP_REMOTE" ]] && return 0

  log "INFO: pushing $(basename "${snapshot}") to ${BACKUP_REMOTE}"

  if [[ "$BACKUP_REMOTE" == s3://* ]]; then
    if ! command -v aws >/dev/null 2>&1; then
      log "ERROR: 'aws' CLI not found — install awscli on the Pi to use S3 remote"
      return 1
    fi
    aws s3 cp "${snapshot}" "${BACKUP_REMOTE%/}/$(basename "${snapshot}")"

  elif [[ "$BACKUP_REMOTE" == rclone:* ]]; then
    if ! command -v rclone >/dev/null 2>&1; then
      log "ERROR: 'rclone' not found — install rclone on the Pi to use rclone remote"
      return 1
    fi
    local remote_path="${BACKUP_REMOTE#rclone:}"
    rclone copy "${snapshot}" "${remote_path}"

  else
    # scp-style: user@host:/path
    if ! command -v scp >/dev/null 2>&1; then
      log "ERROR: 'scp' not found"
      return 1
    fi
    scp "${snapshot}" "${BACKUP_REMOTE%/}/$(basename "${snapshot}")"
  fi

  log "INFO: remote push complete"
}

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
main() {
  log "INFO: starting backup"

  local snapshot
  snapshot="$(create_snapshot)"

  [[ -n "$snapshot" ]] && prune_retention
  [[ -n "$snapshot" ]] && push_remote "$snapshot"

  log "INFO: backup complete"
}

main "$@"
