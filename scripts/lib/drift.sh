#!/usr/bin/env bash
# Deploy drift detection for lightsctl.sh
set -euo pipefail

# Compares the repo's deploy set (control-server/, scripts/, lightsctl.sh —
# the exact set scripts/deploy.sh rsyncs) against what is actually on the Pi,
# by checksum over a single SSH session. Also flags systemd units installed
# on the Pi that no repo script knows about — hand-deployed services that
# would be lost if the SD card died.
#
# Exit code: 0 = in sync, 1 = drift found (usable as a CI/pre-deploy gate).

# Shared find predicate — mirror deploy.sh's rsync excludes on both ends.
_DRIFT_PRUNE="! -path '*/__pycache__/*' ! -name '*.pyc' ! -path '*/.git/*' ! -name '.env' ! -path '*/venv/*' ! -path '*/.venv/*'"

function _drift_local_hashes() {
  local hasher=(sha256sum)
  command -v sha256sum >/dev/null 2>&1 || hasher=(shasum -a 256)
  (
    cd "${SCRIPT_DIR}"
    eval "find control-server scripts -type f ${_DRIFT_PRUNE} -print0" \
      | xargs -0 "${hasher[@]}"
    "${hasher[@]}" lightsctl.sh
  ) | sort -k2
}

function _drift_remote_hashes() {
  run "cd /home/${PI_USER} && \
    { eval \"find control-server scripts -type f ${_DRIFT_PRUNE} -print0\" 2>/dev/null \
        | xargs -0 -r sha256sum; \
      if [ -f lightsctl.sh ]; then sha256sum lightsctl.sh; fi; }" | sort -k2
}

function deploy_drift() {
  echo "=== Deploy drift: repo ↔ ${PI_USER}@${PI_HOST} ==="
  echo ""

  local tmp_local tmp_remote
  tmp_local=$(mktemp)
  tmp_remote=$(mktemp)
  # shellcheck disable=SC2064
  trap "rm -f '${tmp_local}' '${tmp_remote}'" RETURN

  echo "--- Hashing local repo files ---"
  _drift_local_hashes > "${tmp_local}"
  echo "    $(wc -l < "${tmp_local}" | tr -d ' ') files"
  echo "--- Hashing deployed files on Pi ---"
  _drift_remote_hashes > "${tmp_remote}"
  echo "    $(wc -l < "${tmp_remote}" | tr -d ' ') files"
  echo ""

  local tmp_report drift_count
  tmp_report=$(mktemp)
  awk '
    NR==FNR { l[$2]=$1; next }
             { r[$2]=$1 }
    END {
      for (p in l) if (!(p in r))            print "  not deployed:   " p
      for (p in l) if (p in r && l[p]!=r[p]) print "  differs:        " p
      for (p in r) if (!(p in l))            print "  pi-only file:   " p " (pull back + commit, or delete on Pi)"
    }' "${tmp_local}" "${tmp_remote}" | sort > "${tmp_report}"
  drift_count=$(wc -l < "${tmp_report}" | tr -d ' ')
  if [[ ${drift_count} -eq 0 ]]; then
    echo "  ✓ deploy set matches the repo"
  else
    cat "${tmp_report}"
  fi
  rm -f "${tmp_report}"

  # Systemd units on the Pi vs. units any repo script knows how to install.
  echo ""
  echo "--- Systemd units on Pi vs repo-managed units ---"
  local known_units remote_units orphan_count=0 unit exec_line
  known_units=$(grep -rhoE '[A-Za-z0-9@_.-]+\.(service|timer)' \
    "${SCRIPT_DIR}/scripts/services" "${SCRIPT_DIR}/scripts/provisioning" \
    "${SCRIPT_DIR}/lightsctl.sh" "${SCRIPT_DIR}/scripts/lib" 2>/dev/null | sort -u)
  remote_units=$(run "find /etc/systemd/system -maxdepth 1 -type f \\( -name '*.service' -o -name '*.timer' \\) -printf '%f\\n'" | sort -u)
  for unit in ${remote_units}; do
    if ! grep -qxF "${unit}" <<<"${known_units}"; then
      echo "  pi-only unit:   ${unit} (no repo script installs this — pull it back)"
      exec_line=$(run "grep -h '^ExecStart=' /etc/systemd/system/${unit} 2>/dev/null" | head -1 || true)
      [[ -n "${exec_line}" ]] && echo "                  ${exec_line}"
      echo "                  → ./lightsctl.sh pull-file /etc/systemd/system/${unit}"
      orphan_count=$((orphan_count + 1))
    fi
  done
  [[ ${orphan_count} -eq 0 ]] && echo "  ✓ every unit on the Pi is repo-managed"

  echo ""
  local total=$((drift_count + orphan_count))
  if [[ ${total} -eq 0 ]]; then
    echo "--- Result: in sync ---"
    return 0
  fi
  echo "--- Result: ${total} item(s) out of sync ---"
  echo "    pull back:  ./lightsctl.sh pull-file <remote-path> [repo-dest]"
  echo "    push out:   bash scripts/deploy.sh"
  return 1
}

# Copy a file from the Pi back into the repo working tree so it can be
# reviewed and committed. Dest defaults to the path relative to the Pi's
# home directory (i.e. its natural place in the repo layout).
function deploy_pull_file() {
  local remote_path="${1:-}"
  local dest="${2:-}"
  if [[ -z "${remote_path}" ]]; then
    echo "Usage: lightsctl.sh pull-file <remote-path> [repo-relative-dest]"
    return 1
  fi
  if [[ -z "${dest}" ]]; then
    dest="${remote_path#/home/${PI_USER}/}"
    if [[ "${dest}" == /* ]]; then
      # System path (e.g. /etc/systemd/system/foo.service) — land it under
      # scripts/services/ where unit files live in this repo.
      dest="scripts/services/$(basename "${remote_path}")"
    fi
  fi
  mkdir -p "$(dirname "${SCRIPT_DIR}/${dest}")"
  "${SCP_CMD[@]}" "${PI_USER}@${PI_HOST}:${remote_path}" "${SCRIPT_DIR}/${dest}"
  echo "✓ pulled ${remote_path} → ${dest}"
  echo "  review with: git diff -- ${dest}  (or git status if new)"
}
