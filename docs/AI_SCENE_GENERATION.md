# AI Scene Generation System

## Overview

An AI-powered system that generates QLC+ scenes based on natural language descriptions, fixture inventory, and style profiles. The system understands your lighting setup and creates appropriate DMX channel values to achieve the desired mood or effect.

---

## Core Concept

**Input:**
- Natural language description: "warm sunset ambiance" or "high-energy concert vibe"
- Current fixture inventory (auto-detected from QLC+ workspace)
- Style profile: "complete", "modular", "timeline", or "reactive"

**Output:**
- QLC+ scene XML with DMX channel values
- Multiple variations to choose from
- Preview/validation before deployment

---

## Style Profiles

### 1. Complete Style

**Philosophy:** Self-contained, ready-to-use scenes

**Characteristics:**
- All fixture parameters set explicitly
- Colors, intensities, positions defined
- No dependencies on other scenes
- Immediate playback ready
- Best for: Simple setups, beginners, standalone scenes

**Example Scene: "Warm Sunset"**
```xml
<Scene>
  <Name>Warm Sunset</Name>
  <Fixture ID="1">
    <Channel Number="1">255</Channel>  <!-- Red: Full -->
    <Channel Number="2">140</Channel>  <!-- Green: Medium -->
    <Channel Number="3">0</Channel>    <!-- Blue: Off -->
    <Channel Number="4">180</Channel>  <!-- Dimmer: 70% -->
  </Fixture>
  <Fixture ID="2">
    <Channel Number="1">255</Channel>  <!-- Red: Full -->
    <Channel Number="2">100</Channel>  <!-- Green: Low -->
    <Channel Number="3">20</Channel>   <!-- Blue: Slight -->
    <Channel Number="4">200</Channel>  <!-- Dimmer: 78% -->
  </Fixture>
</Scene>
```

**Use Cases:**
- Quick scene creation
- Simple lighting needs
- Learning QLC+
- Standalone effects

---

### 2. Modular Style

**Philosophy:** Composable building blocks

**Characteristics:**
- Scenes as functions/layers
- Separate color, intensity, position scenes
- Combine multiple scenes for final effect
- Reusable components
- Best for: Complex setups, advanced users, dynamic shows

**Example Scene: "Warm Sunset" (Modular)**

**Color Layer:**
```xml
<Scene>
  <Name>Color: Warm Orange</Name>
  <Fixture ID="1">
    <Channel Number="1">255</Channel>  <!-- Red -->
    <Channel Number="2">140</Channel>  <!-- Green -->
    <Channel Number="3">0</Channel>    <!-- Blue -->
  </Fixture>
  <Fixture ID="2">
    <Channel Number="1">255</Channel>
    <Channel Number="2">100</Channel>
    <Channel Number="3">20</Channel>
  </Fixture>
</Scene>
```

**Intensity Layer:**
```xml
<Scene>
  <Name>Intensity: Evening Glow</Name>
  <Fixture ID="1">
    <Channel Number="4">180</Channel>  <!-- Dimmer: 70% -->
  </Fixture>
  <Fixture ID="2">
    <Channel Number="4">200</Channel>  <!-- Dimmer: 78% -->
  </Fixture>
</Scene>
```

**Position Layer (if applicable):**
```xml
<Scene>
  <Name>Position: Downstage Wash</Name>
  <Fixture ID="1">
    <Channel Number="5">128</Channel>  <!-- Pan: Center -->
    <Channel Number="6">64</Channel>   <!-- Tilt: Down -->
  </Fixture>
</Scene>
```

**Composition:**
- Activate "Color: Warm Orange" + "Intensity: Evening Glow" + "Position: Downstage Wash"
- Result: Warm sunset effect
- Can swap color layer for different mood while keeping intensity/position

**Use Cases:**
- Complex shows
- Dynamic scene building
- Reusable components
- Advanced programming

---

### 3. Timeline Style

**Philosophy:** Scene as a sequence of states over time

**Characteristics:**
- Scenes include temporal progression
- Define keyframes with timing
- Automatic transitions between states
- Built-in fade curves and easing
- Best for: Storytelling, automated shows, time-based effects

**Example Scene: "Sunrise Sequence"**
```xml
<Scene Type="Timeline">
  <Name>Sunrise Sequence</Name>
  <Duration>180000</Duration> <!-- 3 minutes -->
  
  <Keyframe Time="0">
    <Fixture ID="1">
      <Channel Number="1">0</Channel>    <!-- Red: Off -->
      <Channel Number="2">0</Channel>    <!-- Green: Off -->
      <Channel Number="3">50</Channel>   <!-- Blue: Deep night -->
      <Channel Number="4">30</Channel>   <!-- Dimmer: Very low -->
    </Fixture>
  </Keyframe>
  
  <Keyframe Time="60000"> <!-- 1 minute -->
    <Fixture ID="1">
      <Channel Number="1">100</Channel>  <!-- Red: Dawn -->
      <Channel Number="2">50</Channel>   <!-- Green: Warming -->
      <Channel Number="3">80</Channel>   <!-- Blue: Morning -->
      <Channel Number="4">120</Channel>  <!-- Dimmer: Rising -->
    </Fixture>
  </Keyframe>
  
  <Keyframe Time="180000"> <!-- 3 minutes -->
    <Fixture ID="1">
      <Channel Number="1">255</Channel>  <!-- Red: Full sun -->
      <Channel Number="2">200</Channel>  <!-- Green: Bright -->
      <Channel Number="3">150</Channel>  <!-- Blue: Clear sky -->
      <Channel Number="4">255</Channel>  <!-- Dimmer: Full -->
    </Fixture>
  </Keyframe>
  
  <Transition Type="smooth" Curve="ease-in-out"/>
</Scene>
```

**Use Cases:**
- Automated shows without manual triggering
- Storytelling sequences
- Time-based ambient changes
- Sunrise/sunset simulations
- Gradual mood transitions

---

### 4. Reactive Style

**Philosophy:** Scene responds to external inputs

**Characteristics:**
- Defines behavior rules, not static values
- Responds to audio, sensors, or data feeds
- Conditional logic for different states
- Dynamic parameter mapping
- Best for: Live performances, interactive installations, audio-reactive shows

**Example Scene: "Audio Pulse"**
```xml
<Scene Type="Reactive">
  <Name>Audio Pulse</Name>
  <Input Source="audio" Channel="bass"/>
  
  <Fixture ID="1">
    <!-- Dimmer follows bass intensity -->
    <Channel Number="4">
      <Mapping Input="bass" Min="0" Max="255" Curve="exponential"/>
    </Channel>
    
    <!-- Color shifts with frequency -->
    <Channel Number="1">
      <Mapping Input="bass" Min="255" Max="100"/>
    </Channel>
    <Channel Number="2">
      <Mapping Input="mid" Min="100" Max="255"/>
    </Channel>
    <Channel Number="3">
      <Mapping Input="treble" Min="50" Max="200"/>
    </Channel>
  </Fixture>
  
  <!-- Conditional: Strobe on peaks -->
  <Condition>
    <If Input="bass" Operator=">" Value="200">
      <Fixture ID="1">
        <Channel Number="7">255</Channel> <!-- Strobe on -->
      </Fixture>
    </If>
  </Condition>
</Scene>
```

**Use Cases:**
- Music-reactive lighting
- Interactive installations
- Sensor-driven effects
- Live performance adaptation
- Environmental response (temperature, motion, etc.)

---

### Style Comparison Matrix

| Feature | Complete | Modular | Timeline | Reactive |
|---------|----------|---------|----------|----------|
| Complexity | Low | Medium | Medium-High | High |
| Flexibility | Low | High | Medium | Very High |
| Setup Time | Fast | Medium | Slow | Slow |
| Reusability | Low | Very High | Medium | High |
| Dynamic | No | No | Yes | Yes |
| Best For | Beginners | Advanced users | Shows | Live events |
| Dependencies | None | Multiple scenes | Time | External input |

---

## AI Scene Generation Workflow

### Step 1: Fixture Discovery

**Command:**
```bash
./lightsctl.sh generate-scene "warm sunset ambiance" --style complete
```

**Process:**
1. Pull current workspace from Pi
2. Parse QLC+ XML to extract:
   - Fixture list with IDs
   - Fixture types (manufacturer, model)
   - Channel mappings (RGB, dimmer, pan/tilt, etc.)
   - Current universe assignments
   - Fixture capabilities (color mixing, gobos, etc.)

**Example Fixture Inventory:**
```json
{
  "fixtures": [
    {
      "id": 1,
      "name": "Stage Left Par",
      "manufacturer": "Generic",
      "model": "RGB LED Par",
      "channels": {
        "1": "Red",
        "2": "Green",
        "3": "Blue",
        "4": "Dimmer"
      },
      "capabilities": ["rgb", "dimmer"]
    },
    {
      "id": 2,
      "name": "Stage Right Par",
      "manufacturer": "Generic",
      "model": "RGB LED Par",
      "channels": {
        "1": "Red",
        "2": "Green",
        "3": "Blue",
        "4": "Dimmer"
      },
      "capabilities": ["rgb", "dimmer"]
    },
    {
      "id": 3,
      "name": "Moving Head",
      "manufacturer": "Chauvet",
      "model": "Intimidator Spot 355",
      "channels": {
        "1": "Pan",
        "2": "Pan Fine",
        "3": "Tilt",
        "4": "Tilt Fine",
        "5": "Speed",
        "6": "Dimmer",
        "7": "Shutter",
        "8": "Color Wheel",
        "9": "Gobo"
      },
      "capabilities": ["pan_tilt", "dimmer", "color_wheel", "gobo"]
    }
  ]
}
```

---

### Step 2: AI Prompt Construction

**System Prompt:**
```
You are a professional lighting designer with expertise in DMX control and QLC+.
Your task is to generate scene configurations based on user descriptions.

You will receive:
1. A natural language description of the desired scene
2. A complete fixture inventory with capabilities
3. A style profile (complete or modular)

You must output valid QLC+ scene XML that:
- Uses only the provided fixtures
- Sets appropriate DMX values (0-255)
- Considers fixture capabilities
- Matches the described mood/effect
- Follows the specified style profile

For COMPLETE style:
- Set all relevant channels for each fixture
- Create self-contained scenes
- Include colors, intensities, and positions

For MODULAR style:
- Create separate scenes for different aspects (color, intensity, position)
- Name scenes clearly (e.g., "Color: Warm Orange", "Intensity: Evening Glow")
- Allow for composition and reuse
- Each scene should control only one aspect

DMX Value Guidelines:
- Dimmer: 0=off, 255=full brightness
- RGB: 0-255 per channel
- Pan/Tilt: 0-255 (fixture-specific range)
- Color Wheel: Discrete positions (check fixture manual)
- Gobo: Discrete positions (check fixture manual)

Lighting Design Principles:
- Warm colors: High red, medium green, low blue
- Cool colors: Low red, medium green, high blue
- Sunset: Orange/red gradient, 60-80% intensity
- Concert: High intensity, saturated colors, movement
- Ambient: Low intensity, soft colors
- Dramatic: High contrast, focused beams
```

**User Prompt:**
```
Description: "warm sunset ambiance"
Style: complete

Fixtures:
[fixture inventory JSON]

Generate a QLC+ scene that creates a warm sunset ambiance using these fixtures.
Consider:
- Warm orange/red tones
- Gradual intensity (not too bright)
- Natural, relaxing feel
- Appropriate for video recording

Output valid QLC+ scene XML.
```

---

### Step 3: AI Response Processing

**Expected AI Output (Complete Style):**
```xml
<?xml version="1.0" encoding="UTF-8"?>
<Scene>
  <Name>Warm Sunset Ambiance</Name>
  <Description>Warm orange and red tones with medium intensity for a relaxing sunset feel</Description>
  
  <!-- Stage Left Par -->
  <Fixture ID="1">
    <Channel Number="1">255</Channel>  <!-- Red: Full -->
    <Channel Number="2">140</Channel>  <!-- Green: Medium-high -->
    <Channel Number="3">0</Channel>    <!-- Blue: Off -->
    <Channel Number="4">180</Channel>  <!-- Dimmer: 70% -->
  </Fixture>
  
  <!-- Stage Right Par -->
  <Fixture ID="2">
    <Channel Number="1">255</Channel>  <!-- Red: Full -->
    <Channel Number="2">100</Channel>  <!-- Green: Medium-low -->
    <Channel Number="3">20</Channel>   <!-- Blue: Slight warmth -->
    <Channel Number="4">200</Channel>  <!-- Dimmer: 78% -->
  </Fixture>
  
  <!-- Moving Head (positioned for backlight) -->
  <Fixture ID="3">
    <Channel Number="1">128</Channel>  <!-- Pan: Center -->
    <Channel Number="3">200</Channel>  <!-- Tilt: High angle -->
    <Channel Number="6">150</Channel>  <!-- Dimmer: 59% -->
    <Channel Number="7">255</Channel>  <!-- Shutter: Open -->
    <Channel Number="8">32</Channel>   <!-- Color Wheel: Orange -->
  </Fixture>
</Scene>
```

**Expected AI Output (Modular Style):**
```xml
<!-- Scene 1: Color Layer -->
<Scene>
  <Name>Color: Sunset Orange</Name>
  <Description>Warm orange tones for sunset effect</Description>
  <Fixture ID="1">
    <Channel Number="1">255</Channel>  <!-- Red -->
    <Channel Number="2">140</Channel>  <!-- Green -->
    <Channel Number="3">0</Channel>    <!-- Blue -->
  </Fixture>
  <Fixture ID="2">
    <Channel Number="1">255</Channel>
    <Channel Number="2">100</Channel>
    <Channel Number="3">20</Channel>
  </Fixture>
  <Fixture ID="3">
    <Channel Number="8">32</Channel>   <!-- Color Wheel: Orange -->
  </Fixture>
</Scene>

<!-- Scene 2: Intensity Layer -->
<Scene>
  <Name>Intensity: Evening Glow</Name>
  <Description>Medium-low intensity for ambient feel</Description>
  <Fixture ID="1">
    <Channel Number="4">180</Channel>  <!-- Dimmer: 70% -->
  </Fixture>
  <Fixture ID="2">
    <Channel Number="4">200</Channel>  <!-- Dimmer: 78% -->
  </Fixture>
  <Fixture ID="3">
    <Channel Number="6">150</Channel>  <!-- Dimmer: 59% -->
    <Channel Number="7">255</Channel>  <!-- Shutter: Open -->
  </Fixture>
</Scene>

<!-- Scene 3: Position Layer -->
<Scene>
  <Name>Position: Backlight Wash</Name>
  <Description>Moving head positioned for backlight</Description>
  <Fixture ID="3">
    <Channel Number="1">128</Channel>  <!-- Pan: Center -->
    <Channel Number="3">200</Channel>  <!-- Tilt: High angle -->
  </Fixture>
</Scene>
```

---

### Step 4: Validation

**Automated Checks:**
1. XML syntax validation
2. Fixture ID existence check
3. Channel number validity (within fixture range)
4. DMX value range (0-255)
5. Capability matching (don't set pan/tilt on non-moving fixtures)

**Example Validation:**
```bash
# Check if fixture IDs exist
for fixture_id in scene_xml:
    if fixture_id not in workspace_fixtures:
        error("Fixture ID {fixture_id} not found in workspace")

# Check channel numbers
for fixture in scene_xml:
    for channel in fixture.channels:
        if channel > fixture.max_channels:
            error("Channel {channel} exceeds fixture {fixture.id} max")

# Check DMX values
for value in scene_xml.values:
    if not 0 <= value <= 255:
        error("DMX value {value} out of range")
```

---

### Step 5: Deployment

**Options:**

**A. Add to Existing Workspace**
```bash
./lightsctl.sh generate-scene "warm sunset" --style complete --add-to-workspace
```
- Pulls current workspace
- Injects new scene(s) into XML
- Uploads modified workspace
- Restarts QLC+ service

**B. Create New Workspace**
```bash
./lightsctl.sh generate-scene "warm sunset" --style complete --new-workspace sunset.qxw
```
- Creates new workspace file
- Includes generated scene(s)
- Saves locally (doesn't deploy)

**C. Preview Only**
```bash
./lightsctl.sh generate-scene "warm sunset" --style complete --preview
```
- Generates scene XML
- Displays in terminal
- Doesn't modify workspace
- User can review before deploying

---

## CLI Implementation

### Command Structure

```bash
./lightsctl.sh generate-scene <description> [options]

Options:
  --style <complete|modular|timeline|reactive>    Scene style (default: complete)
  --add-to-workspace            Add to current workspace and deploy
  --new-workspace <file>        Create new workspace file
  --preview                     Show generated XML without deploying
  --variations <n>              Generate N variations (default: 1)
  --output <file>               Save scene XML to file
  --model <name>                AI model to use (default: claude-3-5-sonnet)
```

### Examples

**Generate and preview:**
```bash
./lightsctl.sh generate-scene "dramatic spotlight" --preview
```

**Generate and add to workspace:**
```bash
./lightsctl.sh generate-scene "party mode" --style modular --add-to-workspace
```

**Generate multiple variations:**
```bash
./lightsctl.sh generate-scene "ambient blue" --variations 3 --preview
```

**Save to file for manual review:**
```bash
./lightsctl.sh generate-scene "sunset" --output scenes/sunset.xml
```

---

## Scene Library System

### Library Structure

```
scenes/
├── library.json              # Scene metadata
├── complete/                 # Complete style scenes
│   ├── warm-sunset.xml
│   ├── cool-blue.xml
│   └── party-mode.xml
└── modular/                  # Modular style scenes
    ├── colors/
    │   ├── warm-orange.xml
    │   ├── cool-blue.xml
    │   └── vibrant-purple.xml
    ├── intensities/
    │   ├── dim-ambient.xml
    │   ├── medium-glow.xml
    │   └── full-bright.xml
    └── positions/
        ├── front-wash.xml
        ├── back-light.xml
        └── side-accent.xml
```

### Library Metadata

```json
{
  "version": "1.0",
  "scenes": [
    {
      "id": "warm-sunset",
      "name": "Warm Sunset",
      "description": "Warm orange and red tones with medium intensity",
      "style": "complete",
      "tags": ["warm", "ambient", "sunset", "orange"],
      "fixtures_required": ["rgb"],
      "difficulty": "beginner",
      "author": "AI Generated",
      "created": "2026-03-09",
      "downloads": 142,
      "rating": 4.7
    },
    {
      "id": "color-warm-orange",
      "name": "Color: Warm Orange",
      "description": "Warm orange color layer for sunset effects",
      "style": "modular",
      "layer": "color",
      "tags": ["warm", "orange", "color"],
      "fixtures_required": ["rgb"],
      "difficulty": "intermediate",
      "author": "AI Generated",
      "created": "2026-03-09",
      "compatible_with": ["intensity-evening-glow", "position-backlight"]
    }
  ]
}
```

### Library Commands

```bash
# List available scenes
./lightsctl.sh scene-library list [--style complete|modular] [--tag warm]

# Search scenes
./lightsctl.sh scene-library search "sunset"

# Install scene from library
./lightsctl.sh scene-library install warm-sunset

# Contribute scene to library
./lightsctl.sh scene-library contribute scenes/my-scene.xml

# Update library
./lightsctl.sh scene-library update
```

---

## Advanced Features

### 1. Scene Variations

Generate multiple variations of the same description:

```bash
./lightsctl.sh generate-scene "warm sunset" --variations 3
```

**Output:**
- Variation 1: More orange, higher intensity
- Variation 2: More red, medium intensity
- Variation 3: Orange-pink blend, lower intensity

User selects preferred variation.

---

### 2. Scene Evolution

Iteratively refine a scene:

```bash
# Generate initial scene
./lightsctl.sh generate-scene "sunset" --output sunset-v1.xml

# Refine with feedback
./lightsctl.sh refine-scene sunset-v1.xml "make it more orange and less bright"
```

AI adjusts the scene based on feedback.

---

### 3. Scene Blending

Combine multiple scenes:

```bash
./lightsctl.sh blend-scenes sunset.xml party.xml --ratio 70:30
```

Creates a new scene that's 70% sunset, 30% party mode.

---

### 4. Fixture-Aware Suggestions

AI suggests scenes based on your fixtures:

```bash
./lightsctl.sh suggest-scenes
```

**Output:**
```
Based on your fixtures (2x RGB Par, 1x Moving Head), here are suggested scenes:

1. "Three-Point Lighting" - Classic video setup
2. "Dynamic Concert" - Moving head effects with color washes
3. "Ambient Workspace" - Soft, focused lighting
4. "Dramatic Interview" - High-contrast key/fill/back
5. "Party Mode" - Colorful, energetic effects

Generate any scene: ./lightsctl.sh generate-scene "Three-Point Lighting"
```

---

### 5. Scene Templates

Pre-defined templates for common scenarios:

```bash
./lightsctl.sh generate-from-template youtube-studio
```

**Templates:**
- `youtube-studio` - Three-point lighting for video
- `photography-portrait` - Soft, flattering portrait lighting
- `photography-product` - Even, shadow-free product lighting
- `live-stream` - Dynamic, camera-friendly lighting
- `party` - Colorful, energetic effects
- `ambient` - Soft, background lighting
- `dramatic` - High-contrast, focused lighting

---

## Technical Implementation

### Architecture

```
lightsctl.sh
    ↓
scripts/lib/ai_scene.sh
    ↓
┌─────────────────────────────────────┐
│  1. Fixture Discovery               │
│     - Pull workspace from Pi        │
│     - Parse QLC+ XML                │
│     - Extract fixture inventory     │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│  2. AI Prompt Construction          │
│     - Build system prompt           │
│     - Include fixture data          │
│     - Add style profile             │
│     - Add user description          │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│  3. AI API Call                     │
│     - Claude API (Anthropic)        │
│     - GPT-4 API (OpenAI)            │
│     - Local LLM (Ollama)            │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│  4. Response Processing             │
│     - Parse XML response            │
│     - Validate structure            │
│     - Check fixture IDs             │
│     - Verify DMX values             │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│  5. Deployment                      │
│     - Inject into workspace         │
│     - Upload to Pi                  │
│     - Restart QLC+ service          │
└─────────────────────────────────────┘
```

### Dependencies

```bash
# Required
- curl or wget (API calls)
- jq (JSON parsing)
- xmllint (XML validation)

# Optional
- fzf (interactive selection)
- bat (pretty preview)
```

### Configuration

**Add to .env:**
```bash
# AI Scene Generation
AI_PROVIDER="anthropic"           # anthropic, openai, ollama
AI_API_KEY="sk-ant-..."           # API key (not needed for ollama)
AI_MODEL="claude-3-5-sonnet"      # Model name
AI_SCENE_STYLE="complete"         # Default style
AI_SCENE_VARIATIONS="1"           # Default variations
```

---

## Future Enhancements

### Phase 2
- Web UI for scene generation
- Visual scene preview (3D fixture visualization)
- Scene rating and feedback system
- Community scene library
- Scene marketplace

### Phase 3
- Real-time scene adjustment (AI watches output, suggests tweaks)
- Audio-reactive scene generation (analyze music, generate matching scenes)
- Video analysis (analyze video content, suggest lighting)
- Multi-universe support
- Advanced fixture capabilities (gobos, prisms, etc.)

---

## Example Workflows

### Workflow 1: Quick Scene Creation

```bash
# Generate a scene
./lightsctl.sh generate-scene "warm cozy lighting" --add-to-workspace

# Test it in QLC+ web UI
./lightsctl.sh open-web

# If good, set as default
./lightsctl.sh set-default-workspace
```

---

### Workflow 2: Building a Show (Modular)

```bash
# Generate color layers
./lightsctl.sh generate-scene "vibrant red" --style modular --output colors/red.xml
./lightsctl.sh generate-scene "cool blue" --style modular --output colors/blue.xml
./lightsctl.sh generate-scene "warm orange" --style modular --output colors/orange.xml

# Generate intensity layers
./lightsctl.sh generate-scene "dim ambient" --style modular --output intensities/dim.xml
./lightsctl.sh generate-scene "full bright" --style modular --output intensities/full.xml

# Generate position layers (if moving heads)
./lightsctl.sh generate-scene "front wash" --style modular --output positions/front.xml
./lightsctl.sh generate-scene "back light" --style modular --output positions/back.xml

# Combine in QLC+ virtual console
# Button 1: Red + Dim + Front = Subtle red wash
# Button 2: Blue + Full + Back = Bright blue backlight
# Button 3: Orange + Dim + Front = Warm ambient
```

---

### Workflow 3: Scene Library

```bash
# Browse library
./lightsctl.sh scene-library list --tag warm

# Install a scene
./lightsctl.sh scene-library install warm-sunset

# Customize it
./lightsctl.sh refine-scene warm-sunset "make it more orange"

# Contribute back
./lightsctl.sh scene-library contribute warm-sunset-custom
```

---

This AI scene generation system transforms lighting design from manual DMX programming into natural language descriptions, making professional lighting accessible to everyone while maintaining the flexibility and power of QLC+.
