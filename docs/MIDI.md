# MIDI controllers

Plug a USB MIDI controller (an Akai MIDI Mix, a Korg nanoKONTROL2, a pad
controller or a keyboard) into the Pi and its faders and buttons can drive
the rig: a fader on a fixture's dimmer or on the master, a button on a scene,
a chase or blackout.

## 1. Plug it in

Any class-compliant USB MIDI device works; nothing to install. The Pi picks
it up within a couple of seconds, and again after you unplug and replug it.

Open the **MIDI** tab (under More on a phone). **Controllers** lists what is
connected. If it says MIDI is off, python-rtmidi isn't installed on the Pi:
run `bash scripts/deploy.sh` from your Mac, which installs the control
server's requirements.

## 2. Bind a control

1. Press **Learn**.
2. Move a fader or knob, or press a button or pad, on the controller.
3. Choose what it does, optionally give it a name, and **Save**.

| Control | Can do |
|---|---|
| Fader or knob (sends CC) | **A fixture channel**: any channel of any fixture, labelled from its profile. Under **Range** you can limit it, for example 0–180 so a fader never goes to full. **Master dimmer**: every fixture's brightness. |
| Button or pad (sends notes) | **Recall a scene.** **Start / stop a chase**: one press starts it, the next stops it. **Blackout.** |

A binding listens to the MIDI channel the control used when you learned it.
Each binding shows the control's live value, and **Edit** changes what it
does.

**Controls you've moved** lists everything the controllers have sent since
the server started, with live values, so you can see which number a fader
sends before binding it. Anything not bound yet has a **Bind** button.

## 3. What gets saved

Bindings are stored on the Pi in `~/.qlcplus/midi_mappings.json` and survive
restarts and deploys. They aren't tied to one controller: a binding for CC 21
on channel 1 answers any controller that sends CC 21 on channel 1.

## Trying it without a controller

On a Mac, a script can stand in for a controller: python-rtmidi can open a
virtual MIDI port, which the control server running on the same Mac sees as
a plugged-in device.

```python
import time, rtmidi
out = rtmidi.MidiOut()
out.open_virtual_port("Test controller")
time.sleep(3)                                  # let the server find it
for v in range(128):                           # move CC 21 from bottom to top
    out.send_message([0xB0, 21, v]); time.sleep(0.005)
out.send_message([0x90, 36, 100])              # press note 36
```

Run the control server locally with `MOCK_DMX=1` (no rig needed), open the
MIDI tab, press **Learn** and run the script.

## API

| Method | Path | |
|---|---|---|
| GET | `/api/midi/devices` | Connected input ports (probes rtmidi) |
| GET | `/api/midi/monitor?after=<seq>` | What the MIDI tab polls: open ports, messages newer than `seq`, latest value per control |
| GET | `/api/midi/mappings` | All bindings |
| POST | `/api/midi/mappings` | Add one: `{"name", "input": {"type": "cc"\|"note", "channel": 0-15\|null, "number": 0-127}, "action": {...}}` |
| PATCH / DELETE | `/api/midi/mappings/<id>` | Change or remove one |
| GET | `/api/midi/state` | Last raw value per binding |

Actions: `{"type": "channel", "fixture_id", "channel_offset", "out_min", "out_max"}`,
`{"type": "master"}`, `{"type": "scene", "scene_id"}`,
`{"type": "chase_toggle", "chase_id"}`, `{"type": "blackout"}`. Faders take
`channel` and `master`; buttons take the rest.
