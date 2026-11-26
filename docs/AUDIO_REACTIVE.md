# Audio reactivity

A USB microphone on the Pi lets the rig follow the music. It detects the
tempo (BPM), individual hits (and whether each is a kick or a snare) and how
loud it is, then flashes the fixtures on hits, shifts their colour with the
loudness, or steps a chase in time with the beat. Everything runs on the Pi;
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

1. **Enable mic.** The BPM readout, the hit bar (with the kind of the last
   hit) and the loudness meter come alive.
2. **Calibrate** while the room is at its normal background level, before the
   music starts. It measures the background noise and sets the noise gate to
   three times that level, so talking and air-conditioning hum stop
   triggering hits. Or set the gate by hand with **Sensitivity**: lower
   reacts to quieter sounds, higher ignores more. Moving the slider while
   the mic is on takes effect straight away.
3. Choose what the lights do under **Effect**, then **Apply** (you can
   switch effects while the mic is on):
   - **Flash on hits** takes every fixture's brightness to full on each hit
     and fades it back to the look underneath. **React to** picks the hits:
     any, kick drum only, or snare (which also catches claps and hats).
     **Fade back** sets how long the fade takes; short suits fast music,
     long suits slow. If you change scene while it runs, the flash fades
     back to the new scene.
   - **Colour follows loudness** sets the colour fixtures from deep blue
     when quiet, through violet and red, to amber when loud, getting
     brighter as it goes. Warm/cool white fixtures follow the loudness with
     their brightness. The loudness adjusts itself to the room, so a quiet
     set and a loud one both use the whole range.
   - **Off** keeps the mic listening (for audio-tempo chases) without
     touching the lights.

   Disable puts back the look from before the effect started.
4. **Audio-tempo chase** picks one of your chases and steps it at the
   detected BPM instead of its own fixed speed. **Stop** returns it to
   normal.

Detected tempo is kept between 60 and 200 BPM and smoothed over the last few
seconds, so it takes a few bars to settle after the music changes.

## How it decides

- **Kick or snare.** At each hit the Pi looks at the last ~46 ms of sound.
  Mostly below 150 Hz is a kick; a large share above 2 kHz with little low
  end is a snare (claps and hi-hats land here too); anything else, such as
  a vocal or a chord, counts only for *Any hit*. A bass-heavy room mic can
  make snares read as kicks; a line feed tells them apart best.
- **Latency.** Effects render about 33 times a second on the same thread
  that talks to QLC+, so a hit reaches the lights within one frame (~30 ms)
  plus QLC+'s own output time.
- **Not built.** Splitting frequency bands across fixtures
  (`spectrum_split` in the original proposal).

## API

For agents, the MCP server has `get_audio_state`, `enable_audio_react`,
`disable_audio_react` and `set_audio_sensitivity` (see
[MCP_SERVER.md](MCP_SERVER.md)). Scripts can call the endpoints directly:

| Method | Path | Body |
|---|---|---|
| `GET` | `/api/audio` | Current state: `available`, `enabled`, `bpm`, `level` (0–1), `last_onset_kind`, `sensitivity`, `device`, and `react` (the running effect, or null) |
| `POST` | `/api/audio/enable` | All optional: `effect` (`"flash"`, `"amplitude"` or null), `react_to` (`"any"`, `"kick"`, `"snare"`), `decay_ms` (50–2000, default 250), `groups`, `device`, `sensitivity`. Calling it again changes the effect without restarting the mic. `react_to` alone means `flash` |
| `POST` | `/api/audio/disable` | Stops the effect (restoring the look underneath) and capture |
| `POST` | `/api/audio/sensitivity` | `{"sensitivity": 0.02}`; changes the gate without restarting |
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
| *Kick* never fires, or every hit is a kick | Room mic hearing mostly bass or mostly treble; try *Any hit*, or move the mic |
| Colour stays blue | Loudness meter near 0: the gate is above the music; lower **Sensitivity** |
| Tempo wanders or doubles | Room mic hearing too much; move it toward the speakers or use a line feed |
