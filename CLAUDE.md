# CLAUDE.md — Contributor & Agent Steering

Project-level conventions for **lights-pi**. Read this before your first PR.
Applies to both human contributors and AI agents driving the codebase.

---

## Branch & commit conventions

- **Branch prefixes:** `feat/`, `fix/`, `chore/`, `docs/`, `refactor/`.
  Example: `feat/midi-input`, `fix/diagnostics-not-installed-state`,
  `docs/post-v2.13-sweep`.
- **Never include `claude` (or any other agent identifier) in branch names.**
  If your tooling generates a `claude/...` branch, rename it before pushing.
- **Commits are authored by the user only.** Do not add `Co-Authored-By:`
  trailers, especially not for Claude or other AI assistants.
- **Conventional commit messages** matching the existing log:
  `feat(scope): summary`, `fix(scope): summary`, `docs: summary`,
  `chore: summary`. The scope is the area touched (`ui`, `chat`, `mcp`,
  `diag`, `mobile+pwa`, etc.).
- **Never push directly to `main`.** Open a PR with `gh pr create` and let
  the maintainer review/merge. Two exceptions, both narrow:
  - **`lights-pi-www`** (marketing site) — commits directly to `main`. The
    user treats it as low-stakes content and doesn't want PR ceremony.
  - **`lights-pi.wiki`** (GitHub wiki) — commits directly to `master`.
    GitHub wikis don't support pull requests by design; direct push is
    the only way to update them. Prior wiki history is all direct pushes.

---

## Release workflow

- **Versioning:** semantic-ish — `vMAJOR.MINOR.PATCH`. Features bump minor,
  fixes/polish bump patch.
- Tag the merge commit on `main` (`git tag vX.Y.Z && git push --tags`).
- Write release notes that follow the pattern of recent releases — see
  [v2.13.1](https://github.com/gfargo/lights-pi/releases/tag/v2.13.1) as a
  template:
  - One-line **summary** at top.
  - **What changed** as a tight bulleted list, prose-style not raw commits.
  - **Why** if the change isn't self-explanatory.
  - **Upgrade notes** if behaviour or deploy steps differ.
- All notable changes — even patch releases — get a release entry. The
  README's "Recent Releases" section links to the GitHub releases page; keep
  that as the source of truth and only summarize a few entries in the README.

---

## Deploy paths

Two things called "update" exist; they do different jobs.

| Command | What it does |
|---|---|
| `bash scripts/deploy.sh` | **Deploys lights-pi code.** Rsyncs `control-server/`, `scripts/`, and `lightsctl.sh` to the Pi and restarts `lighting-control.service`; when the MCP server is installed it also syncs `mcp-server/` (keeping its `.env`) and restarts `lighting-mcp.service`. This is the real deploy path. |
| `./lightsctl.sh update` | **OS packages only.** Runs `sudo apt update && apt upgrade` on the Pi. Does not touch lights-pi code. |
| `./lightsctl.sh mcp-install` | Installs and enables the MCP server (port 5001). Not part of initial provisioning — run separately, otherwise Pi-health reports `lighting-mcp: not_installed`. |

After every deploy: **hard-refresh the browser** (⌘⇧R / Ctrl⇧R) to bust
cached CSS/JS.

`.env` is excluded from `scripts/deploy.sh` rsync — production secrets on
the Pi must be edited there directly (`./lightsctl.sh ssh`).

---

## Repo layout

```
control-server/         Flask app — the single writer to QLC+.
  app.py                ~all routes, AI integration, chat agent loop.
  chat_store.py         SQLite chat history (/api/conversations).
  event_bus.py          SSE event stream.
  audio_engine.py, midi_engine.py, osc_backend.py
                        Audio-reactive, MIDI and OSC inputs.
  gunicorn.conf.py      Production run config (one eventlet worker).
  static/               Per-install logo.* / theme.css; deploy.sh skips them.
  templates/index.html  Single-file UI: HTML + CSS tokens + vanilla JS.
  tests/                pytest suite (~800 tests: pure helpers, integration/,
                        eventlet stress test).
mcp-server/             FastMCP wrapper. Calls control-server over localhost.
scripts/                Provisioning + ops shells.
  deploy.sh             Workstation → Pi sync. See above.
  provisioning/         One-shot Pi setup helpers.
  services/             systemd unit installers (control, mcp, etc.).
lightsctl.sh            Workstation-side SSH/ops swiss-army knife.
docs/                   Architecture + roadmap + MCP server docs.
.github/workflows/      CI (test.yml) and the release-triggered www deploy.
landing/                Static landing page served by nginx on the Pi.
workspaces/             Versioned `.qxw` workspace files.
scenes/                 AI-saved / hand-built scene library.
studio.qxw              Canonical studio workspace used by default.
```

Sibling repos (not in this tree):

- **`lights-pi-www`** — public marketing site. Deployed to
  [lights.griffen.codes](https://lights.griffen.codes/) via Vercel.
  Work directly on `main`, no PR ceremony. A GitHub release published on
  this repo (`.github/workflows/trigger-www-deploy.yml`) hits a Vercel
  Deploy Hook to redeploy it — useful when the site references
  lights-pi's release history/version even though its own source hasn't
  changed.
- **`lights-pi.wiki`** — GitHub wiki for long-form docs.

---

## Continuous integration

`.github/workflows/test.yml` runs four jobs; all must pass before merge:

1. **Python** on 3.11, 3.12 and 3.13 (3.13 is what the Pi runs): ruff,
   pytest (`control-server/tests/`), and a syntax check of
   `control-server/app.py` and `mcp-server/server.py`. Pinned to
   `ubuntu-24.04` because `aubio` does not build on newer images.
2. **`node --check`** on inline JS extracted from `templates/index.html` —
   syntax sanity for the single-file UI.
3. **Backup/restore smoke test** (`scripts/test_backup_restore.sh`, uses
   rclone).
4. **HTML tag balance** — counts opening vs closing `<div>`, `<script>`,
   `<style>`, `<button>`, `<select>` tags to catch the most common
   template-merging breakage.

Run locally before committing:

```bash
cd control-server && python -m pytest -q
```

---

## Key conventions

- **Single-file Flask template.** `control-server/templates/index.html` holds
  the full UI — HTML, CSS, and vanilla JS in one file. No build step, no
  framework. Edits go straight to the file.
- **Token-driven CSS.** All colours, hairlines, and signal accents are
  declared as CSS custom properties on `:root` (`--ink`, `--paper`, `--rule`,
  `--amber-tungsten`, `--signal-*`). `--amber-tungsten` is the brand accent,
  overridden per install by `BRAND_ACCENT`. Add new colours to the token set
  before using them inline.
- **Pure-helper unit tests preferred.** Tests target side-effect-free
  functions in `app.py` — colour math, palette parsing, cue normalization,
  systemd-state parsing. Avoid mocking the QLC+ WebSocket. If you're
  reaching for a mock, the function probably wants to be refactored into a
  pure helper first.
- **The Flask process is the single writer to QLC+.** MCP and any other
  callers go through `lighting-control.service` over localhost HTTP — never
  directly to the QLC+ WebSocket.
- **Chat history is stored server-side.** `POST /api/chat` takes the
  history plus an optional `conversation_id` and appends new turns to SQLite
  (`chat_store.py`, `CHAT_DB_PATH`, default `~/.qlcplus/chat_history.db`).
  The browser keeps only a local copy and the current conversation id.
- **Production runs under gunicorn + eventlet.** Code on the QLC+ asyncio
  thread must not touch eventlet-greened primitives (locks, queues, futures,
  logging). See the run-model section of CONTROL_SERVER_ARCHITECTURE.md and
  test new loop-thread code with `tests/_eventlet_stress.py`.
- **AI provider is pluggable.** `AI_PROVIDER=anthropic|openai|ollama` in
  `.env`. Tool-calling currently works for Anthropic and OpenAI; Ollama is
  text-generation only.

---

## When in doubt

- Read [docs/CONTROL_SERVER_ARCHITECTURE.md](docs/CONTROL_SERVER_ARCHITECTURE.md)
  before changing anything in `control-server/app.py`.
- Read [docs/MCP_SERVER.md](docs/MCP_SERVER.md) before touching `mcp-server/`.
- The roadmap with the live backlog lives in
  [docs/ROADMAP.md](docs/ROADMAP.md). New ideas go to GitHub issues.
