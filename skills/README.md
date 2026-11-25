# Agent skill

`skills/lights-pi/` is an [Agent Skill](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview)
that teaches an AI agent how to work with a Lights Pi rig: operate the lights through the
MCP tools, set up and deploy an install, brand it, and troubleshoot the known failure modes.
It pairs with the MCP server (`mcp-server/`); the skill supplies the know-how, the MCP
server supplies the tools.

```
skills/lights-pi/
  SKILL.md                  entry point: when to use it, safety rules, routing
  references/operating.md   MCP tool catalog, scenes, Kelvin, chases, cue lists, lighting craft
  references/setup.md       provisioning, deploy vs update, secrets, MCP install, Tailscale
  references/branding.md    BRAND_* variables, logo, theme.css, landing page
  references/troubleshooting.md  QLC+ link freeze, /healthz, ttyUSB0, Wi-Fi, undervoltage
```

## Install

**Claude Code** — copy the folder into your personal or project skills directory:

```bash
cp -r skills/lights-pi ~/.claude/skills/        # all projects
# or, inside a project:
mkdir -p .claude/skills && cp -r skills/lights-pi .claude/skills/
```

**Claude Desktop / claude.ai** — zip the folder and upload it under Settings, Capabilities,
Skills:

```bash
cd skills && zip -r lights-pi.zip lights-pi
```

**Other Agent Skills clients** — point the client at `skills/lights-pi/` (the folder with
`SKILL.md`).

Then connect the MCP server so the agent has tools to call (see
[docs/MCP_SERVER.md](../docs/MCP_SERVER.md#client-wiring)). Without it the skill still helps
with setup, deploys and troubleshooting from a shell.
