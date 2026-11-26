# OSC: TouchOSC and QLab

The control server listens for OSC (Open Sound Control) on UDP port 8000. A
tablet running TouchOSC can then be a wireless control surface, and a QLab
show can fire scenes from its own cues. State goes back the other way too:
buttons in TouchOSC light up for the active scene, and the master fader
moves when something else changes the level.

## 1. Let OSC through the Pi's firewall

The Pi's firewall (ufw) only lets in what it has been told to. Installs from
before this guide don't allow OSC, so messages from the studio network are
silently dropped. Traffic over Tailscale may still get through, which can
make this confusing.

```bash
./lightsctl.sh ssh
sudo ufw allow 8000/udp comment 'OSC (TouchOSC, QLab)'
```

`./lightsctl.sh control-install` adds this rule on new installs.

## 2. Check it from the control UI

Open **Diagnostics** (under More on a phone) and find **OSC**. It shows:

- the address and port to point devices at, for example `192.168.1.20`, port `8000`;
- where feedback goes (see below), with **Send test**;
- the last ten messages that arrived, what each one did, and errors such as
  an unknown address;
- under **Addresses for this workspace**, the address of every scene and
  chase.

The list refreshes every few seconds while the tab is open. If you press a
button in TouchOSC and nothing appears, the message isn't reaching the Pi:
check the firewall, the IP address and that both devices are on the same
network.

## Addresses

| Address | Argument | Does |
|---|---|---|
| `/scene/<id or name>` | none, or 1 | Activates the scene |
| `/chase/<id or name>` | none or 1 starts, 0 stops | Starts or stops the chase |
| `/master` | 0.0–1.0 (or 0–255) | Sets every fixture's brightness |
| `/fixture/<id>/<channel>` | 0.0–1.0 (or 0–255) | Sets one channel; channels count from 0 |
| `/blackout` | none, or 1 | Blackout |
| `/cue/go`, `/cue/stop` | optional cue-list name | Next cue, or stop. Without a name, works when there is one cue list |

**Names.** OSC addresses can't contain spaces. Use the scene's number
(`/scene/6`), or its name in lower case with dashes for spaces and
punctuation: "Lights OFF" is `/scene/lights-off`, "White - Cool" is
`/scene/white-cool`. The Diagnostics panel lists both for every scene and
chase.

**Buttons.** TouchOSC buttons send 1 when pressed and 0 when released.
Scenes, blackout and cue buttons act on the press and ignore the release,
so one tap fires once. For chases, 0 means stop, so use a **toggle** button
for a chase: on starts it, off stops it. A momentary button would stop the
chase as soon as you let go.

Values between 0.0 and 1.0 are read as a fraction of full (TouchOSC's
default range). A value above 1 is taken as a raw DMX level, 0–255.

## Feedback

When the lights change from anywhere (the web UI, the AI chat, QLab), the Pi sends OSC back so TouchOSC controls show the real state:

| Sent | When |
|---|---|
| `/scene/<id>` and `/scene/<name>` with 1.0 | That scene was activated; the previous scene's addresses get 0.0 |
| `/chase/<id>` and `/chase/<name>` with 1.0 or 0.0 | A chase started or stopped |
| `/master` with 0.0–1.0 | The master level changed |
| `/state/scene-active`, `/state/chase-active` with the name | Same events, for scripts and labels |
| `/state/master` with 0–255 | Same, raw |

Because feedback uses the same addresses as the controls, a TouchOSC button
set to `/scene/6` lights up whenever scene 6 is active, with no scripting.

Where it goes is set in the **OSC** panel:

- **Back to the device that sent the last message** (the default): replies
  to whichever tablet or computer last sent something, on port 9000. This
  suits a tablet whose IP address changes; it starts working after the first
  button press.
- **To a fixed address**: always one host and port, for a computer that
  only listens.
- **Off**.

The port must match the port TouchOSC listens on (its "receive port",
9000 by default).

## TouchOSC

1. In TouchOSC, open the connections (the chain-link icon) and add an
   **OSC** connection, **UDP**:
   - **Host**: the Pi's address from the OSC panel
   - **Send port**: `8000`
   - **Receive port**: `9000`
2. Load a layout. The quickest is the one made for your workspace:
   **Diagnostics → OSC → Download layout (.tosc)**. It has:
   - **Scenes**: a button for every scene (more pages past 24), the
     master fader, cue **GO** / **STOP** and a red **BLACKOUT**;
   - **Faders**: eight faders on the first eight channels, labelled with
     fixture and channel, plus master and blackout;
   - **Chases** (when the workspace has any): a toggle per chase.

   It's laid out for an iPad in landscape (1024 × 768) and is generated
   from the workspace loaded when you download it, so download it again
   after adding scenes. Open the file in TouchOSC (AirDrop, Files, or the
   editor's **Open**). [`docs/touchosc/lights-pi.tosc`](touchosc/lights-pi.tosc)
   is the same layout built from the bundled sample workspace, if you want
   to look before connecting.

   To build your own instead, use these addresses: a **button** per scene
   (`/scene/1`, `/scene/lights-on`), a **fader** on `/master`, a **toggle
   button** per chase (`/chase/12`), a **button** on `/blackout`, and
   faders on single channels (`/fixture/0/0`, `/fixture/0/1`; fixture IDs
   come from `GET /api/fixtures`, channels count from 0).
3. Press a scene button. It should appear in the OSC panel's recent
   messages, the lights should change, and the button should stay lit.

From a computer you can also fetch the layout for any running server:

```bash
python control-server/touchosc_layout.py http://lights.local:5000 my-rig.tosc
```

## QLab

1. In **Workspace Settings → Network**, add a destination: type **OSC**,
   address the Pi's IP, port `8000`.
2. Add a **Network** cue, choose that destination, and set the message to
   an address from the table, such as `/scene/lights-on` or `/cue/go`.
   No arguments are needed.
3. Fire the cue and check the OSC panel's recent messages.

QLab doesn't need feedback; leave it pointed at the tablet, or **Off**.

## Settings

In the Pi's `~/control-server/.env`:

| Variable | Default | Notes |
|---|---|---|
| `OSC_ENABLED` | `true` | `false` turns the listener and feedback off |
| `OSC_LISTEN_PORT` | `8000` | Change the firewall rule to match |
| `OSC_LISTEN_HOST` | `0.0.0.0` | All interfaces |
| `OSC_OUT_PORT` | `9000` | Feedback port, until set in the panel |
| `OSC_OUT_HOST` | unset | When set, feedback starts as a fixed address |

The panel's feedback choice is saved in `~/.qlcplus/osc.json` and takes
precedence over `OSC_OUT_*`. Restart the service after editing `.env`:
`sudo systemctl restart lighting-control`.

## API

| Method | Path | Body |
|---|---|---|
| `GET` | `/api/osc` | Listener state, addresses, feedback settings, recent messages |
| `GET` | `/api/osc/addresses` | Address of every scene and chase |
| `POST` | `/api/osc/settings` | `{"mode": "sender" \| "fixed" \| "off", "host": "…", "port": 9000}` |
| `POST` | `/api/osc/test` | Sends `/state/test 1` to the feedback destination |

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Nothing appears under recent messages | Firewall (step 1), wrong IP, or the tablet is on a guest network that can't reach the Pi |
| "unknown address" in the list | Typo in the address, or a space in a name; use the number or the dashed name |
| Scene name not found | The scene was renamed or deleted; check **Addresses for this workspace** |
| Lights change but TouchOSC buttons don't light | Feedback is off, or TouchOSC's receive port isn't 9000; use **Send test** |
| A chase stops as soon as the button is released | Make it a toggle button |
| "not listening" in the panel | Port 8000 is taken by another program; set `OSC_LISTEN_PORT` |
