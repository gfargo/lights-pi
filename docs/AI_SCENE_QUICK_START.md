# AI Scene Generation - Quick Start

Get started with AI-powered scene generation in 5 minutes.

## Setup

1. Add your AI API key to `.env`:

```bash
# Copy example if you haven't already
cp .env.example .env

# Edit .env and add:
AI_PROVIDER=anthropic
AI_API_KEY=sk-ant-your-key-here
AI_MODEL=claude-3-5-sonnet-20241022
```

2. Verify configuration:

```bash
./lightsctl.sh generate-scene "test" --preview
```

## Basic Usage

### Generate and Preview

```bash
./lightsctl.sh generate-scene "warm sunset ambiance" --preview
```

This will:
- Pull your current workspace from the Pi
- Analyze your fixture inventory
- Generate appropriate DMX values
- Display the scene XML

### Save to File

```bash
./lightsctl.sh generate-scene "party mode" --output scenes/party.xml
```

### Add to Workspace

```bash
./lightsctl.sh generate-scene "dramatic spotlight" --add-to-workspace
```

This will inject the scene into your workspace and deploy it to the Pi.

## Style Profiles

### Complete (Default)

Self-contained scenes ready to use immediately:

```bash
./lightsctl.sh generate-scene "cool blue ambient" --style complete
```

### Modular

Separate layers you can combine:

```bash
./lightsctl.sh generate-scene "warm orange" --style modular
```

Generates multiple scenes:
- Color: Warm Orange
- Intensity: Medium Glow
- Position: Front Wash (if moving heads)

### Timeline

Time-based sequences:

```bash
./lightsctl.sh generate-scene "sunrise over 3 minutes" --style timeline
```

### Reactive

Audio/sensor responsive:

```bash
./lightsctl.sh generate-scene "bass-reactive pulse" --style reactive
```

## Common Descriptions

Try these natural language descriptions:

**Ambient:**
- "warm cozy lighting"
- "cool blue ambient"
- "soft evening glow"

**Video/Photo:**
- "three-point lighting for video"
- "soft portrait lighting"
- "bright product photography"

**Events:**
- "party mode with vibrant colors"
- "dramatic spotlight effect"
- "concert stage lighting"

**Time-based:**
- "sunrise over 3 minutes"
- "gradual fade to darkness"
- "pulsing energy build"

## Tips

1. **Be specific:** "warm orange sunset at 70% intensity" works better than just "sunset"

2. **Mention your use case:** "for video recording" or "for live performance" helps the AI choose appropriate settings

3. **Iterate:** Generate, preview, refine with feedback

4. **Save variations:** Use `--output` to save different versions

5. **Check your fixtures:** The AI uses your actual fixture inventory, so make sure your workspace is up to date

## Troubleshooting

**"AI_API_KEY not set"**
- Add your API key to `.env`

**"Invalid XML syntax"**
- The AI generated invalid XML. Try regenerating or use a different description

**"Fixture ID not found"**
- Pull your latest workspace: `./lightsctl.sh pull-workspace`

**Scene doesn't match description**
- Try being more specific in your description
- Mention specific colors, intensities, or effects

## Next Steps

- Read the full documentation: [AI_SCENE_GENERATION.md](AI_SCENE_GENERATION.md)
- Explore example scenes: `scenes/examples/`
- Try different style profiles
- Experiment with scene variations: `--variations 3`
