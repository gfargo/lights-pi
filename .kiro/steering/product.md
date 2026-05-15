---
inclusion: auto
---

# Product Overview

Headless Raspberry Pi lighting controller for studio environments. Provides
network-based control of DMX lighting fixtures through QLC+, plus a custom
natural-language control layer for voice/chat-driven scene generation.

## Core Purpose

Remote DMX lighting control via browser, phone, voice, or LLM agent without
physical access to the Pi. Anyone on the studio network can adjust lights
through QLC+'s web UI, the custom Virtual Console at `:5000`, by sending
plain-English commands like "make it warm and dim", or by connecting an
MCP-capable LLM client (Claude Desktop, ChatGPT, Cursor, custom agent) to the
Streamable HTTP MCP endpoint at `:5001/mcp`.

## Key Components

- **QLC+** open source lighting software running headless on port `9999`
- **ENTTEC DMX USB Pro** interface for USB-to-DMX
- **Raspberry Pi** as dedicated controller host
- **Custom control server** (Flask, port `5000`) providing:
  - Natural-language scene generation via OpenAI/Anthropic/Ollama
  - Live channel manipulation through a single persistent QLC+ WebSocket
  - Fixture group management with per-group scene targeting
  - Authoritative `.qxf` fixture-definition parsing for accurate channel roles
  - `POST /api/action` structured-dispatch endpoint that bypasses the AI
    interpreter (used by the MCP server so LLM agents don't double-LLM)
- **MCP server** (FastMCP + httpx, port `5001`) providing:
  - Streamable HTTP transport at `/mcp` for any MCP-capable LLM client
  - Read-only discovery tools (fixtures, groups, scenes, templates, live channels)
  - Write actions (activate_scene, apply_template, adjust_brightness/color,
    fade, generate_scene, save/snapshot_scene, set_channel)
  - `lights://workspace` resource as a one-shot rig context dump
  - Bearer-token scaffolding for optional auth
- **Landing page** (nginx on port 80) for guest/visitor access
- **`lightsctl.sh`** workstation-side CLI for provisioning, deployment,
  diagnostics, scene generation, group management, and MCP server lifecycle

## AI Scene Generation

The control server enriches every AI prompt with each fixture's authoritative
channel layout sourced from the QLC+ `.qxf` definitions on the Pi. The LLM
sees real channel names, semantic roles (`dimmer`, `red`, `warm`, `strobe`,
`macro`, etc.), and group classifications, so it picks the right channels per
fixture (e.g. driving warm/cool/amber on a SlimPAR Pro W instead of guessing
RGB and accidentally triggering its strobe channel).

## MCP Integration

The MCP server is a thin wrapper over the control server's REST API — it runs
as a sibling systemd service (`lighting-mcp.service`) ordered after the Flask
backend. The Flask app remains the single writer to QLC+; the MCP process
stays stateless and crash-safe. Both services boot together so the rig
exposes web UI, REST API, and MCP endpoint simultaneously on every Pi start.

## Target Users

Studio operators, photographers, videographers, dance/movement studios, and
agent-builders needing programmable DMX lighting controllable from any device,
voice prompt, or LLM agent on the network.
