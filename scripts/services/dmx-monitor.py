#!/usr/bin/env python3
"""DMX output + system health monitor.

Polls the local control-server continuously and logs anything that
changes. Runs as a systemd service; stdout is captured by journald, so
history is queryable with `journalctl -u dmx-monitor`.

  - Every 1s:  fetch /api/channel_values, diff against the previous
               snapshot, log only the channels that changed.
  - Every 1s:  read the Wi-Fi interface's operstate and log WIFI_LINK
               when it goes down or comes back, so a network drop gets
               a timestamp to the second.
  - Every 15s: fetch /api/diagnostics/system, log a one-line health
               snapshot (CPU temp, load, memory, USB DMX device count,
               service states, Wi-Fi signal) so a flicker/incident can
               be correlated against system load, a dropped USB device,
               or a weak or busy Wi-Fi link.

Deliberately stdlib-only (urllib/json/time) so it needs no venv.
"""
import json
import subprocess
import time
import urllib.error
import urllib.request

BASE = "http://localhost:5000"
POLL_INTERVAL_SEC = 1.0
SYSTEM_SNAPSHOT_EVERY = 15  # iterations (~15s at POLL_INTERVAL_SEC=1)
REQUEST_TIMEOUT_SEC = 4
WIFI_IFACE = "wlan0"
IW_BIN = "/usr/sbin/iw"  # not on PATH for a non-root service user


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


def parse_proc_wireless(text, iface=WIFI_IFACE):
    """Pull one interface's row out of /proc/net/wireless.

    Returns {link, signal_dbm, retry, missed_beacon} or None when the
    interface has no row (radio off / not associated). `retry` and
    `missed_beacon` are counters since the interface came up; a jump
    between two snapshots means the link was struggling in between.
    """
    for line in text.splitlines():
        name, sep, rest = line.partition(":")
        if not sep or name.strip() != iface:
            continue
        cols = rest.split()
        try:
            return {
                "link": int(float(cols[1])),
                "signal_dbm": int(float(cols[2])),
                "retry": int(cols[7]),
                "missed_beacon": int(cols[9]),
            }
        except (IndexError, ValueError):
            return None
    return None


def parse_iw_link(text):
    """Pull AP address, frequency and tx bitrate out of `iw dev X link`."""
    info = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Connected to "):
            info["bssid"] = line.split()[2]
        elif line.startswith("freq:"):
            info["freq_mhz"] = int(float(line.split()[1]))
        elif line.startswith("tx bitrate:"):
            info["tx_mbit"] = float(line.split()[2])
    return info


def format_wifi(stats, link=None):
    """Compact Wi-Fi summary for the SYSTEM line."""
    if not stats:
        return "wifi=down"
    link = link or {}
    return (
        f"wifi_dbm={stats['signal_dbm']} wifi_link={stats['link']}/70 "
        f"wifi_retry={stats['retry']} wifi_missed_beacon={stats['missed_beacon']} "
        f"wifi_tx_mbit={link.get('tx_mbit', '?')} wifi_freq={link.get('freq_mhz', '?')} "
        f"wifi_ap={link.get('bssid', '?')}"
    )


def read_text(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def read_iw_link():
    try:
        out = subprocess.run(
            [IW_BIN, "dev", WIFI_IFACE, "link"],
            capture_output=True, text=True, timeout=REQUEST_TIMEOUT_SEC,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    return parse_iw_link(out)


def main():
    last_values = None
    last_operstate = None
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
                    was_lit = any(last_values.values())
                    is_lit = any(values.values())
                    if was_lit and not is_lit and values:
                        log("BLACKOUT all channels dropped to 0")
                    elif not was_lit and is_lit and last_values:
                        log("LIT_AGAIN output no longer all-zero")
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

        operstate = read_text(f"/sys/class/net/{WIFI_IFACE}/operstate").strip() or "absent"
        if last_operstate is not None and operstate != last_operstate:
            detail = ""
            if operstate == "up":
                link = read_iw_link()
                detail = f" ap={link.get('bssid', '?')} freq={link.get('freq_mhz', '?')}"
            log(f"WIFI_LINK {last_operstate}->{operstate}{detail}")
        last_operstate = operstate

        if iteration % SYSTEM_SNAPSHOT_EVERY == 0:
            wifi = format_wifi(parse_proc_wireless(read_text("/proc/net/wireless")), read_iw_link())
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
                    f"{wifi} "
                    f"services={sysinfo.get('services')}"
                )
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
                log(f"POLL_FAIL diagnostics/system error={e} {wifi}")

        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main()
