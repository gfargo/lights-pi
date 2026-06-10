# Security Audit Methodology

A repeatable strategy for performing security audits on small-to-medium production systems — web apps, IoT devices, self-hosted services, and Pi-based projects.

Developed from four audit passes on a Raspberry Pi lighting controller (Flask + MCP + nginx + systemd + Tailscale).

---

## Philosophy

Audit in layers, from outside-in. Each pass has a distinct focus. Resist the urge to fix during the audit — document everything first, then prioritize remediation as a separate step.

The goal is **comprehensive coverage without diminishing returns**. Four passes consistently uncovers findings that a single deep-dive misses, because each pass changes your mental model of the system.

---

## Pass Structure

### Pass 1: Attack Surface & Access Control

**Focus:** What's exposed, who can reach it, what stops them.

Checklist:
- [ ] Map all listening ports and services (bind addresses)
- [ ] Identify authentication mechanisms (or lack thereof) on every endpoint
- [ ] Check CORS configuration — origins, methods, credentials
- [ ] Review network perimeter: firewall rules, VPN access, port forwarding
- [ ] Identify all user-facing inputs (HTTP endpoints, WebSocket, CLI)
- [ ] Check TLS/SSL configuration (protocols, ciphers, cert management)
- [ ] Review deployment access (SSH keys, service accounts, sudo)

**Key questions:**
- Can an unauthenticated user reach this?
- What's the blast radius if they can?
- Is there defense-in-depth, or is it one layer?

---

### Pass 2: Injection & Data Handling

**Focus:** How user input flows through the system and where it can escape its intended context.

Checklist:
- [ ] Trace all `subprocess` / shell execution — is `shell=True` used? Are inputs sanitized?
- [ ] Check SQL/NoSQL queries for injection (parameterized vs string formatting)
- [ ] Review XML/JSON/YAML parsing for injection (XXE, billion-laughs, unsafe deserialization)
- [ ] Check template rendering for XSS (server-side and client-side)
- [ ] Examine file operations for path traversal
- [ ] Review AI/LLM integrations for prompt injection (tool access, trust boundaries)
- [ ] Check WebSocket message construction for frame injection
- [ ] Look at error responses — do they leak internals? (`str(e)`, stack traces, paths)
- [ ] Validate input bounds: length limits, type coercion, range clamping

**Key questions:**
- Where does untrusted data cross a trust boundary?
- What's the most dangerous thing this input could become?
- Is the escaping/quoting appropriate for the destination context?

---

### Pass 3: Infrastructure & Operational Security

**Focus:** The system as a running service — how it's deployed, updated, monitored.

Checklist:
- [ ] Review systemd service configuration (sandboxing, capabilities, restart policy)
- [ ] Check secrets handling: storage, rotation, access scope, logging
- [ ] Examine backup/restore: encryption at rest, integrity verification, access control
- [ ] Review dependency management: pinned versions, lockfiles, hash verification
- [ ] Check CI/CD pipeline: secret exposure, action pinning, build isolation
- [ ] Review provisioning scripts: credential injection, heredoc safety, idempotency
- [ ] Look at logging: what's captured, what's exposed, rotation policy
- [ ] Assess update strategy: unattended upgrades, reboot policy
- [ ] Check file permissions on sensitive configs and scripts

**Key questions:**
- If this system is compromised, what's the lateral movement path?
- How quickly would you detect a breach?
- Can you rebuild this system from scratch without the live device?

---

### Pass 4: Subtle Attacks & Architectural Weaknesses

**Focus:** Attacks that exploit the design itself — race conditions, DNS tricks, supply chain, information disclosure.

Checklist:
- [ ] DNS rebinding: does the app validate Host headers?
- [ ] Race conditions: concurrent access to shared state (files, databases, in-memory)
- [ ] Supply chain: dependency confusion, typosquatting, unpinned transitive deps
- [ ] Information disclosure: status endpoints, version headers, debug payloads
- [ ] Timing attacks: do error paths take different time than success paths?
- [ ] Resource exhaustion: can a single request block the server? Consume all memory?
- [ ] Origin validation on WebSocket upgrade
- [ ] Cookie/session security: flags, lifetime, fixation
- [ ] Privilege boundaries: does the service user have more access than needed?
- [ ] Symlink attacks: does file traversal follow symlinks into unexpected places?

**Key questions:**
- What assumptions does the architecture make about its environment?
- What happens when two things happen simultaneously?
- What does an attacker learn just by observing responses?

---

### Pass 5: Logic Bugs & Amplification

**Focus:** How valid operations can be abused through volume, sequencing, or inconsistent validation.

Checklist:
- [ ] Batch/bulk endpoints: is there a cap on items per request?
- [ ] Whitelist vs. sanitize: are enum-like values (template names, action types) validated against a list, or just escaped?
- [ ] Inconsistent escaping: does the same type of input get different treatment in different code paths?
- [ ] Amplification: can one request trigger N expensive operations (API calls, subprocesses, file writes)?
- [ ] Error path information leakage: what gets returned to the client when parsing fails?
- [ ] Concurrent state mutations: shared files/data without locking (even JSON, not just databases)
- [ ] Graceful shutdown: what happens to in-flight operations on SIGTERM?
- [ ] Long-running requests: can a single request monopolize the server for minutes?
- [ ] Delete cascades: can destroying one resource leave orphaned references?
- [ ] Trust boundaries between AI and execution: does AI-generated output get validated before reaching dangerous APIs?

**Key questions:**
- What if I call this 1000 times in 1 second?
- Are all paths to this shell command equally protected?
- Does the error case reveal more than the success case?
- What breaks if two users press the button simultaneously?

---

## Techniques That Emerged from Practice

### The "Inconsistent Sibling" Pattern

One of the most productive techniques: find where the same type of input is handled in multiple places, then check if the protection is applied consistently.

In this audit, `description` got single-quote escaping but `template` (same function, same shell command pattern) got nothing. This happened because `template` was assumed to be from a known set — but the API accepts arbitrary strings.

**How to apply it:**
1. Find all calls to a dangerous function (e.g., `execute_command`)
2. For each call, trace back: where does each interpolated variable come from?
3. Check: is the same escaping applied to all of them?

### The "What If I Control This?" Walk

For every user-controllable input, walk it forward through the system:
1. Where does it enter? (HTTP body, URL param, WebSocket message)
2. Where does it get stored? (file, memory, database)
3. Where does it get rendered? (HTML, shell, XML, log)
4. Where does it cross a trust boundary? (client→server, server→subprocess, server→AI)

At each boundary, ask: "what if this value is `'; rm -rf / #`?" or `<script>alert(1)</script>`?" 

### The "Amplification Factor" Question

For every endpoint, calculate its cost:
- How many external API calls does it make?
- How many file I/O operations?
- How many subprocess spawns?
- How long can it block?

Then ask: can I multiply that cost by sending a batch, a loop, or N concurrent requests?

### Follow the Unquoted Variable

In shell-heavy systems, search for every f-string or format string that builds a command. For each variable in the string, check:
1. Is it quoted? (`'{var}'` vs `{var}`)
2. Is it escaped? (`shlex.quote(var)` or manual replacement)
3. Is it validated? (whitelist check before it reaches the string)

Variables that are "obviously" from a trusted source (like a template name from a dropdown) are the ones most likely to be unprotected — because the developer assumed the input would always be one of five known values.

### Diff the Deploy Path

Compare what's declared in config (requirements.txt, package.json) against what the deploy script actually installs. Version mismatches mean the production system runs code that was never tested.

### The "What Does a Curious Visitor See?" Test

Without any authentication, hit every endpoint that returns JSON and catalog what's exposed. Status endpoints, health checks, and error messages collectively form an information disclosure profile that aids further attacks.

---

## Tools & Techniques

### File Discovery (what to read first)

| Priority | Files | Why |
|----------|-------|-----|
| 1 | Entry points: `app.py`, `server.py`, `main.py` | All routes, middleware, config |
| 2 | Config: `.env.example`, `docker-compose.yml`, `Dockerfile` | Secrets, ports, volumes |
| 3 | Deploy: `Makefile`, deploy scripts, CI workflows | How code reaches production |
| 4 | Dependencies: `requirements.txt`, `package.json` | Supply chain surface |
| 5 | Provisioning: setup scripts, systemd units, nginx configs | OS-level exposure |
| 6 | Templates/Frontend: HTML, JS | Client-side injection vectors |

### Grep Patterns That Find Bugs

```bash
# Shell injection
subprocess.*shell=True
os.system|os.popen
eval\(|exec\(

# Missing auth
@app.route|@router  (then check: is there a decorator/middleware?)

# Info leaks
str(e)|traceback|exc_info
debug.*True|DEBUG

# Dangerous patterns
pickle|yaml.load|__import__
innerHTML|document.write|eval(
cors_allowed_origins.*\*

# Secrets
API_KEY|SECRET|PASSWORD|TOKEN  (in code, not .env)

# Unquoted shell interpolation (f-strings building commands)
f".*\{.*\}.*\{  (look for variables WITHOUT surrounding quotes)
execute_command|subprocess\.run  (trace every caller)

# Race conditions
\.write\(|\.write_text\(  (then check: is there a lock?)
threading\.Lock|flock|FileLock  (absence = problem)

# Amplification / unbounded
for.*in.*actions|for.*in.*items  (loop over user-supplied array — is it capped?)
max_iters|timeout|MAX_CONTENT_LENGTH  (absence = problem)

# Trust boundary crossings
json\.loads.*response  (AI response parsed and acted on without validation)
request\.get_json.*execute|request\.json.*command  (input → action without allowlist)
```

### What Good Looks Like

When you finish an audit and find nothing, you should see:
- Auth middleware on every route (or a `before_request` gate)
- `subprocess.run([...], shell=False)` everywhere
- `CORS(app, origins=[...])` with specific origins
- `MAX_CONTENT_LENGTH` set
- `defusedxml` for any XML parsing
- Pinned dependencies with hashes
- Systemd `NoNewPrivileges=yes` + `ProtectSystem=strict`
- Security headers in nginx or `@app.after_request`
- File locking on shared-state writes
- Generic error messages to clients, detailed logs server-side

---

## Severity Classification

| Level | Definition | Examples |
|-------|-----------|----------|
| **Critical** | Unauthenticated remote exploitation, data loss, or arbitrary code execution | No auth + public exposure, shell injection, wildcard CORS |
| **High** | Exploitable with low effort, meaningful impact, but requires some access or conditions | DNS rebinding, race conditions on critical files, plaintext credentials |
| **Medium** | Real vulnerability but limited blast radius or requires specific conditions | XML bombs, info disclosure, missing size limits, version leaks |
| **Low** | Defense-in-depth gaps, maintenance risks, or requires unlikely preconditions | Unpinned CI actions, innerHTML patterns, symlink edge cases |

---

## Reporting Structure

Each finding should include:
1. **Title** — one-line description
2. **Severity** — Critical / High / Medium / Low
3. **Location** — exact file and line number
4. **Description** — what's wrong and why it matters
5. **Exploit scenario** — how an attacker would use this (1-3 sentences)
6. **Recommendation** — specific code or config change with example
7. **Effort estimate** — hours to fix

Group findings by severity, not by pass number. The remediation plan should be a prioritized table sorted by `severity × effort` (high-severity quick-wins first).

---

## When to Stop

Diminishing returns set in around pass 4-5 for a project of this size (~6000 LOC). Signs you've reached adequate coverage:

- Each pass finds fewer issues than the previous
- New findings are Low severity or purely theoretical
- You're looking at the same files and not finding new patterns
- Your threat model is well-defined and all attack paths are documented

### Tracking Diminishing Returns

| Pass | Criticals Found | Highs Found | Total Findings |
|------|----------------|-------------|----------------|
| 1    | 3              | 4           | 15             |
| 2    | 0              | 1           | 10             |
| 3    | 0              | 1           | 10             |
| 4    | 0              | 2           | 8              |
| 5    | 1              | 0           | 7              |

Pass 5 still found a Critical (the unquoted template variable) — but it was the only one, and most other findings were Medium or Low. This is the inflection point. A sixth pass would likely yield only Low-severity or theoretical issues.

**Exception:** If pass 5 reveals a new *category* of vulnerability (not just another instance of a known pattern), consider a targeted sixth pass focusing on that category.

### Common Blind Spots (What Gets Missed on First Passes)

These patterns consistently survived early passes in our audit:

1. **Enum-like values assumed safe** — Template names, action types, service names. Developers treat them as constants but APIs accept arbitrary strings.
2. **Inconsistent protection across similar code paths** — The first instance gets sanitized, copies/variants don't.
3. **Batch/bulk endpoints** — Single-request cost is reasonable; 1000x cost is not checked.
4. **Error path leakage** — Developers test the happy path. The error path returns raw exceptions.
5. **File locking** — Easy to miss because single-user testing never triggers the race.
6. **The "trusted" AI response** — AI output is treated as trusted data, but prompt injection makes it attacker-controlled.
7. **Deploy script drift** — The script is written once, requirements.txt evolves, they diverge silently.

For production systems handling sensitive data or public traffic, consider additionally:
- Automated DAST scanning (OWASP ZAP, nuclei)
- Dependency vulnerability scanning in CI (`pip-audit`, `npm audit`, `trivy`)
- Manual penetration testing by a specialist
- Runtime monitoring (fail2ban, intrusion detection)

---

## Post-Audit Workflow

1. **Prioritize:** Sort by `impact × exploitability / effort`. Quick wins first.
2. **Branch:** Create a `security/hardening` branch. Don't mix with feature work.
3. **Implement in batches:** Group related fixes (e.g., all auth changes together).
4. **Test:** Each fix should be verifiable — write a test or document a manual check.
5. **Document:** Update the audit doc to mark completed items.
6. **Schedule re-audit:** Set a calendar reminder (quarterly for active projects).

---

## Checklist: Minimum Viable Security for a Self-Hosted Service

Before going live, confirm:

- [ ] Every HTTP endpoint requires authentication (even "internal" ones)
- [ ] CORS is restricted to known origins
- [ ] User input never reaches a shell without `shlex.quote()` or argument lists
- [ ] Request body size is bounded (`MAX_CONTENT_LENGTH`)
- [ ] Error responses don't leak filesystem paths, stack traces, or config
- [ ] Dependencies are pinned with integrity hashes
- [ ] TLS is configured for all external traffic
- [ ] The service runs as a non-root user with minimal filesystem access
- [ ] Secrets are in environment variables, not code
- [ ] There's a way to know if someone is abusing the system (logs, rate limits)

---

*This methodology was developed iteratively through a real five-pass audit. Adapt the checklists to your stack — the pass structure and mental models transfer across any tech.*
