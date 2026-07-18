#!/usr/bin/env python3
"""DMX output + system health monitor.

Polls the local control-server continuously and logs anything that
changes. Runs as a systemd service; stdout is captured by journald, so
history is queryable with `journalctl -u dmx-monitor`.

  - Every 1s:  fetch /api/channel_values, diff against the previous
               snapshot, log only the channels that changed.
  - Every 15s: fetch /api/diagnostics/system, log a one-line health
               snapshot (CPU temp, load, memory, USB DMX device count,
               service states) so a flicker/incident can be correlated
               against system load or a dropped USB device.

Deliberately stdlib-only (urllib/json/time) so it needs no venv.
"""
import json
import time
import urllib.error
import urllib.request

BASE = "http://localhost:5000"
POLL_INTERVAL_SEC = 1.0
SYSTEM_SNAPSHOT_EVERY = 15  # iterations (~15s at POLL_INTERVAL_SEC=1)
REQUEST_TIMEOUT_SEC = 4


def fetch(path):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=REQUEST_TIMEOUT_SEC) as r:
        return json.loads(r.read())


def log(msg):
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}", flush=True)


def format_changes(old, new):
    changed = {ch: (old.get(ch), v) for ch, v in new.items() if old.get(ch) != v}
    ordered = sorted(changed.items(), key=lambda kv: int(kv[0]))
    return ", ".join(f"ch{ch}: {o}->{n}" for ch, (o, n) in ordered)


def format_power(power):
    """Compact power-flag summary from /api/diagnostics/system's `power`."""
    if not power:
        return "power=?"
    if power.get("ok"):
        return f"power={power.get('raw', '0x0')}"
    return f"power={power.get('raw')}({','.join(power.get('issues', []))})"


def main():
    last_values = None
    poll_failures = 0
    empty_streak = 0
    iteration = 0

    log("dmx-monitor started")

    while True:
        iteration += 1

        try:
            values = fetch("/api/channel_values").get("values", {})
            if not values and last_values:
                # A transient empty read (QLC+ WS timeout inside the
                # control-server) is not a real all-channels change — keep
                # the previous snapshot so the next good read diffs against
                # actual prior state instead of logging ch1: None->…
                empty_streak += 1
                log(f"POLL_EMPTY channel_values returned no data consecutive={empty_streak}")
            else:
                if empty_streak:
                    log(f"POLL_EMPTY_RECOVERED after {empty_streak} empty read(s)")
                empty_streak = 0
                if last_values is not None:
                    diff = format_changes(last_values, values)
                    if diff:
                        log(f"DMX_CHANGE {diff}")
                last_values = values
            if poll_failures:
                log(f"POLL_RECOVERED after {poll_failures} failed attempt(s)")
            poll_failures = 0
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            poll_failures += 1
            log(f"POLL_FAIL channel_values consecutive={poll_failures} error={e}")

        if iteration % SYSTEM_SNAPSHOT_EVERY == 0:
            try:
                sysinfo = fetch("/api/diagnostics/system")
                usb = sysinfo.get("usb") or {}
                log(
                    "SYSTEM "
                    f"cpu_temp_c={sysinfo.get('cpu_temp_c')} "
                    f"load1m={sysinfo.get('load_avg', {}).get('1m')} "
                    f"mem_used_pct={sysinfo.get('memory', {}).get('used_pct')} "
                    f"usb_total={usb.get('all_count')} "
                    f"usb_dmx={len(usb.get('dmx_related') or [])} "
                    f"{format_power(sysinfo.get('power'))} "
                    f"services={sysinfo.get('services')}"
                )
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
                log(f"POLL_FAIL diagnostics/system error={e}")

        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main()
