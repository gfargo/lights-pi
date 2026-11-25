# Operating the lights through MCP

Tool names and arguments below come from `mcp-server/server.py`. If a call fails with an
unknown tool or argument, the install may be older than this skill: list the server's tools
and trust that list.

## Contents

1. Start of session
2. Scenes vs live values
3. Tool catalog
4. Colour and Kelvin
5. Chases and tap tempo
6. Cue lists
7. Groups and workspaces
8. Lighting craft in brief

## 1. Start of session

1. `get_status`: AI provider, QLC+ service, active workspace, WebSocket state. If the
   WebSocket is down, go to troubleshooting.md before changing anything.
2. Read the `lights://workspace` resource if your client supports resources (status,
   fixtures, groups, scenes and templates in one read). Otherwise call `list_groups`,
   `list_fixtures` and `list_scenes`.
3. If there are no groups and the user wants per-zone control, propose groups
   (`create_group(name, fixtures, description)`) built from real fixture IDs. Use
   `identify_fixture(fixture_id)` to flash a fixture so the user can tell you which is which.

Most write tools accept an optional `groups` list. Omit it to target every fixture.

## 2. Scenes vs live values

- **Live values** are what the rig is outputting now. `adjust_*`, `color_temperature`,
  `palette`, `strobe`, `fade`, `set_channel` and `generate_scene` change live values only.
  `get_channel_values` reads them.
- **Scenes** are saved QLC+ functions in the workspace. `activate_scene` recalls one;
  `snapshot_scene(name)` saves the current live state as a new scene; `save_scene` stores
  scene XML (for example the XML returned by `generate_scene`).
- Live tweaks are lost the moment another scene is activated. If the user likes a look,
  snapshot it.
- To start from an existing look and change it, `duplicate_scene(scene, new_name)`, activate
  the copy, adjust, then snapshot.

## 3. Tool catalog

**Discovery (read-only):** `get_status`, `list_fixtures`, `get_fixture_channels(fixture_id)`,
`list_groups`, `list_scenes`, `list_templates`, `get_channel_values`, `describe_scene(scene)`.

**Live looks:**

| Tool | Arguments | Notes |
|---|---|---|
| `activate_scene` | `scene` (name or ID) | Recall a saved scene |
| `apply_template` | `template`, `groups` | Built-in: `youtube-studio`, `party`, `ambient`, `spotlight`, `work-light`, `warm-white`, `cool-white` (check `list_templates`) |
| `adjust_brightness` | `value`, `groups` | `0`-`255`, `"75%"`, `"+30"`, `"-20"` |
| `adjust_color` | `color`, `intensity`, `groups` | Presets such as red, warm, cool, amber, magenta, cyan, white |
| `color_temperature` | `kelvin`, `intensity`, `groups` | 1800-10000K, picks a per-fixture strategy (WWA, RGB, RGBW) |
| `palette` | `assignments` | `{group: value}`; value is a preset, a Kelvin number, `"5600K"`, or `{"kelvin": 3200, "intensity": "50%"}` |
| `strobe` | `rate`, `intensity`, `groups` | 0-20 Hz, `"off"` to stop. Warn people first |
| `fade` | `target`, `duration`, `groups` | Target 0-255, duration in seconds |
| `generate_scene` | `description`, `groups` | AI-built scene applied live; needs `AI_PROVIDER`/`AI_API_KEY` on the Pi |
| `set_channel` | `fixture_id`, `channel` (0-based), `value` | Raw DMX escape hatch; check `get_fixture_channels` first |
| `blackout` | `groups` | Zero every channel on the targets. The safe stop |
| `batch_action` | `actions`, `stop_on_error` | Several `{action, parameters, groups}` in one round trip |
| `identify_fixture` | `fixture_id`, `duration`, `pulses` | Flash one fixture to find it |

**Scenes:** `snapshot_scene(name, path)`, `save_scene(name, scene_xml, snapshot, path)`,
`describe_scene`, `rename_scene(scene, new_name, path)`, `duplicate_scene(scene, new_name)`,
`delete_scene`.

**Groups:** `create_group`, `update_group(name, new_name, description, fixtures)`,
`add_fixtures_to_group(name, ...)`, `remove_fixtures_from_group(name, ...)`, `delete_group`.

**Chases:** `list_chases`, `describe_chase`, `create_chase`, `start_chase`, `stop_chase`,
`delete_chase`.

**Cue lists:** `list_cue_lists`, `describe_cue_list`, `get_active_cue_lists`,
`create_cue_list`, `update_cue_list`, `go_cue_list`, `stop_cue_list`, `delete_cue_list`.

**Workspaces:** `list_workspaces`, `get_current_workspace`, `load_workspace(name)`,
`create_workspace(name, copy_from)`, `delete_workspace` (refuses the active one).

**Diagnostics:** `test_dmx(duration, groups)` (red, green, blue sweep, then restore),
`get_system_info` (CPU temperature, memory, disk, USB, services, power/undervoltage flags),
`get_logs(service, n)` where service is one of `qlcplus-web`, `lighting-control`,
`lighting-mcp`, `nginx`.

Blackout leaves the rig dark. To come back, `activate_scene` a known look or apply a
template; there is no separate "undo blackout".

## 4. Colour and Kelvin

Think in white balance first for anything with people or a camera in it; saturated colour is
for accents and backgrounds.

| Kelvin | Reads as |
|---|---|
| 1800-1900 | Candle, firelight |
| 2700 | Warm household bulb |
| 3200 | Tungsten, the classic studio key |
| 4000 | Cool-white fluorescent |
| 5600 | Daylight |
| 6500 | Neutral, slightly cool white |
| 7500 | Overcast, north window |

- Match the key and fill to the camera's white balance. Mixing 3200K and 5600K on one face
  looks like a mistake unless it is intended.
- `color_temperature` on RGB-only fixtures is an approximation; WWA and RGBW fixtures do it
  better. Say so if the result looks off on cheaper fixtures.
- Use `palette` to set several groups in one call (the "set the room" move).

## 5. Chases and tap tempo

A chase is an ordered list of saved scenes with per-step timing, played by QLC+'s chase
engine.

```
create_chase(name="Sunset", steps=[
  {"scene": "Daylight", "hold_ms": 3000},
  {"scene": "Warm Wash", "hold_ms": 5000},
  "Deep Amber"
], fade_in_ms=500, hold_ms=2000, fade_out_ms=500, run_order="Loop")
start_chase("Sunset")
```

- Steps must reference scenes that exist; build and snapshot the scenes first.
- `run_order`: `Loop` (default, runs until `stop_chase`), `SingleShot`, `PingPong`, `Random`.
  `direction`: `Forward` or `Backward`.
- Stopping a chase leaves fixtures on their current state; follow with a scene or blackout.
- **Tap tempo is not an MCP tool.** It lives in the web UI's Chases tab (tap button) and the
  control-server route `POST /api/chases/<chase_id>/tempo` with `{"bpm": 120}` (40-240 BPM),
  which rewrites step hold times. With MCP only, set the speed through `hold_ms`: one step
  per beat is `60000 / BPM` ms (120 BPM = 500 ms). Fast chases with big colour or intensity
  jumps count as flashing light; warn first.

## 6. Cue lists

A cue list is a timed cue stack (QLab-style). The user starts their audio elsewhere and
presses GO at the same moment; the server fires each cue at its timestamp.

```
create_cue_list(name="Intro", description="30s intro", cues=[
  {"at": "0:00", "scene": "Daylight"},
  {"at": "0:08", "chase": "Sunset"},
  {"at": "0:22", "action": "fade", "parameters": {"target": "0", "duration": "2"}},
  {"at": "0:30", "action": "blackout"}
])
go_cue_list("Intro")
```

- Timestamps: `at_ms` (integer) or `at` (`"0:32.500"`, `"32s"`, `"1:23:45"`).
- A cue fires a `scene`, a `chase`, or an `action` with `parameters`; optional `groups`.
- Scene and chase references are validated on create; a broken reference fails the whole
  create.
- `stop_cue_list` halts playback and leaves fixtures as they are.

## 7. Groups and workspaces

- Groups are named fixture subsets ("key-lights", "back-lights"). Name them by role, not by
  model, so lighting requests map onto them naturally.
- `update_group(..., fixtures=[...])` replaces the list; use `add_fixtures_to_group` /
  `remove_fixtures_from_group` for incremental edits.
- Each workspace is a separate `.qxw` with its own fixtures, scenes, groups and last look.
  `load_workspace` switches the whole rig; confirm first. `create_workspace(name,
  copy_from=...)` is the safe way to start a new production from an existing one.

## 8. Lighting craft in brief

- **Three-point lighting:** key (main light, about 45 degrees off camera axis, brightest),
  fill (opposite side, softer, about half the key's level or lower), back/rim (behind the
  subject, separates them from the background). A common move:
  `palette({"key-lights": 3200, "fill-lights": {"kelvin": 3200, "intensity": "50%"},
  "back-lights": "magenta"})`, using the group names that actually exist.
- **Interview / video call:** soft, even, one colour temperature; avoid saturated colour on
  faces. `youtube-studio` is a reasonable starting template.
- **Mood:** lower intensity and warmer Kelvin for calm; colour on the background and back
  lights rather than faces.
- **Change in steps.** Make one adjustment, describe what changed, let the user react.
  Nudge with relative values (`"+20"`) rather than jumping.
- **Flashing light:** strobes and fast chases can trigger photosensitive seizures and are
  unpleasant without warning. Ask first, keep it short, turn it off.
