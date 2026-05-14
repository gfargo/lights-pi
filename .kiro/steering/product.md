---
inclusion: auto
---

# Product Overview

Headless Raspberry Pi lighting controller for studio environments. Provides
network-based control of DMX lighting fixtures through QLC+, plus a custom
natural-language control layer for voice/chat-driven scene generation.

## Core Purpose

Remote DMX lighting control via browser, phone, or voice without physical access
to the Pi. Anyone on the studio network can adjust lights through QLC+'s web UI,
the custom Virtual Console at `:5000`, or by sending plain-English commands like
"make it warm and dim" that get translated into proper DMX values for the rig's
specific fixture mix.

## Key Components

- **QLC+** open source lighting software running headless on port `9999`
- **ENTTEC DMX USB Pro** interface for USB-to-DMX
- **Raspberry Pi** as dedicated controller host
- **Custom control server** (Flask, port `5000`) providing:
  - Natural-language scene generation via OpenAI/Anthropic/Ollama
  - Live channel manipulation through a single persistent QLC+ WebSocket
  - Fixture group management with per-group scene targeting
  - Authoritative `.qxf` fixture-definition parsing for accurate channel roles
- **Landing page** (nginx on port 80) for guest/visitor access
- **`lightsctl.sh`** workstation-side CLI for provisioning, deployment,
  diagnostics, scene generation, and group management

## AI Scene Generation

The control server enriches every AI prompt with each fixture's authoritative
channel layout sourced from the QLC+ `.qxf` definitions on the Pi. The LLM
sees real channel names, semantic roles (`dimmer`, `red`, `warm`, `strobe`,
`macro`, etc.), and group classifications, so it picks the right channels per
fixture (e.g. driving warm/cool/amber on a SlimPAR Pro W instead of guessing
RGB and accidentally triggering its strobe channel).

## Target Users

Studio operators, photographers, videographers, and dance/movement studios
needing programmable DMX lighting that can be controlled from any device or
voice prompt on the network.
