# QLC+ WebSocket API Reference

Notes on the QLC+ 4.x WebSocket API as discovered through live probing.
Useful for debugging or extending the control server.

## Connection

```
ws://<host>:9999/qlcplusWS
```

QLC+ must be started with a workspace loaded, otherwise the API responds but
all channel values are zero and no fixtures are registered:

```bash
# Correct — loads workspace on start
qlcplus --nogui --web --web-port 9999 --open ~/.qlcplus/default.qxw

# Wrong — starts blank, no fixtures
qlcplus --nogui --web --web-port 9999
```

---

## Getting Channel Values

### Request

```
QLC+API|getChannelsValues|<universe>|<start_ch>|<count>
```

- `universe` — **1-based** universe index (universe 0 in workspace = `1` here)
- `start_ch` — **1-based** starting channel number
- `count` — number of channels to return

Example — fetch first 32 channels of universe 1:
```
QLC+API|getChannelsValues|1|1|32
```

### Response

```
QLC+API|getChannelsValues|<universe>|<ch>|<value>|<pct.color>|<ch>|<value>|<pct.color>|...
```

Pipe-delimited, repeating groups of **3 fields** starting at index 3:
- `ch` — 1-based channel number
- `value` — DMX value 0–255
- `pct.color` — percentage and color hint (e.g. `0.#FF0000`), may be empty when no scene is active

Example response with an active scene:
```
QLC+API|getChannelsValues|1|1|0|0.#000000|2|0|0.#FF0000|3|0|0.#00FF00|4|241|0.#0000FF|...
```

Example response with no scene running (all zeros, empty color):
```
QLC+API|getChannelsValues|1|1|0||2|0||3|0||4|0||...
```

### Parsing (Python)

```python
parts = msg.split("|")
# parts[0] = "QLC+API", parts[1] = "getChannelsValues", parts[2] = universe
i = 3
while i + 1 < len(parts):
    ch = int(parts[i])       # 1-based channel number
    val = int(parts[i + 1])  # DMX value 0-255
    # parts[i + 2] is pct.color, skip it
    values[ch] = val
    i += 3
```

### Parsing (JavaScript)

```js
const parts = msg.split('|');
const values = {};
for (let i = 3; i + 1 < parts.length; i += 3) {
    const ch = parseInt(parts[i], 10);
    const val = parseInt(parts[i + 1], 10);
    if (!isNaN(ch) && !isNaN(val)) values[ch] = val;
}
```

---

## Setting a Channel Value

### Request

```
CH|<channel>|<value>
```

- `channel` — **1-based** channel number within the current universe page
- `value` — DMX value 0–255

Example — set channel 4 to 200:
```
CH|4|200
```

### Live Push

QLC+ also pushes `CH|` messages to all connected clients when a channel changes:
```
CH|<channel>|<value>
```

---

## Starting / Stopping a Scene

```
QLC+API|setFunctionStatus|<function_id>|<status>
```

- `function_id` — the numeric ID of the function/scene from the workspace XML
- `status` — `1` to start, `0` to stop

Example — start scene with ID 42:
```
QLC+API|setFunctionStatus|42|1
```

---

## Address Mapping

QLC+ workspace XML stores fixture addresses as **0-based**. The WebSocket API
uses **1-based** channel numbers. The mapping is:

```
qlc_channel = fixture.address + channel_offset + 1
```

Where `channel_offset` is 0-based (0 = first channel of the fixture).

Example with fixtures from `default.qxw`:

| Fixture              | Workspace address | Channels | QLC+ channels |
|----------------------|-------------------|----------|---------------|
| SlimPAR Pro H USB [1]| 0 (0-based)       | 7        | 1–7           |
| SlimPAR 56 [4]       | 7                 | 3        | 8–10          |
| SlimPAR 56 [5]       | 10                | 3        | 11–13         |
| SlimPAR Pro H USB [6]| 13                | 7        | 14–20         |

---

## Commands That Do NOT Exist

These were tested and return no response:

- `QLC+API|getChannelValues` (singular — wrong name)
- `getChannelsValues|1|1|32` (missing `QLC+API|` prefix)
- `QLC+API|getChannelsValues|0|1|32` (universe 0 — must be 1-based)
- `QLC+API|getChannelsValues|1|0|32` (start channel 0 — must be 1-based)

---

## Debugging

A probe script is available at `scripts/debug/probe_qlc_ws.py`. Copy it to the
Pi and run it to inspect raw WS responses:

```bash
scp scripts/debug/probe_qlc_ws.py riversway@lights.local:/tmp/
ssh riversway@lights.local "python3 /tmp/probe_qlc_ws.py"
```

---

## Common Issues

### SimpleDesk shows all zeros after reboot

QLC+ was started without a workspace. Check the systemd service:

```bash
systemctl cat qlcplus-web.service | grep ExecStart
```

The `--open` flag must point to the workspace file:

```
ExecStart=/usr/bin/qlcplus --nogui --web --web-port 9999 --open /home/riversway/.qlcplus/default.qxw
```

Fix and reload:

```bash
sudo sed -i 's|--web-port 9999$|--web-port 9999 --open /home/riversway/.qlcplus/default.qxw|' \
  /etc/systemd/system/qlcplus-web.service
sudo systemctl daemon-reload
sudo systemctl restart qlcplus-web.service
```

### Virtual Console shows all zeros despite active scene

The scene was injected into the workspace XML but not activated. After inject +
restart, the control server calls `QLC+API|setFunctionStatus|<id>|1` to start
the scene. If QLC+ hasn't finished booting yet the activation is retried up to
6 times with a 2-second delay between attempts.

### Channel values don't update after AI command

The scene is activated via WebSocket after restart. The Virtual Console polls
`/api/channel_values` on load and subscribes to live `CH|` pushes via the
browser WebSocket connection to `ws://lights.local:9999/qlcplusWS`.
