# QLC+ Scenes

This directory contains QLC+ scene files that can be injected into workspaces or used as templates.

## Directory Structure

```
scenes/
├── examples/           # Example scenes and AI-generated test scenes
└── README.md          # This file
```

## Scene Files

Scene files are XML fragments that define QLC+ Function elements of type "Scene". They can be:
- Manually created
- AI-generated
- Extracted from existing workspaces

## Using Scenes

### Preview a Scene
```bash
cat scenes/examples/warm-sunset-complete.xml
```

### Inject into Workspace
```bash
# Using the AI scene generation system
./lightsctl.sh generate-scene "warm sunset" --output scenes/my-scene.xml --mock

# Then manually inject or use workspace tools
source scripts/lib/workspace.sh
workspace_inject_scene RiversWayStudio.qxw scenes/my-scene.xml RiversWayStudio-modified.qxw
```

### Generate New Scenes
```bash
# Generate and preview
./lightsctl.sh generate-scene "cool blue ambient" --preview --mock

# Generate and save
./lightsctl.sh generate-scene "party mode" --output scenes/party.xml --mock

# Generate and deploy to Pi
./lightsctl.sh generate-scene "dramatic spotlight" --add-to-workspace
```

## Scene Format

QLC+ scenes are XML Function elements with Type="Scene":

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE Function>
<Function Type="Scene" Name="Scene Name">
  <Speed FadeIn="500" FadeOut="500" Duration="0"/>
  <FixtureVal ID="0">1,255,2,140,3,0,4,180</FixtureVal>
  <FixtureVal ID="1">1,255,2,100,3,20,4,200</FixtureVal>
</Function>
```

### FixtureVal Format
`<FixtureVal ID="fixture_id">channel,value,channel,value,...</FixtureVal>`

Example:
- `ID="0"` - Fixture ID from workspace
- `1,255` - Channel 1 = 255 (Red: Full)
- `2,140` - Channel 2 = 140 (Green: Medium)
- `3,0` - Channel 3 = 0 (Blue: Off)
- `4,180` - Channel 4 = 180 (Dimmer: 70%)

## Example Scenes

### examples/warm-sunset-complete.xml
A warm sunset ambiance scene with orange/red tones at medium intensity.

### examples/ai-generated-*.xml
Various AI-generated test scenes demonstrating different styles and colors.

## Creating Custom Scenes

### Method 1: AI Generation
```bash
./lightsctl.sh generate-scene "your description here" --output scenes/custom.xml --mock
```

### Method 2: Extract from Workspace
```bash
source scripts/lib/workspace.sh
workspace_extract_scene RiversWayStudio.qxw 0 > scenes/extracted.xml
```

### Method 3: Manual Creation
1. Copy an example scene
2. Modify the Name attribute
3. Adjust FixtureVal elements for your fixtures
4. Set DMX values (0-255) for each channel

## Tips

- DMX values range from 0-255
- Dimmer: 0=off, 255=full brightness
- RGB: 0-255 per channel
- Warm colors: High red, medium green, low blue
- Cool colors: Low red, medium green, high blue
- Test scenes in QLC+ before deploying to production

## Scene Styles

### Complete Style
Self-contained scenes with all parameters set. Ready to use immediately.

### Modular Style
Separate scenes for color, intensity, and position that can be combined.

### Timeline Style
Sequences of scenes with timing (QLC+ Chasers).

### Reactive Style
Scenes that respond to audio or sensor inputs.

## Documentation

For more information, see:
- [AI Scene Generation](../docs/AI_SCENE_GENERATION.md)
- [Quick Start Guide](../docs/AI_SCENE_QUICK_START.md)
- [Implementation Status](../docs/AI_IMPLEMENTATION_STATUS.md)
