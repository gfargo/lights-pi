# AI scene generation (CLI)

`lightsctl.sh generate-scene` turns a plain-language description into a QLC+
scene for the fixtures in a workspace. It runs on your workstation, calls the
configured AI provider, and can inject the result into the Pi's workspace.

Most day-to-day use now happens in the web UI instead: the **Scenes** tab
takes a one-line request ("make it warmer"), and the **Chat** tab runs a
tool-using agent that can build, save and recall scenes. See
[CONTROL_SERVER_ARCHITECTURE.md](CONTROL_SERVER_ARCHITECTURE.md). The CLI is
still useful for scripted or offline scene building.

## Setup

Add the provider to your workstation `.env`:

```bash
AI_PROVIDER=anthropic        # anthropic | openai | ollama
AI_API_KEY=...               # not needed for ollama
AI_MODEL=...                 # model name for that provider
AI_SCENE_STYLE=complete      # default --style
AI_SCENE_VARIATIONS=1        # default --variations
```

Without an API key (and with a provider other than `ollama`), the command
falls back to the mock generator. `--mock` forces it.

## Usage

```bash
# Preview against a local workspace file
./lightsctl.sh generate-scene "warm sunset ambiance" --workspace studio.qxw --preview

# Save the XML
./lightsctl.sh generate-scene "party mode" --workspace studio.qxw --output scenes/party.xml

# Pull the live workspace from the Pi, add the scene, deploy it back
./lightsctl.sh generate-scene "dramatic spotlight" --add-to-workspace

# Generate three and pick one interactively
./lightsctl.sh generate-scene "cool blue ambient" --workspace studio.qxw --variations 3
```

| Option | Meaning |
|---|---|
| `--style <complete\|modular\|timeline\|reactive>` | Scene style (default `complete`) |
| `--preview` | Print the XML, deploy nothing |
| `--add-to-workspace` | Pull the Pi's workspace, inject the scene, deploy it |
| `--output <file>` | Write the XML to a file |
| `--variations <n>` | Generate n candidates and choose one |
| `--mock` | Use the offline mock generator |
| `--workspace <file>` | Workspace to read fixtures from |

Pass `--workspace` or `--add-to-workspace`; without either the command only
looks for one hard-coded workspace filename in the current directory.

### Styles

- **complete**: one self-contained scene that sets every relevant channel.
  This is the style that is fully supported with real providers.
- **modular**: separate colour/intensity layers meant to be combined.
- **timeline** and **reactive**: currently produced only by the mock
  generator as starting points. For timed sequences use chases and cue lists
  in the web UI; for audio-driven looks use the **Audio** tab.

### Templates

```bash
./lightsctl.sh list-templates
./lightsctl.sh generate-from-template <name> --preview
```

`generate-from-template` accepts `--preview`, `--add-to-workspace`,
`--output` and `--workspace`.

### Fixture groups

`./lightsctl.sh group-scene <group> "<description>"` limits generation to one
fixture group. See the fixture groups section of the [README](../README.md).

## How it works

1. `ai_extract_fixtures` (via `scripts/lib/extract_fixtures.py`) reads the
   workspace and the `.qxf` definitions to get each fixture's channels and
   roles.
2. `scripts/lib/ai_scene.sh` builds a system prompt with that channel map and
   calls Anthropic, OpenAI or Ollama.
3. The response is cleaned and validated as XML (fixture IDs, channel
   numbers, 0–255 values).
4. `scripts/lib/workspace.sh` injects it into the `.qxw`; with
   `--add-to-workspace` the workspace is deployed to the Pi.

Reference output lives in [`scenes/examples/`](../scenes/examples/).
