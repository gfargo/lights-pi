# TouchOSC layout

`lights-pi.tosc` is a TouchOSC (Mk2) layout for an iPad in landscape:
scene buttons, eight channel faders, master, cue GO / STOP and
blackout. It was generated from the bundled sample workspace
(`control-server/tests/fixtures/sample.qxw`), so its scene buttons are the
sample's; it has no Chases page because the sample's chases are test data.

For a layout with your own scenes and fixtures, download it from the
control UI (**Diagnostics → OSC → Download layout**), or run:

```bash
python control-server/touchosc_layout.py http://lights.local:5000 my-rig.tosc
```

Setup, addresses and feedback: [../OSC.md](../OSC.md). The generator is
`control-server/touchosc_layout.py`.
