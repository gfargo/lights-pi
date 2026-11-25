# Troubleshooting

If the lights are doing something unsafe or unwanted, call `blackout` first, then diagnose.

## Contents

1. Triage in order
2. Scene changes time out / "Failed to apply scene" (QLC+ link freeze)
3. `/dev/ttyUSB0` is missing (usually normal)
4. Lights don't respond at all
5. QLC+ web UI on 9999 hangs or loads blank
6. Wi-Fi drops / Pi unreachable
7. Undervoltage and random USB drops
8. Can't reach the Pi / MCP errors

## 1. Triage in order

Work from the top of the stack down and stop at the first red:

1. **Control server health:** `curl http://<pi-host>:5000/healthz` (no login needed).
   200 = healthy, 503 = a critical check is red. Fields: `flask`, `qlc_ws`,
   `qlc_bridge_ok`, `qlc_consecutive_timeouts`, `qlc_loop_restarts`, `dmx_device`,
   `last_dmx_write_age_s`, `workspace_loaded`. It returns 503 when `qlc_ws`,
   `workspace_loaded` or `qlc_bridge_ok` is false.
2. **Via MCP:** `get_status` (QLC+ service, WebSocket), `get_system_info` (temperature, disk,
   USB, services, `power` flags), `get_logs("lighting-control")` / `get_logs("qlcplus-web")`.
3. **Via shell:** `./lightsctl.sh doctor` (full check with recommendations),
   `./lightsctl.sh health` (quick), `./lightsctl.sh logs-errors`, `./lightsctl.sh diagnose`.
4. **Physical:** `test_dmx()` or `./lightsctl.sh test-dmx`. If the sweep shows nothing, the
   problem is between the ENTTEC and the fixtures.

## 2. Scene changes time out / "Failed to apply scene ... via WebSocket"

The QLC+ link froze: every call times out even though `qlc_ws` may still read true.

- Check `/healthz`: `qlc_bridge_ok: false` and a climbing `qlc_consecutive_timeouts` confirm
  it. Since v2.16.2 a watchdog probes the link after 3 consecutive timeouts and replaces it
  if dead (`qlc_loop_restarted` in the log; `qlc_loop_restarts` counts them).
- If it does not recover on its own: on the Pi, `sudo systemctl restart lighting-control`
  (or `./lightsctl.sh control-restart`). QLC+ keeps the current look across this restart.
- If it recurs, check the install is on v2.16.2 or later, and collect
  `get_logs("lighting-control", 200)` for an issue.

## 3. `/dev/ttyUSB0` is missing (usually normal)

QLC+ talks to the ENTTEC through libftdi, which detaches the kernel `ftdi_sio` driver. So
while QLC+ holds the output, `/dev/ttyUSB0` (and the `/dev/dmx0` symlink) disappear. That
is expected and not a fault.

- Check the device is still enumerated instead: on the Pi, `lsusb -d 0403:6001` (the ENTTEC
  may show up as a generic FTDI FT232 serial chip), or `./lightsctl.sh lsusb`.
- It is a real problem only if the USB device itself is gone from `lsusb`, or drops randomly
  (see section 7).
- Permission problems: the Pi user should be in the `dialout` group (`groups $USER`); log out
  and back in after changing it.

## 4. Lights don't respond at all

- QLC+ Inputs/Outputs: universe output enabled, ENTTEC selected as the output plugin for the
  right universe.
- Fixture DMX addresses match their DIP switch or menu settings and the workspace.
- Wireless DMX: transmitter in **Transmit** mode, channels matching on both ends.
- Run `test_dmx` with a single group to isolate one fixture or one link.

## 5. QLC+ web UI on 9999 hangs or loads blank

- **Port open but nothing loads (spinning):** the QLC+ web server thread stalled.
  `./lightsctl.sh restart` restarts QLC+ and it recovers cleanly; the control server
  reconnects on the next request. If it keeps stalling, read `./lightsctl.sh logs-errors`.
- **Hangs right after start:** the `--operate` flag in `qlcplus-web.service` can make the web
  UI unresponsive; remove it (see README, "QLC+ web interface hangs").
- **Loads blank (no fixtures or scenes):** the workspace is probably QLC+ 5.x format; the Pi
  runs 4.14.x. Convert it (docs/CUSTOMIZATION.md, "QLC+ Workspace"). Also
  `~/.qlcplus/autostart.qxw` must be a regular file, not a symlink.
- **Qt platform errors in the logs:** `./lightsctl.sh qlc-headless`.

## 6. Wi-Fi drops / Pi unreachable

Wi-Fi is managed by **NetworkManager**. Use `nmcli` or lightsctl; **never** `wpa_cli` or
edits to `/etc/wpa_supplicant/wpa_supplicant.conf`. Those connect but do not persist, and can
leave NetworkManager with zero profiles, which the watchdog cannot recover from.

```bash
./lightsctl.sh wifi-test           # end-to-end check
./lightsctl.sh wifi-diagnose       # full diagnostics
./lightsctl.sh wifi-status
./lightsctl.sh wifi-add-network "<ssid>" "<passphrase>" 30   # priority last; higher wins
./lightsctl.sh wifi-reconnect
./lightsctl.sh wifi-watchdog-install                          # auto-recovery every 2 min
```

On the Pi: `sudo nmcli device disconnect wlan0 && sudo nmcli device connect wlan0`, or
`sudo systemctl restart NetworkManager`. Confirm saved profiles exist in
`/etc/NetworkManager/system-connections/`. Power save on the Pi 3 radio causes drops;
`docs/WIFI_RELIABILITY.md` has the fix. `iw` may not be installed; use `nmcli dev wifi list`.

## 7. Undervoltage and random USB drops

- `get_system_info` returns `power` decoded from `vcgencmd get_throttled`: `ok`, `raw`, and
  `issues` such as `undervoltage_now`, `throttled_now`, `undervoltage_since_boot`. On the Pi:
  `vcgencmd get_throttled` (`0x0` = clean).
- Since-boot bits mean a brownout happened earlier. Brownouts cause random Wi-Fi and USB
  (ENTTEC) drops on a Pi 3. Fix the supply: an official or known-good 5.1 V supply, short
  thick cable, a powered USB hub if needed.
- To reset a dropped FTDI device without rebooting, see README, "USB DMX device keeps
  disconnecting".

## 8. Can't reach the Pi / MCP errors

- `lights.local` not resolving: `./lightsctl.sh scan`, `./lightsctl.sh check`, or use the
  Tailscale name (`PI_HOST=<pi-name>.<tailnet>.ts.net`). See setup.md, section 6.
- MCP tools return `{success: false, ...}`: the control server is down or erroring. Check
  `/healthz` and `./lightsctl.sh control-status`. `401` on `/mcp` means `LIGHTS_PASSWORD` is
  set and the client is not sending the matching Bearer token.
- `lighting-mcp: not_installed` in health: run `./lightsctl.sh mcp-install`.
- MCP server misbehaving: `./lightsctl.sh mcp-logs`, then `./lightsctl.sh mcp-restart`
  (always safe).
- After a deploy the UI looks unchanged: hard-refresh the browser.
- Deploy fails waiting for `/healthz`: read `./lightsctl.sh control-logs`. A dependency that
  does not support the Pi's Python version is a common cause.
