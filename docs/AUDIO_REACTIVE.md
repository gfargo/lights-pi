# Audio reactivity

A USB microphone on the Pi lets the rig follow the music: it detects the
tempo (BPM) and individual hits (onsets), then either flashes the fixtures on
each hit or steps a chase in time with the beat. Everything runs on the Pi;
no audio leaves it.

## What you need

- **A USB microphone that needs no driver** (USB audio class compliant).
  Almost any cheap USB lavalier, desk or "conference" mic works, as does a
  USB audio interface with a mic or line input. A line feed from the mixing
  desk gives the steadiest tempo; a room mic picks up talking and footsteps.
- **The audio packages.** `scripts/provisioning/setup.sh` installs PortAudio
  and aubio, and `control-server/requirements.txt` pulls in `aubio`,
  `sounddevice` and `numpy`. On an install that predates them, run
  `bash scripts/deploy.sh`, which installs new Python dependencies.

## Set it up

1. Plug the mic into the Pi. On a Pi 3 use a short cable or plug it in
   directly; a mic on a long hub can add to the Pi's power problems.
2. Check the Pi sees it:

   ```bash
   ./lightsctl.sh ssh
   arecord -l          # the mic should be listed as a capture device
   ```

   If it is not listed, try another port or another mic; nothing in Lights
   Pi can fix a mic the kernel does not see.
3. Check the control server is ready:

   ```bash
   curl -s http://<pi-host>:5000/api/audio
   ```

   `"available": true` means the audio libraries loaded. `false` means the
   packages above are missing.

The engine listens to the system's default capture device. With more than
one input attached, pass the one you want when enabling it through the API
(`"device": "hw:1,0"`, the card and device numbers `arecord -l` prints).

## Use it

Open **Audio** in the control UI (under More on a phone).

1. **Enable mic.** The BPM readout and the onset bar come alive.
2. **Calibrate** while the room is at its normal background level, before the
   music starts. It measures the background noise and sets the noise gate to
   three times that level, so talking and air-conditioning hum stop
   triggering hits. Or set the gate by hand with **Sensitivity**: lower
   reacts to quieter sounds, higher ignores more.
3. Choose what the lights do:
   - **Onset flash** drives every fixture to full brightness on each
     detected hit. Set **React to** to *Off* to keep the mic listening
     without flashing.
   - **Audio-tempo chase** picks one of your chases and steps it at the
     detected BPM instead of its own fixed speed. **Stop** returns it to
     normal.

Detected tempo is kept between 60 and 200 BPM and smoothed over the last few
seconds, so it takes a few bars to settle after the music changes.

## Limits today

- *Kick* and *Snare* in **React to** currently behave like *Any onset*;
  telling the two apart is not built yet.
- The onset flash raises brightness to full and leaves it there; it does
  not fade back between hits. It suits music with steady hits more than
  sparse ones.
- Changing colour with loudness (amplitude mode) is planned but not built.
  Progress is tracked in
  [issue #12](https://github.com/gfargo/lights-pi/issues/12).

## API

For agents and scripts. The MCP server has no audio tools yet; call these endpoints directly:

| Method | Path | Body |
|---|---|---|
| `GET` | `/api/audio` | Current state: `available`, `enabled`, `bpm`, `sensitivity`, `device` |
| `POST` | `/api/audio/enable` | Optional `device`, `sensitivity`, `react_to` (`"any"`, or `null` for no flash) |
| `POST` | `/api/audio/disable` | Stops capture and any audio chases |
| `POST` | `/api/audio/calibrate` | Needs the engine enabled; returns the measured noise floor and new threshold |
| `POST` | `/api/audio/chase/<id>/start` | Steps chase `<id>` at the detected BPM |
| `POST` | `/api/audio/chase/<id>/stop` | Returns the chase to its own speed |
| `GET` | `/api/audio/chases` | Chases currently following the audio |

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `available: false` | aubio, sounddevice or PortAudio missing; redeploy or rerun provisioning |
| Enable fails, or BPM stays at 0 | No capture device (`arecord -l` is empty), or the gate is above the music; lower **Sensitivity** |
| Lights flash on talking or footsteps | Gate too low; **Calibrate** in a quiet moment, or raise **Sensitivity** |
| Tempo wanders or doubles | Room mic hearing too much; move it toward the speakers or use a line feed |
