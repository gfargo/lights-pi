# Roadmap

GitHub issues are the live backlog: <https://github.com/gfargo/lights-pi/issues>.
This page summarises what has shipped and what is still open. Release
details are on the [releases page](https://github.com/gfargo/lights-pi/releases).

## Shipped

| Area | Since |
|---|---|
| Auto-load default workspace, AI scene generation (CLI), fixture groups, `.qxf`-aware channels | v1.x |
| Web control UI, chases, cue lists, agentic chat, MCP server | v2.x up to v2.11 |
| Mobile layout and installable PWA, pytest + CI | v2.12 |
| Visual identity (CSS tokens) | v2.13 |
| Ruff in CI, dependency refresh | v2.14.0 |
| SSE event stream (#10), server-side chat history (#32), integration tests | v2.14.1 |
| Shared-password auth (#25), tap-tempo and nightly-backup fixes | v2.14.2 |
| MIDI and OSC backends, cue-list preview/waveform, stage-layout storage | v2.15.0 |
| Multi-workspace switching, HTTPS over Tailscale, gunicorn + eventlet | v2.16.0 |
| Self-healing QLC+ link and `/healthz` link health | v2.16.2 |
| Per-install branding, thumb-first phone UI | v2.17.0 |
| Gzip responses, audio-reactivity guide | v2.17.2 |
| Stage tab (#36): room plan in live colour, select, make group | v2.18.0 |
| Stage: aim scene commands at picked fixtures, aim arrows, floor-plan image, PNG export | v2.18.1 |
| OSC panel, feedback to the sender, TouchOSC button handling, setup guide (#27) | v2.18.1 |
| Audio: flash that fades back, kick/snare, colour follows loudness, MCP tools (#12) | v2.18.1 |
| Integration tests: every route smoke-tested, every MCP tool checked against Flask (#24) | v2.18.1 |
| Cue list timeline editor: drag to re-time, scrub to preview, waveform, live playhead (#29) | v2.19.0 |
| Audio: bands to fixtures (spectrum), bass/mids/highs on their own fixtures (#12) | v2.19.0 |
| Floor-plan uploads up to 8 MB, `deploy.sh` updates the MCP server | v2.19.0 |

## Open

Several open issues have their backend shipped and are waiting on UI work
that needs hands-on review with real hardware:

- [#26 MIDI controller input](https://github.com/gfargo/lights-pi/issues/26): web UI tab and learn/bind flow.
- [#27 OSC](https://github.com/gfargo/lights-pi/issues/27): the generated TouchOSC layout ([PR #131](https://github.com/gfargo/lights-pi/pull/131)) needs a check on a real tablet, and a QLab cue check.
- [#12 Audio reactivity](https://github.com/gfargo/lights-pi/issues/12): every mode is built; it needs a check with a real microphone.

New ideas go to GitHub issues rather than this file.
