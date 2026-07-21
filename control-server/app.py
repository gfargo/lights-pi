#!/usr/bin/env python3
"""
Natural Language Lighting Control Server
Interprets natural language commands and adjusts QLC+ workspace in real-time
Also provides direct fixture/group controls with QLC+ WebSocket integration
"""

import asyncio
import concurrent.futures
import contextlib
import hmac
import json
import logging
import math
import os
import queue
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import wave
import xml.etree.ElementTree as ET
from datetime import timedelta
from pathlib import Path

import structlog
import websockets
from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    stream_with_context,
)
from flask_cors import CORS
from flask_socketio import SocketIO

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Local QLC+ fixture definition parser (.qxf)
sys.path.insert(0, str(Path(__file__).parent))
import chat_store
import fixture_definitions
import midi_engine
from audio_engine import _engine as _audio_engine
from audio_engine import bpm_to_interval_ms
from event_bus import EventBus, format_sse, parse_filter
from osc_backend import (
    OscConfig,
    OscStateEmitter,
    build_udp_client,
    drain_event_bus,
    start_listener,
)

# ---------------------------------------------------------------------------
# Structured logging
# LOG_FORMAT=json (default) → JSON lines for journald/prod
# LOG_FORMAT=console        → human-readable for local dev
# LOG_LEVEL=DEBUG|INFO|WARNING|ERROR (default INFO)
# ---------------------------------------------------------------------------
_LOG_LEVEL_STR = os.getenv("LOG_LEVEL", "INFO").upper()
_LOG_FORMAT = os.getenv("LOG_FORMAT", "json").lower()
_LOG_LEVEL_INT = getattr(logging, _LOG_LEVEL_STR, logging.INFO)

logging.basicConfig(format="%(message)s", stream=sys.stdout, level=_LOG_LEVEL_INT)

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.dev.ConsoleRenderer()
        if _LOG_FORMAT == "console"
        else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(_LOG_LEVEL_INT),
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

log = structlog.get_logger("lights")

app = Flask(__name__)

# ---------------------------------------------------------------------------
# SSE event bus — module-level singleton
# ---------------------------------------------------------------------------
EVENT_BUS = EventBus()
START_TIME = time.time()


def _emit(event_type: str, data: dict) -> None:
    """Convenience wrapper: publish *data* as *event_type* to all SSE clients."""
    EVENT_BUS.publish(event_type, data)

# ---------------------------------------------------------------------------
# Security hardening (quick wins from security audit)
# ---------------------------------------------------------------------------

# 1. Restrict CORS to known origins (audit item #3)
_ALLOWED_ORIGINS = [
    "http://lights.local",
    "http://lights.local:5000",
    "https://lights.local",
    f"http://localhost:{os.getenv('CONTROL_PORT', '5000')}",
    "http://localhost:5000",
    "http://127.0.0.1:5000",
]
# Allow Tailscale hostname (default to known tailnet, override via env)
_ts_host = os.getenv("TAILSCALE_HOST", "riversway-lights.tailb82ead.ts.net")
if _ts_host:
    _ALLOWED_ORIGINS.append(f"http://{_ts_host}")
    _ALLOWED_ORIGINS.append(f"http://{_ts_host}:5000")
    _ALLOWED_ORIGINS.append(f"https://{_ts_host}")
# Allow PI_HOST if set (covers direct-IP access)
_pi_host = os.getenv("PI_HOST", "")
if _pi_host and _pi_host not in ("lights.local", _ts_host):
    _ALLOWED_ORIGINS.append(f"http://{_pi_host}")
    _ALLOWED_ORIGINS.append(f"http://{_pi_host}:5000")

CORS(app, origins=_ALLOWED_ORIGINS)
socketio = SocketIO(app, cors_allowed_origins=_ALLOWED_ORIGINS)


@socketio.on("connect")
def _on_socket_connect():
    """Reject the handshake if a password is configured and the session isn't
    authenticated; otherwise send current audio engine state to the browser."""
    if LIGHTS_PASSWORD is not None and not session.get("authed"):
        return False
    socketio.emit("audio_state", _audio_engine.get_state())


# 2. Set SECRET_KEY for session signing (audit item #21)
app.config["SECRET_KEY"] = os.getenv(
    "FLASK_SECRET_KEY",
    os.urandom(32).hex(),  # random per restart if not configured
)

# 3. Limit request body size to 1MB (audit item #8)
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1MB

# 4. Shared-password auth (issue #25). Unset LIGHTS_PASSWORD == open mode,
# preserving backwards compat for existing rigs that don't opt in.
LIGHTS_PASSWORD = os.getenv("LIGHTS_PASSWORD", "").strip() or None
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_LOCKOUT_S = 60
_LOGIN_ATTEMPTS: dict[str, list[float]] = {}

_AUTH_EXEMPT_PATHS = {"/login", "/healthz", "/manifest.json", "/icon.svg", "/sw.js", "/logo"}


def _verify_password(supplied: str, expected: str | None) -> bool:
    """Constant-time password compare. False if no password is configured."""
    if not expected:
        return False
    return hmac.compare_digest(supplied, expected)


def _login_rate_check(state: dict, ip: str, now: float) -> tuple[bool, int]:
    """Pure rate limiter: 5 failed attempts per IP within 60s locks it out.

    *state* is a dict mapping ip -> list of failure timestamps; the caller
    appends a new timestamp on each failed attempt. Returns
    (allowed, retry_after_s).
    """
    attempts = [t for t in state.get(ip, []) if now - t < _LOGIN_LOCKOUT_S]
    if len(attempts) >= _LOGIN_MAX_ATTEMPTS:
        retry_after = int(_LOGIN_LOCKOUT_S - (now - attempts[0]))
        return False, max(retry_after, 1)
    return True, 0


def _is_auth_exempt(path: str) -> bool:
    """Routes reachable without a session — login itself, static assets, and
    /healthz so the systemd watchdog keeps working."""
    return path in _AUTH_EXEMPT_PATHS or path.startswith("/static/")


@app.before_request
def _require_auth():
    """Gate every non-exempt route behind the session cookie when a shared
    password is configured. No-op entirely in open mode (LIGHTS_PASSWORD unset)."""
    if LIGHTS_PASSWORD is None:
        return None
    if _is_auth_exempt(request.path) or session.get("authed"):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "unauthorized"}), 401
    return redirect("/login")


@app.after_request
def _security_headers(response):
    """Strip version info and add basic security headers (audit items #29, #13)."""
    response.headers.pop("Server", None)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

# Configuration
SCRIPT_DIR = Path(__file__).parent.parent
LIGHTSCTL = SCRIPT_DIR / "lightsctl.sh"

# ---------------------------------------------------------------------------
# Mock DMX mode — set MOCK_DMX=1 to run without QLC+/ENTTEC hardware
# ---------------------------------------------------------------------------
MOCK_DMX: bool = os.getenv("MOCK_DMX", "").strip().lower() in ("1", "true", "yes")

if MOCK_DMX:
    import mock_dmx as _mock_dmx
    print("⚠  MOCK_DMX mode enabled — no QLC+ WebSocket will be opened")

# Default to ~/.qlcplus/default.qxw, but can be overridden via env var.
# In mock mode, fall back to a scratch copy of the bundled sample workspace when no
# real one exists — writes must never land on the git-tracked fixture (see #66).
_default_ws = Path.home() / ".qlcplus" / "default.qxw"
if MOCK_DMX and not _default_ws.exists() and not os.getenv("QLC_WORKSPACE"):
    _fixture_ws = Path(__file__).parent / "tests" / "fixtures" / "sample.qxw"
    # Namespaced by uid so concurrent MOCK_DMX sessions from different users on a
    # shared host don't clobber each other's scratch workspace (see #66 review).
    _scratch_dir = Path(tempfile.gettempdir()) / f"lights-pi-mock-{os.getuid()}"
    _scratch_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.mkdir(_scratch_dir, mode=0o700)
    except FileExistsError as exc:
        # Refuse to reuse a pre-existing path unless it's a plain directory we
        # own — on a shared host an attacker who knows our uid could pre-plant
        # a symlink at this predictable location to redirect workspace writes
        # (see #66 review: mkdir(exist_ok=True) was symlink-attack prone).
        if _scratch_dir.is_symlink() or not _scratch_dir.is_dir() or _scratch_dir.stat().st_uid != os.getuid():
            raise RuntimeError(
                f"refusing to use MOCK_DMX scratch dir {_scratch_dir}: it exists but "
                "is not a plain directory owned by the current user"
            ) from exc
    _scratch_ws = _scratch_dir / "sample.qxw"
    _persist = os.getenv("MOCK_DMX_PERSIST", "").strip().lower() in ("1", "true", "yes")
    if not (_persist and _scratch_ws.exists()):
        shutil.copyfile(_fixture_ws, _scratch_ws)
    WORKSPACE_PATH = _scratch_ws
    print(f"⚠  MOCK_DMX fallback workspace → {WORKSPACE_PATH} (copied from bundled fixture)")
else:
    WORKSPACE_PATH = Path(os.getenv("QLC_WORKSPACE", str(_default_ws)))

GROUPS_FILE = Path.home() / ".qlcplus" / "fixture_groups.json"
CUE_LISTS_FILE = Path.home() / ".qlcplus" / "cue_lists.json"
AUDIO_CHASES_FILE = Path.home() / ".qlcplus" / "audio_chases.json"
CUE_AUDIO_DIR = Path.home() / ".qlcplus" / "audio"
STAGE_LAYOUT_FILE = Path.home() / ".qlcplus" / "stage_layout.json"
MIDI_MAPPINGS_FILE = Path.home() / ".qlcplus" / "midi_mappings.json"

# Registry of audio-BPM-driven chases currently running.
# Shape: { chase_key: { 'task': concurrent.futures.Future, 'react_to': str } }
_active_audio_chases: dict[str, dict] = {}
_active_audio_chases_lock = threading.Lock()

RF_SETTINGS_FILE = Path.home() / ".qlcplus" / "rf_settings.json"
CHAT_DB_PATH = Path(os.getenv("CHAT_DB_PATH", str(Path.home() / ".qlcplus" / "chat_history.db")))
CHAT_SUMMARIZE_EVERY = int(os.getenv("CHAT_SUMMARIZE_EVERY", "20"))

# Serializes every workspace read-modify-write cycle (scene/chase saves, tempo
# updates, id generation). RLock because route handlers hold it across id-gen
# + inject, and the inject helpers re-acquire it themselves.
_WORKSPACE_LOCK = threading.RLock()


def _atomic_write_tree(tree: ET.ElementTree) -> None:
    """Write an ElementTree to WORKSPACE_PATH atomically (tmp file + os.replace).

    Callers must hold _WORKSPACE_LOCK. Writing to a temp file in the same
    directory and replacing it keeps concurrent readers from ever observing
    a torn/truncated .qxw.
    """
    d = WORKSPACE_PATH.parent
    fd, tmp = tempfile.mkstemp(prefix=".qlc-ws-", suffix=".qxw", dir=str(d))
    try:
        with os.fdopen(fd, "wb") as fh:
            tree.write(fh, encoding="UTF-8", xml_declaration=True)
        with contextlib.suppress(OSError):
            shutil.copymode(WORKSPACE_PATH, tmp)  # mkstemp defaults to 0600; keep the original mode
        os.replace(tmp, str(WORKSPACE_PATH))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


# Scene swatch cache: {scene_id: swatch_data_uri}; cleared when workspace mtime changes.
_scene_swatch_cache: dict = {}
_scene_swatch_cache_mtime: float = 0.0

# Server-side tap-tempo chase runners: str(chase_id) -> {'step_ms': float, 'running': bool}
# Each entry corresponds to a background asyncio task on _qlc_loop stepping through scenes.
_tap_runners: dict = {}

# QLC+ WebSocket configuration
QLC_HOST = os.getenv("QLC_HOST", "localhost")
QLC_PORT = int(os.getenv("QLC_PORT", "9999"))
QLC_WS_URL = f"ws://{QLC_HOST}:{QLC_PORT}/qlcplusWS"

# AI Configuration from environment
AI_PROVIDER = os.getenv("AI_PROVIDER", "openai")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv(
    "AI_MODEL",
    "gpt-4.1" if os.getenv("AI_PROVIDER", "openai") == "openai" else "claude-3-5-sonnet-20241022",
)

# Per-provider API keys — prefer explicit per-provider env vars; fall back to
# AI_API_KEY when it belongs to the primary provider.
_OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or (AI_API_KEY if AI_PROVIDER == "openai" else "")
_ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY") or (AI_API_KEY if AI_PROVIDER == "anthropic" else "")

# Per-provider model overrides. The secondary-provider fallbacks use fresh model IDs
# so the stale claude-3-5 name doesn't silently carry over when anthropic is added as
# a failover target to an openai-primary install.
_OPENAI_MODEL = os.getenv("OPENAI_MODEL") or (AI_MODEL if AI_PROVIDER == "openai" else "gpt-4.1")
_ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL") or (AI_MODEL if AI_PROVIDER == "anthropic" else "claude-sonnet-4-6")


def _parse_failover_chain(raw: str, default_provider: str) -> list:
    """Parse AI_PROVIDER_FAILOVER into an ordered, de-duped list of valid providers.

    Valid providers: 'anthropic', 'openai'. Unknowns (e.g. 'ollama') are dropped.
    An empty/missing value returns [default_provider] if it is valid, else [].
    """
    _VALID = {"anthropic", "openai"}
    if not raw or not raw.strip():
        return [default_provider] if default_provider in _VALID else []
    seen: list = []
    for part in raw.split(","):
        p = part.strip().lower()
        if p in _VALID and p not in seen:
            seen.append(p)
    return seen if seen else ([default_provider] if default_provider in _VALID else [])


def _provider_config(provider: str) -> tuple:
    """Return (model, api_key) for the given provider."""
    if provider == "anthropic":
        return _ANTHROPIC_MODEL, _ANTHROPIC_API_KEY
    if provider == "openai":
        return _OPENAI_MODEL, _OPENAI_API_KEY
    return "", ""


# Ordered failover chain resolved once at startup.
AI_PROVIDER_FAILOVER = os.getenv("AI_PROVIDER_FAILOVER", "")
_AI_FAILOVER_CHAIN: list = _parse_failover_chain(AI_PROVIDER_FAILOVER, AI_PROVIDER)

# ----------------------------------------------------------------------------
# Circuit breaker — per-provider transient-failure back-off
# ----------------------------------------------------------------------------
_BREAKER_THRESHOLD = 3      # failures before opening
_BREAKER_COOLDOWN_S = 60    # seconds to back off after opening
_provider_breaker: dict = {}  # {provider: {"fails": int, "open_until": float}}
_breaker_lock = threading.Lock()


def _breaker_is_open(state: dict, provider: str, now: float) -> bool:
    info = state.get(provider)
    return info is not None and info.get("open_until", 0.0) > now


def _breaker_record_failure(state: dict, provider: str, now: float) -> None:
    info = state.setdefault(provider, {"fails": 0, "open_until": 0.0})
    info["fails"] += 1
    if info["fails"] >= _BREAKER_THRESHOLD:
        info["open_until"] = now + _BREAKER_COOLDOWN_S


def _breaker_record_success(state: dict, provider: str) -> None:
    state.pop(provider, None)


SERVICE_NAME = os.getenv("SERVICE", "qlcplus-web.service")

# Initialise the chat history database on startup (idempotent).
chat_store.init_db(CHAT_DB_PATH)


def _is_local():
    """Detect whether we're running on the same host as QLC+.

    Returns True when QLC_HOST resolves to a loopback address or to one of
    this machine's own IPs, meaning we can use local systemctl / file
    operations instead of SSH.
    """
    if QLC_HOST in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        target_ip = socket.gethostbyname(QLC_HOST)
    except socket.gaierror:
        return False
    if target_ip.startswith("127."):
        return True
    # Check if the resolved IP belongs to one of our own interfaces
    try:
        local_ip = socket.gethostbyname(socket.gethostname())
        if target_ip == local_ip:
            return True
    except socket.gaierror:
        pass
    # Also compare against the hostname set in PI_HOSTNAME / HOSTNAME
    try:
        pi_hostname = os.getenv("PI_HOSTNAME", "")
        if pi_hostname and socket.gethostname().lower().startswith(pi_hostname.lower()):
            return True
    except Exception:
        pass
    return False


IS_LOCAL = _is_local()

# ---------------------------------------------------------------------------
# QLC+ WebSocket — single persistent connection on a background event loop
# ---------------------------------------------------------------------------
# QLC+ 4.14.1 has a hard limit (~50) on concurrent WebSocket clients. Each
# call to websockets.connect() leaves the underlying TCP socket in CLOSE_WAIT
# when QLC+ closes its end (transport.abort() doesn't flush the FIN). Within
# minutes the limit is exhausted and new handshakes silently time out.
#
# The fix: maintain ONE long-lived WebSocket on a dedicated asyncio loop in a
# background thread. All Flask requests dispatch sends to that loop via a
# thread-safe call. The connection auto-reconnects if QLC+ drops it.

_qlc_loop: asyncio.AbstractEventLoop = None  # type: ignore
_qlc_loop_thread: threading.Thread = None  # type: ignore
_qlc_ws = None  # the actual websocket connection (lives on _qlc_loop)
_qlc_ws_lock: asyncio.Lock = None  # type: ignore
_qlc_pending_responses = {}  # request_id -> Future for QLC+API replies
_last_dmx_write_ts: float = None  # type: ignore  # set after each successful send
_ws_reconnect_count: int = 0  # monotonic count of successful (re)connections


def _start_qlc_loop():
    """Start the dedicated background event loop used for QLC+ comms."""
    global _qlc_loop, _qlc_loop_thread, _qlc_ws_lock

    if _qlc_loop is not None and _qlc_loop.is_running():
        return

    ready = threading.Event()

    def _run_loop():
        global _qlc_loop, _qlc_ws_lock
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _qlc_loop = loop
        _qlc_ws_lock = asyncio.Lock()
        ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()

    _qlc_loop_thread = threading.Thread(target=_run_loop, daemon=True, name="qlc-ws-loop")
    _qlc_loop_thread.start()
    ready.wait(timeout=5)


def _qlc_run(coro, timeout=10):
    """Submit a coroutine to the QLC+ background loop and wait for the result."""
    if _qlc_loop is None:
        _start_qlc_loop()
    future = asyncio.run_coroutine_threadsafe(coro, _qlc_loop)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        future.cancel()
        raise


async def _ensure_qlc_ws():
    """Open the persistent WebSocket if needed. Lock-protected."""
    global _qlc_ws, _ws_reconnect_count
    # In mock mode, return a MockQLCWebSocket immediately (no real connection).
    if MOCK_DMX:
        if _qlc_ws is None:
            _qlc_ws = _mock_dmx.MockQLCWebSocket()
        return _qlc_ws
    async with _qlc_ws_lock:
        # Treat both "missing" and "closed" cases as needing reconnect
        needs_connect = _qlc_ws is None
        if not needs_connect:
            try:
                # websockets >=10 exposes .closed; older uses .open
                if getattr(_qlc_ws, "closed", False):
                    needs_connect = True
            except Exception:
                needs_connect = True
        if needs_connect:
            # Make sure the previous connection (if any) is fully torn down
            # before opening a new one — otherwise the old TCP socket sits in
            # CLOSE_WAIT until garbage collection runs, eventually exhausting
            # QLC+'s connection slot pool.
            old_ws = _qlc_ws
            _qlc_ws = None
            if old_ws is not None:
                try:
                    await asyncio.wait_for(old_ws.close(), timeout=1.0)
                except Exception:
                    pass
            try:
                _qlc_ws = await websockets.connect(
                    QLC_WS_URL,
                    open_timeout=3,
                    close_timeout=1,
                    ping_interval=20,
                    ping_timeout=10,
                    max_size=2 ** 20,
                )
                # Start a background reader so QLC+ pushes don't fill the recv buffer
                asyncio.create_task(_qlc_reader(_qlc_ws))
                _ws_reconnect_count += 1
                log.info("qlc_ws_connected", url=QLC_WS_URL, reconnect_count=_ws_reconnect_count)
                _emit("qlc_reconnect", {"url": QLC_WS_URL})
            except Exception as e:
                _qlc_ws = None
                log.error("qlc_ws_connect_failed", error_type=type(e).__name__, error=str(e))
                raise
        return _qlc_ws


async def _qlc_reader(ws):
    """Continuously drain incoming messages, dispatching API replies to waiters.

    Takes the websocket as an explicit argument so we don't race against
    reassignment of the global. When the connection drops, we explicitly close
    it to avoid leaking the underlying TCP socket into CLOSE_WAIT.
    """
    global _qlc_ws
    try:
        async for msg in ws:
            # Dispatch QLC+API responses to any pending request waiting on it
            for key, fut in list(_qlc_pending_responses.items()):
                if key in msg and not fut.done():
                    fut.set_result(msg)
                    _qlc_pending_responses.pop(key, None)
                    break
    except Exception as e:
        log.warning("qlc_ws_reader_exited", error_type=type(e).__name__, error=str(e))
    finally:
        # Drop the global reference so the next caller reconnects, and
        # explicitly close the underlying connection so the OS frees the FD
        # and QLC+ reclaims the slot — without this, sockets pile up in
        # CLOSE_WAIT for ~minutes until Python GC runs the destructor.
        try:
            await asyncio.wait_for(ws.close(), timeout=1.0)
        except Exception:
            pass
        if _qlc_ws is ws:
            _qlc_ws = None
            _emit("qlc_disconnect", {"url": QLC_WS_URL})


async def _qlc_send_commands(commands):
    """Send one or more raw QLC+ commands over the persistent WebSocket."""
    global _last_dmx_write_ts
    if MOCK_DMX:
        _mock_dmx.apply_commands(commands)
        _last_dmx_write_ts = time.time()
        return
    ws = await _ensure_qlc_ws()
    async with _qlc_ws_lock:
        for command in commands:
            log.debug("dmx_frame", command=command)
            await ws.send(command)
    _last_dmx_write_ts = time.time()


async def _qlc_request_reply(command, response_marker, timeout=2.0):
    """Send a command and wait for a response containing response_marker."""
    if MOCK_DMX:
        # Synthesize replies for the two QLC+API commands app.py uses.
        if response_marker == "getChannelsValues":
            # Parse max_ch from the command "QLC+API|getChannelsValues|1|1|<max>"
            try:
                max_ch = int(command.rsplit("|", 1)[-1])
            except (ValueError, IndexError):
                max_ch = 512
            return _mock_dmx.serialize_get_channels_values(max_ch)
        # setFunctionStatus — mock simply acknowledges
        return f"QLC+API|{response_marker}|OK"
    ws = await _ensure_qlc_ws()
    fut = asyncio.get_running_loop().create_future()
    _qlc_pending_responses[response_marker] = fut
    try:
        async with _qlc_ws_lock:
            await ws.send(command)
        return await asyncio.wait_for(fut, timeout=timeout)
    finally:
        _qlc_pending_responses.pop(response_marker, None)


# ---------------------------------------------------------------------------
# Public API used by the rest of app.py
# ---------------------------------------------------------------------------

# Global QLC+ WebSocket connection (legacy reference — no longer used directly,
# kept for any external code that imports it)
qlc_websocket = None


async def connect_to_qlc():
    """Legacy stub kept for compatibility — uses the persistent connection."""
    try:
        await _ensure_qlc_ws()
        return True
    except Exception:
        return False


async def send_qlc_command(command):
    """Send a single command to QLC+ via the persistent WebSocket."""
    try:
        await _qlc_send_commands([command])
        return True
    except Exception as e:
        log.error("qlc_command_failed", error=str(e))
        return False


def set_channel_value(universe, address, value):
    """Set a single DMX channel via QLC+."""
    absolute_address = (universe * 512) + address
    return set_channel_values([(absolute_address, value)])


def _run_async(coro):
    """Compatibility shim. New code should use _qlc_run() for QLC+ work."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def send_qlc_commands(commands):
    """Send one or more QLC+ commands. Uses the persistent connection."""
    try:
        await _qlc_send_commands(commands)
        return True
    except Exception as e:
        log.error("qlc_commands_failed", error=str(e))
        return False


def set_channel_values(channel_values):
    """Set absolute QLC+ channel values via the persistent WebSocket.

    Args:
        channel_values: iterable of (absolute_channel, value), both 1-based / 0-255
    """
    commands = []
    for channel, value in channel_values:
        try:
            ch = int(channel)
            val = max(0, min(255, int(value)))
        except (TypeError, ValueError):
            continue
        if ch > 0:
            commands.append(f"CH|{ch}|{val}")
    if not commands:
        return True
    try:
        _qlc_run(_qlc_send_commands(commands), timeout=5)
        # Emit a single coalesced channel_change event (list payload avoids
        # flooding the stream when fades/strobe call us repeatedly).
        _emit("channel_change", {
            "channels": [{"channel": ch, "value": val}
                         for cmd in commands
                         for ch, val in [cmd.split("|")[1:3]]
                         for ch, val in [(int(ch), int(val))]]
        })
        return True
    except Exception as e:
        log.error("set_channel_values_failed", error=str(e))
        return False


def _workspace_root():
    tree = ET.parse(WORKSPACE_PATH)
    return tree.getroot()


def _fixture_elements(root):
    ns = {'qlc': 'http://www.qlcplus.org/Workspace'}
    return root.findall(".//qlc:Fixture", ns)


def _fixture_to_dict(fixture):
    ns = {'qlc': 'http://www.qlcplus.org/Workspace'}
    def text(tag, default=""):
        elem = fixture.find(f"qlc:{tag}", ns)
        return elem.text if elem is not None and elem.text is not None else default

    return {
        "id": int(text("ID", "0")),
        "name": text("Name"),
        "universe": int(text("Universe", "0")),
        "address": int(text("Address", "0")),
        "channels": int(text("Channels", "1")),
        "manufacturer": text("Manufacturer"),
        "model": text("Model"),
        "mode": text("Mode"),
    }


def get_workspace_fixtures(root=None):
    """Return fixture metadata from the configured workspace."""
    if root is None:
        if not WORKSPACE_PATH.exists():
            return []
        root = _workspace_root()
    return [_fixture_to_dict(f) for f in _fixture_elements(root)]


def _engine_element(root):
    ns = {'qlc': 'http://www.qlcplus.org/Workspace'}
    engine = root.find("qlc:Engine", ns)
    if engine is not None:
        return engine
    return root.find("Engine")


def _iter_scene_functions(engine):
    """Yield (id, element) for each real Engine scene function."""
    ns = "http://www.qlcplus.org/Workspace"
    for func in engine.findall(f"{{{ns}}}Function") + engine.findall("Function"):
        if func.get("Type") != "Scene":
            continue
        fid = func.get("ID")
        if not fid or not fid.isdigit():
            continue
        yield int(fid), func


def get_workspace_scenes(root=None):
    """Return real Engine scene functions, excluding Virtual Console references."""
    if root is None:
        if not WORKSPACE_PATH.exists():
            return []
        root = _workspace_root()
    engine = _engine_element(root)
    if engine is None:
        return []

    return [
        {
            "id": fid,
            "name": func.get("Name", f"Scene {fid}"),
            "path": func.get("Path", ""),
            "fixture_values": len(_find_children(func, "FixtureVal")),
        }
        for fid, func in _iter_scene_functions(engine)
    ]


def get_next_scene_id():
    """Return next available scene/function ID from Engine functions only."""
    scenes = get_workspace_scenes()
    if not scenes:
        return 0
    return max(scene["id"] for scene in scenes) + 1


def _find_children(element, tag):
    ns = "http://www.qlcplus.org/Workspace"
    return element.findall(f"{{{ns}}}{tag}") + element.findall(tag)


def _find_scene_element(scene_id_or_name):
    root = _workspace_root()
    engine = _engine_element(root)
    if engine is None:
        return None
    needle = str(scene_id_or_name).strip().lower()
    ns = "http://www.qlcplus.org/Workspace"
    for func in engine.findall(f"{{{ns}}}Function") + engine.findall("Function"):
        if func.get("Type") != "Scene":
            continue
        fid = func.get("ID", "")
        name = func.get("Name", "")
        if fid == str(scene_id_or_name) or name.lower() == needle:
            return func
    return None


def _scene_root_from_xml(scene_xml):
    scene_xml = scene_xml.strip()
    if not scene_xml:
        raise ValueError("Empty scene XML")
    return ET.fromstring(scene_xml)


def _decode_fixture_val_pairs(pairs, channel_count):
    """Decode raw FixtureVal (channel, value) pairs into 0-based offsets.

    QLC+ stores FixtureVal channels 0-based natively, but some historical
    hand-authored scenes in this project used 1-based channels. The base
    can't be reliably guessed per-scene from sparse data alone, so we anchor
    the decision in facts knowable from the fixture definition:

    - Any channel == 0 can only occur in 0-based data (1-based data never
      contains 0) -> 0-based.
    - Else, any channel == channel_count can only occur in 1-based data (a
      0-based offset's max is channel_count - 1) -> 1-based.
    - Otherwise the data is ambiguous; default to 0-based, matching QLC+'s
      native format and the scenes this heuristic most commonly sees.
    """
    if any(channel == 0 for channel, _ in pairs):
        one_based = False
    elif channel_count and any(channel == channel_count for channel, _ in pairs):
        one_based = True
    else:
        one_based = False

    shift = 1 if one_based else 0
    return [(channel - shift, value) for channel, value in pairs]


def scene_to_channel_values(scene_root):
    """Convert a QLC+ scene Function element to absolute channel/value pairs."""
    fixtures = {str(f["id"]): f for f in get_workspace_fixtures()}
    updates = []

    for fixture_val in _find_children(scene_root, "FixtureVal"):
        fixture_id = fixture_val.get("ID")
        fixture = fixtures.get(str(fixture_id))
        if not fixture or not fixture_val.text:
            continue

        raw_parts = [p.strip() for p in fixture_val.text.split(",") if p.strip() != ""]
        pairs = []
        for i in range(0, len(raw_parts) - 1, 2):
            try:
                pairs.append((int(raw_parts[i]), int(raw_parts[i + 1])))
            except ValueError:
                continue
        if not pairs:
            continue

        for offset, value in _decode_fixture_val_pairs(pairs, fixture["channels"]):
            if offset < 0 or offset >= fixture["channels"]:
                continue
            absolute_channel = fixture["universe"] * 512 + fixture["address"] + offset + 1
            updates.append((absolute_channel, value))

    return updates


def apply_scene_xml_live(scene_xml):
    """Apply generated scene XML immediately via WebSocket channel updates."""
    scene_root = _scene_root_from_xml(scene_xml)
    updates = scene_to_channel_values(scene_root)
    if not updates:
        return {
            "success": False,
            "output": "",
            "error": "Scene has no applicable FixtureVal channel values",
        }
    success = set_channel_values(updates)
    return {
        "success": success,
        "output": f"Applied {len(updates)} channel values live via WebSocket",
        "error": "" if success else "Failed to apply channel values via WebSocket",
    }


def apply_existing_scene_live(scene_id_or_name):
    scene = _find_scene_element(scene_id_or_name)
    if scene is None:
        return {
            "success": False,
            "output": "",
            "error": f"Scene not found: {scene_id_or_name}",
        }
    updates = scene_to_channel_values(scene)
    if not updates:
        return {
            "success": False,
            "output": "",
            "error": f"Scene has no applicable channel values: {scene.get('Name', scene_id_or_name)}",
        }
    success = set_channel_values(updates)
    scene_name = scene.get("Name", str(scene_id_or_name))
    if success:
        _emit("scene_activated", {
            "scene_id": scene.get("ID"),
            "scene_name": scene_name,
        })
    return {
        "success": success,
        "output": f"Applied scene '{scene_name}' live via WebSocket ({len(updates)} channel values)",
        "error": "" if success else f"Failed to apply scene '{scene_name}' via WebSocket",
    }


async def _fetch_channel_values(max_ch):
    """Fetch live channel values via the persistent QLC+ WebSocket."""
    values = {}
    try:
        msg = await _qlc_request_reply(
            f"QLC+API|getChannelsValues|1|1|{max_ch}",
            response_marker="getChannelsValues",
            timeout=2.0,
        )
    except (TimeoutError, Exception) as e:
        log.warning("channel_values_fetch_failed", error=str(e))
        return values

    parts = msg.split("|")
    # QLC+ 4.14.1 live response starts channel/value groups at index 2:
    # QLC+API|getChannelsValues|1|0||2|0||...
    for i in range(2, len(parts) - 1, 3):
        try:
            values[int(parts[i])] = int(parts[i + 1])
        except (ValueError, IndexError):
            continue
    return values


def get_current_channel_values(max_ch=None):
    if max_ch is None:
        max_ch = 32
        for fixture in get_workspace_fixtures():
            max_ch = max(max_ch, fixture["universe"] * 512 + fixture["address"] + fixture["channels"])
    try:
        return _qlc_run(_fetch_channel_values(max_ch), timeout=4)
    except Exception as e:
        log.warning("channel_values_fetch_failed", error=str(e))
        return {}


# ----------------------------------------------------------------------------
# Boot-time look restore (issue: unattended reboot = blackout)
#
# A fresh boot initializes every DMX channel to 0, so a self-reboot (watchdog
# reset, power blip) leaves the venue dark even though everything recovered.
# The server keeps a rolling snapshot of the last non-blackout look and, on a
# fresh boot where output is still all-zero, re-applies it.
#
# Deliberate behaviors:
#   - Blackouts are never saved: the file always holds the last *lit* look,
#     so a reboot right after an intentional blackout will bring lights back.
#   - Restore only runs within BOOT_RESTORE_MAX_UPTIME_S of kernel boot —
#     a plain service restart (deploy) never re-applies stale state.
#   - Restore requires positive evidence of blackout (a non-empty all-zero
#     read); a failed/empty QLC+ fetch is retried, never treated as dark.
# ----------------------------------------------------------------------------

LAST_LOOK_FILE = Path.home() / ".qlcplus" / "last_look.json"
BOOT_RESTORE_ENABLED = os.getenv("BOOT_RESTORE", "1").lower() not in ("0", "false", "no")
BOOT_RESTORE_MAX_UPTIME_S = 600
LAST_LOOK_SAVE_INTERVAL_S = 10


def _proc_uptime_seconds() -> float | None:
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except Exception:
        return None


def _parse_last_look(text: str) -> dict[int, int]:
    """Parse a saved look file into {channel: value}. Invalid entries are
    dropped; anything unparseable yields {}."""
    try:
        data = json.loads(text)
        raw = data.get("values", {})
    except (ValueError, AttributeError):
        return {}
    values = {}
    for ch, val in raw.items() if isinstance(raw, dict) else []:
        try:
            c, v = int(ch), int(val)
        except (TypeError, ValueError):
            continue
        if c > 0:
            values[c] = max(0, min(255, v))
    return values


def _should_restore_look(uptime_s, current_values, saved_values) -> bool:
    """Pure decision: restore only on a fresh boot, with a lit saved look,
    when current output is confirmed all-zero."""
    if uptime_s is None or uptime_s > BOOT_RESTORE_MAX_UPTIME_S:
        return False
    if not saved_values or not any(saved_values.values()):
        return False
    if not current_values:  # empty dict = fetch failed, not evidence of dark
        return False
    return not any(current_values.values())


def _load_last_look() -> dict[int, int]:
    try:
        return _parse_last_look(LAST_LOOK_FILE.read_text())
    except OSError:
        return {}


def _parse_systemd_show_property(output: str, key: str) -> str:
    """Extract `key=value` from `systemctl show -p <key>` output. Pure."""
    prefix = f"{key}="
    for line in (output or "").splitlines():
        line = line.strip()
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ""


def _qlc_service_started_at() -> str:
    """Monotonic start stamp of qlcplus-web — changes iff the service
    (re)started. Empty string when unavailable."""
    result = execute_command(
        "systemctl show -p ActiveEnterTimestampMonotonic qlcplus-web.service"
    )
    return _parse_systemd_show_property(
        result.get("output", ""), "ActiveEnterTimestampMonotonic"
    )


def _restore_after_qlc_restart():
    """QLC+ just (re)started — it transmits all-zeros until something sets a
    look, so an unattended crash-restart blacks the venue out exactly like a
    reboot does. Re-apply the saved look once output is confirmed all-zero."""
    saved = _load_last_look()
    if not saved:
        return
    deadline = time.time() + 120
    while time.time() < deadline:
        current = get_current_channel_values()
        if current:
            # uptime_s=0: the fresh-start guard is the restart we just saw
            if _should_restore_look(0, current, saved):
                ok = set_channel_values(saved.items())
                lit = sum(1 for v in saved.values() if v)
                print(f"qlc-restart-restore: re-applied last look ({lit} lit channels) ok={ok}")
            else:
                print("qlc-restart-restore: output already non-zero, leaving it alone")
            return
        time.sleep(5)
    print("qlc-restart-restore: QLC+ never returned channel data, giving up")


def _last_look_saver_loop():
    """Every LAST_LOOK_SAVE_INTERVAL_S: snapshot the current look to disk if
    it's lit and changed, and watch for QLC+ service restarts (which reset
    output to zeros) to trigger a restore."""
    last_written = None
    qlc_started_at = _qlc_service_started_at()
    while True:
        time.sleep(LAST_LOOK_SAVE_INTERVAL_S)
        try:
            stamp = _qlc_service_started_at()
            if stamp and qlc_started_at and stamp != qlc_started_at:
                print("last-look saver: qlcplus-web restart detected — checking for blackout")
                _restore_after_qlc_restart()
            if stamp:
                qlc_started_at = stamp

            values = get_current_channel_values()
            if not values or not any(values.values()):
                continue  # never overwrite the saved look with a blackout
            snap = {str(k): int(v) for k, v in sorted(values.items())}
            if snap == last_written:
                continue
            LAST_LOOK_FILE.parent.mkdir(parents=True, exist_ok=True)
            LAST_LOOK_FILE.write_text(json.dumps({
                "values": snap,
                "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }))
            last_written = snap
        except Exception as e:
            print(f"last-look saver error: {e}")


def _boot_restore_last_look():
    """On a fresh boot, re-apply the saved look once QLC+ confirms the
    output is all-zero. Gives up quietly after ~3 minutes."""
    saved = _load_last_look()
    if not saved:
        print("boot-restore: no saved look, skipping")
        return
    deadline = time.time() + 180
    while time.time() < deadline:
        uptime_s = _proc_uptime_seconds()
        if uptime_s is not None and uptime_s > BOOT_RESTORE_MAX_UPTIME_S:
            print(f"boot-restore: uptime {int(uptime_s)}s > {BOOT_RESTORE_MAX_UPTIME_S}s — "
                  "service restart, not a boot; skipping")
            return
        current = get_current_channel_values()
        if current:
            if _should_restore_look(uptime_s, current, saved):
                ok = set_channel_values(saved.items())
                lit = sum(1 for v in saved.values() if v)
                print(f"boot-restore: re-applied last look ({lit} lit channels) ok={ok}")
            else:
                print("boot-restore: output already non-zero, leaving it alone")
            return
        time.sleep(5)
    print("boot-restore: QLC+ never returned channel data, giving up")


def _fixture_channels_info(fixture):
    """Return resolved channel info for a fixture, sourced from .qxf when available.

    Returns a list of dicts with keys: offset, name, role, preset, group, colour.
    Falls back to a synthetic list (Ch 1, Ch 2, ...) when no definition is found.
    """
    manufacturer = fixture.get("manufacturer", "") or ""
    model = fixture.get("model", "") or ""
    mode_name = fixture.get("mode", "") or ""

    mode = fixture_definitions.get_mode(manufacturer, model, mode_name)
    if mode is None or not mode.channels:
        # Fall back: synthesize generic channel info from the heuristic roles
        roles = _fixture_roles_heuristic(fixture)
        offset_to_role: dict[int, str] = {}
        for role, val in roles.items():
            if role == "brightness":
                continue
            if isinstance(val, int):
                offset_to_role[val] = role
        info = []
        for offset in range(int(fixture.get("channels", 0))):
            role = offset_to_role.get(offset)
            info.append({
                "offset": offset,
                "name": role.title() if role else f"Ch {offset + 1}",
                "role": role,
                "preset": None,
                "group": None,
                "colour": None,
            })
        return info

    return [ch.to_dict() for ch in mode.channels]


def _fixture_roles(fixture):
    """Resolve role offsets for a fixture using its .qxf definition.

    Returns a dict like:
        {"dimmer": 0, "warm": 1, "cool": 2, "amber": 3, "brightness": [0]}

    If the .qxf is missing or doesn't cover this mode, falls back to the
    legacy heuristic that inspects fixture name + channel count.
    """
    manufacturer = fixture.get("manufacturer", "") or ""
    model = fixture.get("model", "") or ""
    mode_name = fixture.get("mode", "") or ""

    mode = fixture_definitions.get_mode(manufacturer, model, mode_name)
    if mode is not None and mode.channels:
        return mode.role_offsets()

    return _fixture_roles_heuristic(fixture)


def _fixture_roles_heuristic(fixture):
    """Legacy heuristic-based role inference. Used only when no .qxf is found.

    Channel offsets are 0-based within the fixture's DMX footprint.
    Returns a dict keyed by role name. "brightness" is a list of offsets
    that should track overall intensity (used by adjust_brightness/fade).
    """
    channels = fixture["channels"]
    name = f"{fixture.get('manufacturer', '')} {fixture.get('model', '')} {fixture.get('name', '')}".lower()

    # Generic 3-channel RGB par (e.g. SlimPAR 56 in 3-Ch mode)
    if channels == 3:
        return {"red": 0, "green": 1, "blue": 2, "brightness": [0, 1, 2]}

    # Chauvet SlimPAR Pro W (9-Channel mode): dimmer + WWA + macros/strobe/programs
    # CH1 Master Dimmer, CH2 Warm, CH3 Cool, CH4 Amber, CH5 Macro, CH6 Strobe,
    # CH7 Auto Programs, CH8 Auto Speed, CH9 Dimmer Speed Mode
    if "pro w" in name and channels >= 9:
        return {
            "dimmer": 0,
            "warm": 1,
            "cool": 2,
            "amber": 3,
            "macro": 4,
            "strobe": 5,
            "brightness": [0],
        }

    # Chauvet SlimPAR Pro H (7-Channel mode): dimmer + RGBA + macro + strobe
    if "pro h" in name and channels >= 7:
        return {
            "dimmer": 0,
            "red": 1,
            "green": 2,
            "blue": 3,
            "amber": 4,
            "macro": 5,
            "strobe": 6,
            "brightness": [0],
        }

    # Generic 7-channel RGBA par fallback
    if channels == 7:
        return {
            "dimmer": 0,
            "red": 1,
            "green": 2,
            "blue": 3,
            "amber": 4,
            "brightness": [0],
        }

    # Generic 8+ channel RGBW par fallback (only used when Pro W/Pro H didn't match)
    if channels >= 7:
        return {
            "dimmer": 0,
            "red": 1,
            "green": 2,
            "blue": 3,
            "white": 4,
            "brightness": [0],
        }

    return {"dimmer": 0, "brightness": [0]}


def _absolute_channel(fixture, offset):
    return fixture["universe"] * 512 + fixture["address"] + offset + 1


def _parse_level(value, current=None, default=200):
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    if text.endswith("%"):
        try:
            return round(max(0, min(100, float(text[:-1]))) * 255 / 100)
        except ValueError:
            return default
    try:
        if text[0] in "+-" and current is not None:
            return max(0, min(255, int(current) + int(float(text))))
        return max(0, min(255, int(float(text))))
    except ValueError:
        return default


def _group_fixture_ids(target_groups):
    if not target_groups:
        return None
    if not GROUPS_FILE.exists():
        return set()
    try:
        groups_data = json.loads(GROUPS_FILE.read_text())
    except Exception:
        return set()
    groups_dict = groups_data.get("groups", groups_data)
    fixture_ids = set()
    for group_name in target_groups:
        group = groups_dict.get(group_name)
        if group:
            fixture_ids.update(str(fid) for fid in group.get("fixtures", []))
    return fixture_ids


def _target_fixtures(target_groups=None):
    fixtures = get_workspace_fixtures()
    fixture_ids = _group_fixture_ids(target_groups)
    if fixture_ids is None:
        return fixtures
    return [fixture for fixture in fixtures if str(fixture["id"]) in fixture_ids]


def apply_brightness_live(value, target_groups=None):
    fixtures = _target_fixtures(target_groups)
    current = get_current_channel_values()
    updates = []
    for fixture in fixtures:
        roles = _fixture_roles(fixture)
        for offset in roles.get("brightness", []):
            absolute = _absolute_channel(fixture, offset)
            updates.append((absolute, _parse_level(value, current.get(absolute), default=200)))
    success = set_channel_values(updates)
    if success and target_groups is None and updates:
        # Un-grouped brightness change = "master" — notify OSC/SSE clients
        # so live feedback works regardless of which source drove the change.
        _emit("master_changed", {"value": updates[0][1]})
    return {
        "success": success,
        "output": f"Applied brightness to {len(updates)} channels live via WebSocket",
        "error": "" if success else "Failed to apply brightness via WebSocket",
    }


COLOR_PRESETS = {
    "red":     {"red": 255, "green": 0,   "blue": 0,   "amber": 0,   "warm": 0,   "cool": 0},
    "green":   {"red": 0,   "green": 255, "blue": 0,   "amber": 0,   "warm": 0,   "cool": 0},
    "blue":    {"red": 0,   "green": 0,   "blue": 255, "amber": 0,   "warm": 0,   "cool": 0},
    "purple":  {"red": 200, "green": 0,   "blue": 255, "amber": 0,   "warm": 0,   "cool": 80},
    "magenta": {"red": 255, "green": 0,   "blue": 255, "amber": 0,   "warm": 0,   "cool": 0},
    "cyan":    {"red": 0,   "green": 255, "blue": 255, "amber": 0,   "warm": 0,   "cool": 200},
    # Pro W has no white channel — it produces white via warm+cool together
    "white":   {"red": 220, "green": 220, "blue": 220, "white": 200, "amber": 0,   "warm": 200, "cool": 200},
    "cool":    {"red": 180, "green": 220, "blue": 255, "white": 180, "amber": 0,   "warm": 0,   "cool": 255},
    "warm":    {"red": 255, "green": 170, "blue": 80,  "white": 80,  "amber": 180, "warm": 255, "cool": 0},
    "amber":   {"red": 255, "green": 120, "blue": 0,   "amber": 255, "warm": 120, "cool": 0},
}


def apply_color_live(color, intensity=None, target_groups=None):
    color_key = str(color or "white").strip().lower()
    preset = COLOR_PRESETS.get(color_key, COLOR_PRESETS["white"])
    relative = str(intensity or "").strip().startswith(("+", "-"))
    fixtures = _target_fixtures(target_groups)
    current = get_current_channel_values()
    updates = []

    # Roles whose values we set explicitly from the color preset
    color_roles = {"red", "green", "blue", "white", "warm", "cool", "amber",
                   "uv", "cyan", "magenta", "yellow", "indigo", "lime"}
    # Roles we leave alone: dimmer (set explicitly above), pan/tilt (motion)
    keep_alone_roles = {"dimmer", "pan", "tilt"}

    for fixture in fixtures:
        roles = _fixture_roles(fixture)
        if "dimmer" in roles and not relative:
            updates.append((_absolute_channel(fixture, roles["dimmer"]), _parse_level(intensity, default=220)))

        for role, base in preset.items():
            if role not in roles:
                continue
            absolute = _absolute_channel(fixture, roles[role])
            if relative:
                value = _parse_level(intensity, current.get(absolute), default=current.get(absolute, 0))
            else:
                scale = _parse_level(intensity, default=255) / 255
                value = round(base * scale)
            updates.append((absolute, value))

        # Zero non-color, non-dimmer, non-motion control channels on absolute
        # color sets so leftover macro/strobe/program/speed values don't bleed
        # through from previously applied scenes.
        if not relative:
            color_offsets = {
                roles[r] for r in color_roles
                if r in roles and isinstance(roles[r], int)
            }
            keep_offsets = {
                roles[r] for r in keep_alone_roles
                if r in roles and isinstance(roles[r], int)
            }
            channel_count = int(fixture.get("channels", 0))
            for offset in range(channel_count):
                if offset in color_offsets or offset in keep_offsets:
                    continue
                updates.append((_absolute_channel(fixture, offset), 0))

    success = set_channel_values(updates)
    return {
        "success": success,
        "output": f"Applied {color_key} to {len(updates)} channels live via WebSocket",
        "error": "" if success else f"Failed to apply {color_key} via WebSocket",
    }


# ----------------------------------------------------------------------------
# Color temperature — Kelvin-based white balance (issue #6)
# ----------------------------------------------------------------------------
#
# Per-fixture-type strategy:
#
#   • Warm + cool (+ optional amber) — WWA/tungsten fixtures like the
#     SlimPAR Pro W. Linear mix between warm and cool channels by Kelvin
#     position. Below 2700K we blend toward amber for candle-warmth.
#
#   • RGB / RGBA / RGBW — drive the RGB channels with Tanner Helland's
#     CCT-to-RGB approximation. RGBW additionally pushes the white
#     channel at full so cooler colors look clean rather than blue.
#
#   • White-only or dimmer-only — set the channel at intensity. Includes a
#     per-fixture note in the response since CCT can't actually be expressed.
#
# Kelvin is clamped to 1800–10000 (candle → overcast sky).


def _cct_to_rgb(kelvin: float) -> tuple[int, int, int]:
    """Approximate the color of a black-body radiator at the given Kelvin
    temperature as 8-bit RGB. Uses Tanner Helland's algorithm — widely-cited,
    accurate enough for stage lighting (where the eye + fixture optics smear
    away any precision past ~50K anyway).

    Reference: https://tannerhelland.com/2012/09/18/convert-temperature-rgb-algorithm-code.html
    """
    temp = max(1000.0, min(40000.0, float(kelvin))) / 100.0

    # Red
    if temp <= 66:
        r = 255.0
    else:
        r = temp - 60.0
        r = 329.698727446 * (r ** -0.1332047592)

    # Green
    if temp <= 66:
        g = 99.4708025861 * math.log(temp) - 161.1195681661
    else:
        g = temp - 60.0
        g = 288.1221695283 * (g ** -0.0755148492)

    # Blue
    if temp >= 66:
        b = 255.0
    elif temp <= 19:
        b = 0.0
    else:
        b = temp - 10.0
        b = 138.5177312231 * math.log(b) - 305.0447927307

    def clamp(v: float) -> int:
        return max(0, min(255, round(v)))

    return clamp(r), clamp(g), clamp(b)


# Kelvin anchors for the warm + cool channel mix on WWA fixtures.
# 2700K is conventional tungsten (pure warm); 6500K is daylight (pure cool).
_WWA_WARM_K = 2700
_WWA_COOL_K = 6500
# Below this we start blending the warm channel toward amber for very-warm
# (candle / firelight) territory.
_AMBER_THRESHOLD_K = 2700
# Below this we are fully amber, no warm.
_AMBER_FLOOR_K = 1800


def _wwa_mix(kelvin: float) -> dict[str, float]:
    """Return {role: 0..1} mix for a fixture's warm/cool/amber channels.

    Outside the warm/cool range we clamp to the nearest anchor. Below 2700K
    we additionally taper warm→amber so candle-warm Kelvin (~1900K) reads
    correctly on fixtures that have an amber channel.
    """
    k = float(kelvin)
    # Warm/cool linear mix in the canonical range
    span = _WWA_COOL_K - _WWA_WARM_K
    cool_amount = max(0.0, min(1.0, (k - _WWA_WARM_K) / span))
    warm_amount = max(0.0, min(1.0, (_WWA_COOL_K - k) / span))

    # Amber blend below 2700K
    if k < _AMBER_THRESHOLD_K:
        amber_span = _AMBER_THRESHOLD_K - _AMBER_FLOOR_K
        amber_amount = max(0.0, min(1.0, (_AMBER_THRESHOLD_K - k) / amber_span))
        # Shift some warm to amber proportionally
        warm_amount *= (1.0 - amber_amount)
    else:
        amber_amount = 0.0

    return {"warm": warm_amount, "cool": cool_amount, "amber": amber_amount}


def _fixture_values_to_rgb(channels: list) -> tuple[int, int, int] | None:
    """Map a fixture's channel breakdown to a display RGB triple (0–255 each).

    Uses the same CCT/WWA math as DMX output.  Returns None when no usable
    color role is found so the caller can substitute a neutral fallback.
    """
    roles: dict[str, int] = {}
    for ch in channels:
        role = ch.get("role")
        if role:
            # Keep the highest value when the same role appears on multiple channels.
            roles[role] = max(roles.get(role, 0), ch["value"])

    def _s(v: int) -> float:
        return v / 255.0

    dim = _s(roles["dimmer"]) if "dimmer" in roles else 1.0

    has_rgb = any(r in roles for r in ("red", "green", "blue"))
    has_wwa = any(r in roles for r in ("warm", "cool"))

    if has_rgb:
        r = roles.get("red", 0)
        g = roles.get("green", 0)
        b = roles.get("blue", 0)
        w = roles.get("white", 0)
        r = min(255, r + w)
        g = min(255, g + w)
        b = min(255, b + w)
        # Amber approximated as orange-yellow (255, 191, 0)
        a = roles.get("amber", 0)
        r = min(255, r + a)
        g = min(255, g + round(a * 191 / 255))
        return (round(r * dim), round(g * dim), round(b * dim))

    if has_wwa:
        warm = _s(roles.get("warm", 0))
        cool = _s(roles.get("cool", 0))
        amber = _s(roles.get("amber", 0))
        total = warm + cool + amber
        if total == 0:
            return None
        # Weighted Kelvin from warm/cool balance
        warm_cool_sum = warm + cool
        warm_frac = warm / warm_cool_sum if warm_cool_sum > 0 else 1.0
        kelvin = _WWA_WARM_K + (1.0 - warm_frac) * (_WWA_COOL_K - _WWA_WARM_K)
        base_r, base_g, base_b = _cct_to_rgb(kelvin)
        # Fold amber into the CCT result
        base_r = min(255, base_r + round(amber * 255))
        base_g = min(255, base_g + round(amber * 191))
        scale = min(1.0, total) * dim
        return (round(base_r * scale), round(base_g * scale), round(base_b * scale))

    if "dimmer" in roles:
        # Dimmer-only fixture: render as neutral white scaled by level
        v = round(255 * dim)
        return (v, v, v)

    return None


def apply_color_temperature_live(kelvin, intensity=None, target_groups=None):
    """Set the targeted fixtures to a Kelvin white balance.

    Args:
        kelvin: target color temperature in Kelvin. Numbers or numeric strings.
                Clamped to [1800, 10000].
        intensity: optional 0–255, percentage, or relative (+/-) — same shape
                   accepted by apply_color_live. Defaults to full (255).
        target_groups: optional list of group names. Omit for the entire rig.

    Per-fixture strategy is chosen automatically based on which color roles
    the .qxf parser exposes for that fixture.
    """
    try:
        k = float(kelvin)
    except (TypeError, ValueError):
        return {
            "success": False,
            "output": "",
            "error": f"Invalid kelvin value: {kelvin!r}",
        }
    k = max(1800.0, min(10000.0, k))

    intensity_scale = _parse_level(intensity, default=255) / 255 if intensity is not None else 1.0
    intensity_scale = max(0.0, min(1.0, intensity_scale))
    master_intensity = round(255 * intensity_scale)

    fixtures = _target_fixtures(target_groups)
    updates = []
    per_fixture_notes = []

    # Pre-compute the RGB tint once — same for every fixture, only mix-in
    # differs per fixture type.
    rgb = _cct_to_rgb(k)

    # Roles we explicitly set vs. roles we leave untouched
    color_roles = {"red", "green", "blue", "white", "warm", "cool", "amber",
                   "uv", "cyan", "magenta", "yellow", "indigo", "lime"}
    keep_alone_roles = {"dimmer", "pan", "tilt"}

    for fixture in fixtures:
        roles = _fixture_roles(fixture)
        has_warm = "warm" in roles
        has_cool = "cool" in roles
        has_rgb = all(r in roles for r in ("red", "green", "blue"))
        has_white = "white" in roles
        has_amber = "amber" in roles

        if has_warm and has_cool:
            # Strategy A — WWA / tungsten fixture
            mix = _wwa_mix(k)
            for role in ("warm", "cool", "amber"):
                if role not in roles:
                    continue
                value = round(255 * mix[role] * intensity_scale)
                updates.append((_absolute_channel(fixture, roles[role]), value))
            per_fixture_notes.append({
                "id": fixture["id"],
                "name": fixture.get("name", ""),
                "strategy": "wwa",
                "applied": {role: round(255 * mix[role] * intensity_scale)
                            for role in ("warm", "cool", "amber") if role in roles},
            })
        elif has_rgb:
            # Strategy B — RGB / RGBA / RGBW fixture, Tanner Helland tint
            r_val = round(rgb[0] * intensity_scale)
            g_val = round(rgb[1] * intensity_scale)
            b_val = round(rgb[2] * intensity_scale)
            updates.append((_absolute_channel(fixture, roles["red"]), r_val))
            updates.append((_absolute_channel(fixture, roles["green"]), g_val))
            updates.append((_absolute_channel(fixture, roles["blue"]), b_val))

            applied = {"red": r_val, "green": g_val, "blue": b_val}

            # RGBW — push the white channel at full intensity so cool whites
            # don't read as blue. Skip if no white channel.
            if has_white:
                w_val = master_intensity
                updates.append((_absolute_channel(fixture, roles["white"]), w_val))
                applied["white"] = w_val

            # RGBA — use amber to reinforce warmth below ~3500K
            if has_amber and not has_white:
                # Map 1800K..3500K → amber 255..0
                amber_amount = max(0.0, min(1.0, (3500 - k) / (3500 - 1800)))
                a_val = round(255 * amber_amount * intensity_scale)
                updates.append((_absolute_channel(fixture, roles["amber"]), a_val))
                applied["amber"] = a_val

            per_fixture_notes.append({
                "id": fixture["id"],
                "name": fixture.get("name", ""),
                "strategy": "rgbw" if has_white else ("rgba" if has_amber else "rgb"),
                "applied": applied,
            })
        elif has_white:
            # Strategy C — white-only fixture, can't express CCT, just set intensity
            updates.append((_absolute_channel(fixture, roles["white"]), master_intensity))
            per_fixture_notes.append({
                "id": fixture["id"],
                "name": fixture.get("name", ""),
                "strategy": "white_only",
                "note": "Fixture has no warm/cool or RGB channels; CCT can't be expressed. Set white at intensity.",
                "applied": {"white": master_intensity},
            })
        elif "brightness" in roles or "dimmer" in roles:
            # Strategy D — dimmer-only fixture, even less than white
            offsets = roles.get("brightness", [roles["dimmer"]] if "dimmer" in roles else [])
            for offset in offsets:
                updates.append((_absolute_channel(fixture, offset), master_intensity))
            per_fixture_notes.append({
                "id": fixture["id"],
                "name": fixture.get("name", ""),
                "strategy": "dimmer_only",
                "note": "Fixture has no color channels; CCT can't be expressed. Set dimmer at intensity.",
                "applied": {"dimmer": master_intensity},
            })
        else:
            per_fixture_notes.append({
                "id": fixture["id"],
                "name": fixture.get("name", ""),
                "strategy": "skip",
                "note": "No usable color or brightness channel found.",
            })
            continue

        # Drive the dimmer channel up so the colored channels are actually visible
        if "dimmer" in roles and isinstance(roles["dimmer"], int):
            updates.append((_absolute_channel(fixture, roles["dimmer"]), master_intensity))

        # Zero out non-color, non-motion channels (same pattern as
        # apply_color_live) so macro/strobe/program state doesn't bleed in.
        color_offsets = {
            roles[r] for r in color_roles
            if r in roles and isinstance(roles[r], int)
        }
        keep_offsets = {
            roles[r] for r in keep_alone_roles
            if r in roles and isinstance(roles[r], int)
        }
        channel_count = int(fixture.get("channels", 0))
        for offset in range(channel_count):
            if offset in color_offsets or offset in keep_offsets:
                continue
            updates.append((_absolute_channel(fixture, offset), 0))

    success = set_channel_values(updates)
    return {
        "success": success,
        "output": f"Applied {k:.0f}K to {len(per_fixture_notes)} fixture(s) ({len(updates)} channels)",
        "error": "" if success else f"Failed to apply {k:.0f}K via WebSocket",
        "kelvin": k,
        "rgb": list(rgb),
        "fixtures": per_fixture_notes,
    }


# ----------------------------------------------------------------------------
# Palette — assign different colors/CCTs to different groups in one call
# (issue #7)
# ----------------------------------------------------------------------------
#
# This is the "set the room" primitive — three-point lighting in one move.
# Functionally it's a dispatcher over apply_color_live / apply_color_temperature_live
# per group, but the ergonomics are different: agents (and humans) tend to
# think "here's my palette" as a unit, not as a sequence of independent
# color calls.
#
# Each assignment value is normalized to either {color, intensity} or
# {kelvin, intensity}. The accepted shapes are documented on the MCP tool.


def _normalize_palette_value(value):
    """Coerce a palette assignment value into a routing dict.

    Returns a dict with one of these shapes:
        {"kelvin": float, "intensity": ...}    → routed to color_temperature
        {"color":  str,   "intensity": ...}    → routed to adjust_color
        {"error":  str}                        → unparseable

    Accepted input shapes:
        "warm"                                  → color preset
        3200, 3200.0                            → Kelvin number
        "3200"                                  → Kelvin (numeric string)
        "5600K"                                 → Kelvin (with K suffix)
        {"color": "warm", "intensity": "70%"}   → explicit color
        {"kelvin": 3200, "intensity": "50%"}    → explicit Kelvin
        {"k": 3200}                             → Kelvin (short key)
    """
    if isinstance(value, dict):
        intensity = value.get("intensity")
        kelvin = value.get("kelvin") or value.get("k")
        color = value.get("color")
        if kelvin is not None:
            try:
                return {"kelvin": float(kelvin), "intensity": intensity}
            except (TypeError, ValueError):
                return {"error": f"Invalid kelvin in palette dict: {kelvin!r}"}
        if color is not None:
            return {"color": str(color), "intensity": intensity}
        return {"error": "palette dict requires 'kelvin' or 'color'"}

    if isinstance(value, bool):
        # Bool is technically a number in Python — explicitly reject
        return {"error": f"Invalid palette value: {value!r}"}

    if isinstance(value, (int, float)):
        return {"kelvin": float(value), "intensity": None}

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return {"error": "Empty palette value"}
        # Try Kelvin number with optional K suffix
        kelvin_candidate = stripped.rstrip("Kk").strip() if stripped[-1] in "Kk" else stripped
        try:
            k_val = float(kelvin_candidate)
            # Only treat as Kelvin if it's in a plausible range — otherwise
            # it's likely an intensity-as-string or a malformed color name
            if 1000 <= k_val <= 40000:
                return {"kelvin": k_val, "intensity": None}
        except ValueError:
            pass
        # Otherwise interpret as a color preset name
        return {"color": stripped, "intensity": None}

    return {"error": f"Unsupported palette value type: {type(value).__name__}"}


def apply_palette_live(assignments):
    """Apply a palette: assign different colors/CCTs to different groups in
    one round trip.

    Note: unlike most other actions, palette does NOT accept a
    `target_groups` argument — the assignments dict's keys ARE the targets.
    Group names not present in the assignments are left untouched.

    Args:
        assignments: dict mapping group_name → value. See
                     `_normalize_palette_value` for the accepted value shapes.
    """
    if not isinstance(assignments, dict) or not assignments:
        return {
            "success": False,
            "output": "",
            "error": "assignments must be a non-empty object mapping group → value",
        }

    per_group = {}
    overall_success = True

    for group_name, raw_value in assignments.items():
        routing = _normalize_palette_value(raw_value)
        if "error" in routing:
            per_group[group_name] = {
                "success": False,
                "error": routing["error"],
                "value": raw_value,
            }
            overall_success = False
            continue

        try:
            if "kelvin" in routing:
                result = apply_color_temperature_live(
                    routing["kelvin"],
                    routing.get("intensity"),
                    target_groups=[group_name],
                )
                per_group[group_name] = {
                    "success": bool(result.get("success")),
                    "strategy": "color_temperature",
                    "kelvin": routing["kelvin"],
                    "intensity": routing.get("intensity"),
                    "fixtures_touched": len(result.get("fixtures", [])),
                    "output": result.get("output", ""),
                    "error": result.get("error", "") if not result.get("success") else "",
                }
            else:
                result = apply_color_live(
                    routing["color"],
                    routing.get("intensity"),
                    target_groups=[group_name],
                )
                per_group[group_name] = {
                    "success": bool(result.get("success")),
                    "strategy": "color_preset",
                    "color": routing["color"],
                    "intensity": routing.get("intensity"),
                    "output": result.get("output", ""),
                    "error": result.get("error", "") if not result.get("success") else "",
                }
            if not per_group[group_name]["success"]:
                overall_success = False
        except Exception as e:
            per_group[group_name] = {
                "success": False,
                "error": f"{type(e).__name__}: {e}",
            }
            overall_success = False

    successful = sum(1 for r in per_group.values() if r.get("success"))
    return {
        "success": overall_success,
        "applied_to": len(assignments),
        "successful": successful,
        "groups": per_group,
        "output": f"Palette applied · {successful}/{len(assignments)} groups",
        "error": "" if overall_success else "One or more group assignments failed — see per-group results",
    }


# ----------------------------------------------------------------------------
# Strobe — first-class abstraction over the per-fixture strobe channel
# (issue #5)
# ----------------------------------------------------------------------------
#
# QLC+ fixtures with a dedicated strobe channel almost universally follow
# the convention:
#
#     DMX 0–9    : rest (no strobe)
#     DMX 10–255 : slow → fast strobe
#
# We can't statically know the exact rate-to-DMX mapping for every fixture
# (it varies — some go to 20Hz, some 25Hz, some have non-linear ramps), so
# v1 uses a linear approximation: rate 0 → DMX 0 (rest), rate 20Hz → DMX 255
# (typically the fastest the fixture can do). Most stage fixtures look right
# at this mapping. Future work: parse <Capability> ranges from the .qxf to
# get per-fixture-accurate Hz mappings.


# Fixtures that hit pure-pulse at this Hz get full DMX (255).
_STROBE_MAX_HZ = 20.0
# DMX value where the strobe channel transitions out of "rest" zone.
_STROBE_DMX_REST_FLOOR = 10
_STROBE_DMX_MAX = 255


def _strobe_dmx_value(rate) -> int:
    """Map a rate input to a DMX channel value.

    Accepts:
        "off" / "0" / 0 / 0.0 / None  → 0  (rest)
        positive number (Hz)          → 10..255 linearly (clamped to 0..20 Hz)
    """
    if rate is None:
        return 0
    if isinstance(rate, str):
        normalized = rate.strip().lower()
        if normalized in ("off", "stop", "rest", "none", "0", "0hz"):
            return 0
        # Strip a trailing "hz" if present
        if normalized.endswith("hz"):
            normalized = normalized[:-2].strip()
        try:
            rate_val = float(normalized)
        except ValueError:
            return 0
    else:
        try:
            rate_val = float(rate)
        except (TypeError, ValueError):
            return 0

    if rate_val <= 0:
        return 0
    clamped = min(_STROBE_MAX_HZ, rate_val)
    span = _STROBE_DMX_MAX - _STROBE_DMX_REST_FLOOR
    return int(round(_STROBE_DMX_REST_FLOOR + (clamped / _STROBE_MAX_HZ) * span))


def apply_strobe_live(rate, intensity=None, target_groups=None):
    """Strobe targeted fixtures at the given rate.

    Args:
        rate: 0–20 Hz, or "off"/"0" to stop. Above 20Hz clamps to 20Hz.
        intensity: Optional brightness level (0-255 / "%" / "+/-") applied
                   to the fixture's dimmer / brightness-tracking channels
                   so the strobe is visible. If None, brightness is left
                   alone (operator wants to strobe at whatever's currently lit).
        target_groups: Optional group names to limit the strobe. Omit to
                       target every fixture in the workspace.

    Fixtures without a dedicated strobe channel are skipped and listed in
    the response. Use blackout() and adjust_color() / batch_action for
    fixtures that need brightness-cycled "strobe" effects instead.
    """
    fixtures = _target_fixtures(target_groups)
    if not fixtures:
        return {
            "success": False,
            "output": "",
            "error": "No fixtures matched the request",
        }

    dmx_value = _strobe_dmx_value(rate)
    rest = (dmx_value == 0)

    updates = []
    per_fixture = []

    for fixture in fixtures:
        roles = _fixture_roles(fixture)
        strobe_offset = roles.get("strobe")

        if not isinstance(strobe_offset, int):
            per_fixture.append({
                "id": fixture["id"],
                "name": fixture.get("name", ""),
                "status": "skipped",
                "reason": (
                    "no dedicated strobe channel — use batch_action with "
                    "blackout/adjust_color for brightness-cycled effects"
                ),
            })
            continue

        strobe_abs = _absolute_channel(fixture, strobe_offset)
        updates.append((strobe_abs, dmx_value))

        applied = {"strobe": dmx_value}

        # Drive dimmer to make the strobe visible (only if caller specified
        # an intensity and we're not resting)
        if intensity is not None and not rest:
            parsed_intensity = _parse_level(intensity, default=255)
            if "dimmer" in roles and isinstance(roles["dimmer"], int):
                dim_abs = _absolute_channel(fixture, roles["dimmer"])
                updates.append((dim_abs, parsed_intensity))
                applied["dimmer"] = parsed_intensity
            elif "brightness" in roles and roles["brightness"]:
                for offset in roles["brightness"]:
                    abs_ch = _absolute_channel(fixture, offset)
                    updates.append((abs_ch, parsed_intensity))
                applied[f"brightness({len(roles['brightness'])} channels)"] = parsed_intensity

        per_fixture.append({
            "id": fixture["id"],
            "name": fixture.get("name", ""),
            "status": "applied",
            "applied": applied,
        })

    skipped = sum(1 for f in per_fixture if f["status"] == "skipped")
    applied_count = sum(1 for f in per_fixture if f["status"] == "applied")

    if applied_count == 0:
        return {
            "success": False,
            "output": "",
            "error": "No fixtures with a strobe channel were targeted",
            "fixtures": per_fixture,
        }

    # Reconstruct a human-readable rate string for the response
    if rest:
        rate_label = "off"
    else:
        # Reverse-engineer the rate from the DMX value (after clamping) for
        # display — avoids re-parsing a potentially string `rate`
        span = _STROBE_DMX_MAX - _STROBE_DMX_REST_FLOOR
        approx_hz = ((dmx_value - _STROBE_DMX_REST_FLOOR) / span) * _STROBE_MAX_HZ
        rate_label = f"~{approx_hz:.1f}Hz"

    success = set_channel_values(updates)
    return {
        "success": success,
        "output": (
            f"Strobe {'stopped' if rest else 'at ' + rate_label} "
            f"on {applied_count} fixture(s), {skipped} skipped"
        ),
        "error": "" if success else "Failed to apply strobe via WebSocket",
        "rate": rate_label,
        "dmx_value": dmx_value,
        "fixtures": per_fixture,
    }


async def _fade_brightness_async(channels, target_value, seconds, steps):
    """Run a multi-step fade over the persistent QLC+ WebSocket."""
    ws = await _ensure_qlc_ws()
    try:
        for step in range(1, steps + 1):
            ratio = step / steps
            commands = []
            for channel, start in channels:
                val = max(0, min(255, round(start + (target_value - start) * ratio)))
                commands.append(f"CH|{channel}|{val}")
            async with _qlc_ws_lock:
                for cmd in commands:
                    await ws.send(cmd)
            if step < steps and seconds > 0:
                await asyncio.sleep(seconds / steps)
        return True
    except Exception as e:
        log.error("fade_ws_failed", error=str(e))
        return False


def fade_brightness_live(target, duration, target_groups=None):
    fixtures = _target_fixtures(target_groups)
    current = get_current_channel_values()
    channels = []
    for fixture in fixtures:
        roles = _fixture_roles(fixture)
        for offset in roles.get("brightness", []):
            absolute = _absolute_channel(fixture, offset)
            channels.append((absolute, current.get(absolute, 0)))

    try:
        seconds = max(0, float(duration))
    except (TypeError, ValueError):
        seconds = 3
    steps = max(1, min(30, math.ceil(seconds * 10)))
    target_value = _parse_level(target, default=0)

    try:
        success = _qlc_run(
            _fade_brightness_async(channels, target_value, seconds, steps),
            timeout=seconds + 5,
        )
    except Exception as e:
        log.error("fade_failed", error=str(e))
        success = False

    return {
        "success": success,
        "output": f"Faded {len(channels)} brightness channels to {target_value} over {seconds:g}s",
        "error": "" if success else "Fade WebSocket connection failed",
    }


def execute_command(command):
    """Execute a shell command and return output"""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30
        )
        return {
            "success": result.returncode == 0,
            "output": result.stdout,
            "error": result.stderr
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "output": "",
            "error": "Command timed out"
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": str(e)
        }


def interpret_command(user_input):
    """
    Use AI to interpret natural language command and convert to lighting action

    Args:
        user_input: Natural language command from user

    Returns:
        dict: Action data with action type, parameters, and explanation
    """

    if not user_input or not user_input.strip():
        return {
            "action": "error",
            "parameters": {},
            "explanation": "Empty command"
        }

    # Check AI configuration
    if AI_PROVIDER not in ["anthropic", "openai", "ollama"]:
        return {
            "action": "error",
            "parameters": {},
            "explanation": f"Invalid AI provider: {AI_PROVIDER}"
        }

    if AI_PROVIDER != "ollama" and not AI_API_KEY:
        return {
            "action": "error",
            "parameters": {},
            "explanation": "AI_API_KEY not configured"
        }

    # Build prompt for AI
    system_prompt = """\
You are a lighting control assistant.
Convert natural language commands into structured lighting actions.

Available actions:
1. adjust_brightness: Change overall brightness (value: -100 to +100 or absolute 0-255)
2. adjust_color: Change color (color: red/green/blue/warm/cool/etc, intensity: 0-255)
3. apply_template: Use a template (template: youtube-studio/party/ambient/
   spotlight/work-light/warm-white/cool-white)
4. generate_scene: Create new scene from description (description: text)
5. fade: Fade to black or specific level (duration: seconds, target: 0-255)
6. activate_scene: Apply an existing named scene (scene: Red/Blue/Green/
   Lights ON/Lights OFF/Work Light/Purple/Warm Amber/Spotlight/etc)

Respond ONLY with valid JSON in this format:
{
  "action": "action_name",
  "parameters": {
    "param1": "value1",
    "param2": "value2"
  },
  "explanation": "Brief explanation of what will happen"
}

Examples:
Input: "make it brighter"
Output: {"action": "adjust_brightness", "parameters": {"value": "+50"},
         "explanation": "Increasing brightness by 50"}

Input: "add more blue"
Output: {"action": "adjust_color", "parameters": {"color": "blue", "intensity": "+50"},
         "explanation": "Adding more blue to the scene"}

Input: "switch to party mode"
Output: {"action": "apply_template", "parameters": {"template": "party"},
         "explanation": "Applying party template"}

Input: "warm sunset ambiance"
Output: {"action": "generate_scene", "parameters": {"description": "warm sunset ambiance"},
         "explanation": "Generating warm sunset scene"}

Input: "fade to black over 5 seconds"
Output: {"action": "fade", "parameters": {"duration": "5", "target": "0"},
         "explanation": "Fading to black over 5 seconds"}

Input: "turn on red scene"
Output: {"action": "activate_scene", "parameters": {"scene": "Red"},
         "explanation": "Applying the Red scene"}"""

    user_prompt = f"Convert this command: {user_input}"

    # Call AI based on provider
    try:
        if AI_PROVIDER == "anthropic":
            response = call_anthropic(system_prompt, user_prompt)
        elif AI_PROVIDER == "openai":
            response = call_openai(system_prompt, user_prompt)
        elif AI_PROVIDER == "ollama":
            response = call_ollama(system_prompt, user_prompt)
        else:
            return {
                "action": "error",
                "parameters": {},
                "explanation": f"Unknown AI provider: {AI_PROVIDER}"
            }
    except Exception as e:
        return {
            "action": "error",
            "parameters": {},
            "explanation": f"AI API error: {str(e)}"
        }

    # Parse JSON response
    try:
        # Extract JSON from response (handle markdown code blocks)
        if "```json" in response:
            response = response.split("```json")[1].split("```")[0].strip()
        elif "```" in response:
            response = response.split("```")[1].split("```")[0].strip()

        return json.loads(response)
    except json.JSONDecodeError:
        # Log server-side only — don't leak AI response to the client (audit item #33)
        log.warning("ai_response_parse_failed", response_preview=response[:200])
        return {
            "action": "error",
            "parameters": {},
            "explanation": "AI returned an invalid response format. Please try again."
        }


def call_anthropic(system_prompt, user_prompt):
    """Call Anthropic Claude API"""
    import requests

    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": AI_API_KEY,
                "anthropic-version": "2023-06-01"
            },
            json={
                "model": AI_MODEL,
                "max_tokens": 1024,
                "system": system_prompt,
                "messages": [
                    {"role": "user", "content": user_prompt}
                ]
            },
            timeout=30
        )
        response.raise_for_status()
        return response.json()["content"][0]["text"]
    except requests.exceptions.RequestException as e:
        raise Exception(f"Anthropic API error: {str(e)}") from e


def call_openai(system_prompt, user_prompt):
    """Call OpenAI API"""
    import requests

    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {AI_API_KEY}"
            },
            json={
                "model": AI_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
            },
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        text = data["choices"][0]["message"]["content"]
        # Strip markdown code fences if present
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        return text
    except requests.exceptions.RequestException as e:
        raise Exception(f"OpenAI API error: {str(e)}") from e


def call_ollama(system_prompt, user_prompt):
    """Call Ollama local API"""
    import requests

    combined_prompt = f"{system_prompt}\n\n{user_prompt}"

    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": AI_MODEL,
                "prompt": combined_prompt,
                "stream": False
            },
            timeout=60
        )
        response.raise_for_status()
        return response.json()["response"]
    except requests.exceptions.ConnectionError as e:
        raise Exception("Ollama not running. Start with: ollama serve") from e
    except requests.exceptions.RequestException as e:
        raise Exception(f"Ollama API error: {str(e)}") from e


def execute_lighting_action(action_data, target_groups=None, source="web"):
    """Execute the interpreted lighting action.

    When running locally on the Pi (IS_LOCAL=True), generated scenes are
    rendered to a temporary XML file and applied immediately through QLC+
    WebSocket channel updates. The workspace is not modified for runtime
    commands.

    If target_groups is a non-empty list, the action is applied to each named
    group in sequence using group-scene / group-template instead of the
    global generate-scene command.
    """
    action = action_data.get("action")
    params = action_data.get("parameters", {})

    def _build_scene_cmd(description, output_file, group_name=None):
        safe_desc = description.replace("'", "'\\''")
        if group_name:
            safe_group = group_name.replace("'", "'\\''")
            if IS_LOCAL and WORKSPACE_PATH.exists():
                return (
                    f"{LIGHTSCTL} group-scene '{safe_group}' '{safe_desc}'"
                    f" --workspace '{WORKSPACE_PATH}'"
                    f" --output '{output_file}'"
                )
            else:
                return f"{LIGHTSCTL} group-scene '{safe_group}' '{safe_desc}' --add-to-workspace"
        else:
            if IS_LOCAL and WORKSPACE_PATH.exists():
                return (
                    f"{LIGHTSCTL} generate-scene '{safe_desc}'"
                    f" --workspace '{WORKSPACE_PATH}'"
                    f" --output '{output_file}'"
                )
            else:
                return f"{LIGHTSCTL} generate-scene '{safe_desc}' --add-to-workspace"

    def _temp_scene_file():
        fh = tempfile.NamedTemporaryFile(prefix="qlc-scene-", suffix=".xml", delete=False)
        path = fh.name
        fh.close()
        return Path(path)

    def _generate_and_apply_live(cmd, scene_file):
        result = execute_command(cmd)
        if not IS_LOCAL or not WORKSPACE_PATH.exists():
            return result
        if not result["success"]:
            scene_file.unlink(missing_ok=True)
            return result
        if not scene_file.exists() or not scene_file.read_text().strip():
            result["success"] = False
            result["error"] = "Scene file not created"
            return result
        scene_xml_content = scene_file.read_text()
        try:
            apply_result = apply_scene_xml_live(scene_xml_content)
        finally:
            scene_file.unlink(missing_ok=True)
        result["success"] = apply_result["success"]
        result["output"] = (result.get("output", "") + "\n" + apply_result["output"]).strip()
        result["error"] = apply_result["error"] if not apply_result["success"] else result.get("error", "")
        # Stash the scene XML so the caller can offer a "save" option
        result["scene_xml"] = scene_xml_content
        return result

    if action == "apply_template":
        template = params.get("template")
        # Validate template against known whitelist (audit item #5 — prevents shell injection)
        VALID_TEMPLATES = {
            "youtube-studio", "party", "ambient", "spotlight",
            "work-light", "warm-white", "cool-white",
        }
        if not template or template not in VALID_TEMPLATES:
            return {
                "success": False,
                "output": "",
                "error": f"Unknown template: {template}. Valid: {sorted(VALID_TEMPLATES)}",
            }
        groups = target_groups if target_groups else []
        if groups:
            # Apply template to each selected group
            combined_output = ""
            for gname in groups:
                safe_name = gname.replace("'", "'\\''")
                scene_file = _temp_scene_file()
                if IS_LOCAL and WORKSPACE_PATH.exists():
                    cmd = (f"{LIGHTSCTL} group-template '{safe_name}' {template}"
                           f" --workspace '{WORKSPACE_PATH}'"
                           f" --output '{scene_file}'")
                else:
                    cmd = f"{LIGHTSCTL} group-template '{safe_name}' {template} --add-to-workspace"
                result = _generate_and_apply_live(cmd, scene_file)
                combined_output += result.get("output", "") + "\n"
                if not result["success"]:
                    return result
            return {"success": True, "output": combined_output.strip(), "error": ""}
        elif IS_LOCAL and WORKSPACE_PATH.exists():
            scene_file = _temp_scene_file()
            cmd = (f"{LIGHTSCTL} generate-from-template {template}"
                   f" --workspace '{WORKSPACE_PATH}'"
                   f" --output '{scene_file}'")
            return _generate_and_apply_live(cmd, scene_file)
        else:
            cmd = f"{LIGHTSCTL} generate-from-template {template} --add-to-workspace"
            return execute_command(cmd)

    elif action == "generate_scene":
        description = params.get("description", "")
        groups = target_groups if target_groups else []
        if groups:
            combined_output = ""
            for gname in groups:
                scene_file = _temp_scene_file()
                cmd = _build_scene_cmd(description, scene_file, group_name=gname)
                result = _generate_and_apply_live(cmd, scene_file)
                combined_output += result.get("output", "") + "\n"
                if not result["success"]:
                    return result
            return {"success": True, "output": combined_output.strip(), "error": ""}
        scene_file = _temp_scene_file()
        cmd = _build_scene_cmd(description, scene_file)
        return _generate_and_apply_live(cmd, scene_file)

    elif action == "adjust_brightness":
        value = params.get("value", "+50")
        return apply_brightness_live(value, target_groups=target_groups)

    elif action == "adjust_color":
        color = params.get("color", "white")
        intensity = params.get("intensity", "200")
        return apply_color_live(color, intensity, target_groups=target_groups)

    elif action == "color_temperature":
        kelvin = params.get("kelvin") or params.get("k")
        if kelvin is None:
            return {"success": False, "output": "", "error": "Missing required parameter 'kelvin'"}
        intensity = params.get("intensity")
        return apply_color_temperature_live(kelvin, intensity, target_groups=target_groups)

    elif action == "palette":
        # Palette uses its own per-group assignments — target_groups is
        # ignored (the assignments dict's keys are the targets).
        assignments = params.get("assignments")
        return apply_palette_live(assignments)

    elif action == "strobe":
        rate = params.get("rate")
        if rate is None:
            return {"success": False, "output": "", "error": "Missing required parameter 'rate'"}
        intensity = params.get("intensity")
        return apply_strobe_live(rate, intensity, target_groups=target_groups)

    elif action == "fade":
        duration = params.get("duration", "3")
        target = params.get("target", "0")
        return fade_brightness_live(target, duration, target_groups=target_groups)

    elif action == "activate_scene":
        scene = params.get("scene") or params.get("name") or params.get("id")
        result = apply_existing_scene_live(scene)
        if result["success"]:
            log.info("scene_activated", scene=scene, source=source)
        return result

    elif action == "start_chase":
        # Dispatch through the same helper the /api/chases/<id>/start endpoint uses.
        # Lets cue lists + batch_action fire chases composably.
        chase_ref = params.get("chase") or params.get("name") or params.get("id")
        if chase_ref is None:
            return {"success": False, "output": "", "error": "Missing 'chase' parameter"}
        chase = _find_function_element(chase_ref, function_type="Chaser")
        if chase is None:
            return {"success": False, "output": "", "error": f"Chase not found: {chase_ref}"}
        fid = chase.get("ID")
        if not (fid and fid.isdigit()):
            return {"success": False, "output": "", "error": f"Chase has no numeric ID: {chase.get('Name')}"}
        ok, raw = set_function_status(int(fid), running=True)
        if ok:
            log.info("chase_started", chase=chase.get("Name"), chase_id=fid, source=source)
        return {
            "success": ok,
            "output": f"Started chase '{chase.get('Name')}'" if ok else "",
            "error": "" if ok else raw,
        }

    elif action == "stop_chase":
        chase_ref = params.get("chase") or params.get("name") or params.get("id")
        if chase_ref is None:
            return {"success": False, "output": "", "error": "Missing 'chase' parameter"}
        chase = _find_function_element(chase_ref, function_type="Chaser")
        if chase is None:
            return {"success": False, "output": "", "error": f"Chase not found: {chase_ref}"}
        fid = chase.get("ID")
        if not (fid and fid.isdigit()):
            return {"success": False, "output": "", "error": f"Chase has no numeric ID: {chase.get('Name')}"}
        ok, raw = set_function_status(int(fid), running=False)
        if ok:
            log.info("chase_stopped", chase=chase.get("Name"), chase_id=fid, source=source)
        return {
            "success": ok,
            "output": f"Stopped chase '{chase.get('Name')}'" if ok else "",
            "error": "" if ok else raw,
        }

    else:
        return {
            "success": False,
            "output": "",
            "error": f"Unknown action: {action}"
        }


@app.route("/")
def index():
    """Serve the control interface"""
    return render_template("index.html")


# ----------------------------------------------------------------------------
# Auth — shared password + signed session cookie (issue #25)
# ----------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    """Login form. In open mode (no LIGHTS_PASSWORD) just bounce to /."""
    if LIGHTS_PASSWORD is None:
        return redirect("/")

    if request.method == "GET":
        return render_template("login.html", error=None)

    ip = request.remote_addr or "unknown"
    now = time.time()
    allowed, retry_after = _login_rate_check(_LOGIN_ATTEMPTS, ip, now)
    if not allowed:
        return render_template(
            "login.html",
            error=f"Too many attempts. Try again in {retry_after}s.",
        ), 429

    password = request.form.get("password", "")
    if _verify_password(password, LIGHTS_PASSWORD):
        _LOGIN_ATTEMPTS.pop(ip, None)
        session["authed"] = True
        session.permanent = bool(request.form.get("remember"))
        return redirect("/")

    _LOGIN_ATTEMPTS.setdefault(ip, []).append(now)
    return render_template("login.html", error="Incorrect password"), 401


@app.route("/logout", methods=["GET", "POST"])
def logout():
    """Clear the session cookie and send the user back to the login form."""
    session.clear()
    return redirect("/login")


# ----------------------------------------------------------------------------
# PWA support — manifest + service worker so the web UI installs as a phone app
# ----------------------------------------------------------------------------

@app.route("/manifest.json", methods=["GET"])
def pwa_manifest():
    """Web App Manifest — lets the browser offer "Add to Home Screen"."""
    manifest = {
        "name": "Lighting Control",
        "short_name": "Lights",
        "description": "Drive a QLC+ DMX rig from the browser.",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "any",
        "background_color": "#0a0a0a",
        "theme_color": "#0a0a0a",
        "categories": ["productivity", "utilities"],
        # Pure-SVG icon embedded inline. Avoids needing static-file plumbing
        # for the install flow; the browser will use it at any size.
        "icons": [
            {
                "src": "/icon.svg",
                "sizes": "any",
                "type": "image/svg+xml",
                "purpose": "any maskable",
            }
        ],
    }
    response = jsonify(manifest)
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@app.route("/icon.svg", methods=["GET"])
def pwa_icon():
    """SVG icon used by the manifest. Matches the in-app logo (sun with rays)."""
    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192">'
        '<rect width="192" height="192" fill="#0a0a0a"/>'
        '<g transform="translate(96 96)">'
          '<circle r="64" fill="none" stroke="#444" stroke-width="3"/>'
          '<circle r="20" fill="#f0f0f0" opacity="0.9"/>'
          '<g stroke="#555" stroke-width="4" stroke-linecap="round">'
            '<line x1="0" y1="-72" x2="0" y2="-48"/>'
            '<line x1="0" y1="48" x2="0" y2="72"/>'
            '<line x1="-72" y1="0" x2="-48" y2="0"/>'
            '<line x1="48" y1="0" x2="72" y2="0"/>'
            '<line x1="-50.9" y1="-50.9" x2="-33.9" y2="-33.9"/>'
            '<line x1="33.9" y1="33.9" x2="50.9" y2="50.9"/>'
            '<line x1="33.9" y1="-33.9" x2="50.9" y2="-50.9"/>'
            '<line x1="-50.9" y1="33.9" x2="-33.9" y2="50.9"/>'
          '</g>'
        '</g></svg>'
    )
    response = app.response_class(svg, mimetype="image/svg+xml")
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@app.route("/logo", methods=["GET"])
def serve_logo():
    """Serve a custom logo from the static/ directory.

    Convention: drop a file named 'logo.png', 'logo.webp', 'logo.svg', or
    'logo.jpg' into control-server/static/ to brand the interface. The file
    is gitignored so each deployment can have its own identity.

    Returns 404 if no logo file is present (the template falls back to the
    built-in SVG icon).
    """
    static_dir = Path(__file__).parent / "static"
    for ext in ("webp", "png", "svg", "jpg", "jpeg", "gif"):
        logo_file = static_dir / f"logo.{ext}"
        if logo_file.exists():
            mime_map = {
                "webp": "image/webp", "png": "image/png", "svg": "image/svg+xml",
                "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
            }
            response = send_from_directory(str(static_dir), f"logo.{ext}", mimetype=mime_map[ext])
            response.headers["Cache-Control"] = "public, max-age=86400"
            return response
    abort(404)


@app.route("/sw.js", methods=["GET"])
def pwa_service_worker():
    """Tiny service worker — installability requirement on Chrome/Android.

    Doesn't do offline caching (would conflict with live DMX state).
    Network-first for everything; the only purpose is the install prompt.
    """
    js = (
        "// Lights Pi service worker — install only, no caching\n"
        "self.addEventListener('install', e => self.skipWaiting());\n"
        "self.addEventListener('activate', e => self.clients.claim());\n"
        "self.addEventListener('fetch', e => { /* let the network handle it */ });\n"
    )
    response = app.response_class(js, mimetype="application/javascript")
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


# ---------------------------------------------------------------------------
# Mock-only debug endpoint — only registered when MOCK_DMX=1
# ---------------------------------------------------------------------------
if MOCK_DMX:
    @app.route("/debug/dmx-state", methods=["GET"])
    def debug_dmx_state():
        """Return the current mock DMX bus state (MOCK_DMX=1 only)."""
        return jsonify(_mock_dmx.snapshot())


@app.route("/api/command", methods=["POST"])
def handle_command():
    """Handle natural language command"""
    import time as _time

    data = request.json
    user_input = data.get("command", "").strip()
    target_groups = data.get("groups") or None  # list of group names, or None = all fixtures

    if not user_input:
        return jsonify({
            "success": False,
            "error": "No command provided"
        }), 400

    # Interpret command using AI (with timing)
    t0 = _time.time()
    action_data = interpret_command(user_input)
    interpret_ms = round((_time.time() - t0) * 1000)

    if action_data.get("action") == "error":
        return jsonify({
            "success": False,
            "error": action_data.get("explanation"),
            "action": action_data,
            "debug": {
                "interpret_ms": interpret_ms,
                "provider": AI_PROVIDER,
                "model": AI_MODEL,
                "is_local": IS_LOCAL,
            }
        }), 400

    # Execute the action (with timing)
    t1 = _time.time()
    result = execute_lighting_action(action_data, target_groups=target_groups)
    execute_ms = round((_time.time() - t1) * 1000)

    return jsonify({
        "success": result["success"],
        "action": action_data,
        "groups": target_groups,
        "output": result.get("output", ""),
        "error": result.get("error", "") if not result["success"] else "",
        "log": result.get("error", "") if result["success"] else "",
        "scene_xml": result.get("scene_xml"),  # present for generate_scene/apply_template
        "debug": {
            "interpret_ms": interpret_ms,
            "execute_ms": execute_ms,
            "total_ms": interpret_ms + execute_ms,
            "provider": AI_PROVIDER,
            "model": AI_MODEL,
            "is_local": IS_LOCAL,
        }
    })


@app.route("/api/action", methods=["POST"])
def handle_action():
    """Dispatch a structured lighting action without going through the AI interpreter.

    Body:
        {
            "action": "<adjust_brightness|adjust_color|fade|apply_template|generate_scene|activate_scene>",
            "parameters": { ... action-specific ... },
            "groups": ["group-name", ...]   # optional, applies to all fixtures if omitted
        }

    Designed for programmatic callers (e.g. the MCP server) that have already
    resolved the user's intent into a structured action and don't need the AI
    pass that /api/command performs.
    """
    import time as _time

    data = request.get_json(silent=True) or {}
    action = (data.get("action") or "").strip()
    if not action:
        return jsonify({"success": False, "error": "Missing 'action' field"}), 400

    action_data = {
        "action": action,
        "parameters": data.get("parameters", {}) or {},
        "explanation": data.get("explanation", ""),
    }
    target_groups = data.get("groups") or None

    t0 = _time.time()
    result = execute_lighting_action(action_data, target_groups=target_groups)
    execute_ms = round((_time.time() - t0) * 1000)

    return jsonify({
        "success": result["success"],
        "action": action_data,
        "groups": target_groups,
        "output": result.get("output", ""),
        "error": result.get("error", "") if not result["success"] else "",
        "scene_xml": result.get("scene_xml"),
        "debug": {
            "execute_ms": execute_ms,
            "is_local": IS_LOCAL,
        }
    })


_HEALTHZ_UNSET = object()


def _dmx_device_readable(dev):
    return os.access(dev, os.R_OK)


def _healthz_status(
    qlc_ws=_HEALTHZ_UNSET,
    last_dmx_ts=_HEALTHZ_UNSET,
    workspace_path=None,
    dmx_device_glob=None,
    dmx_readable_fn=None,
    now=None,
):
    """Aggregate health of all subsystems. Returns (payload_dict, all_critical_ok).

    All parameters are injectable for unit testing; defaults pull from live globals.
    dmx_readable_fn: optional callable(path) -> bool; defaults to os.access(path, os.R_OK).
    """
    import glob as _glob

    if qlc_ws is _HEALTHZ_UNSET:
        qlc_ws = _qlc_ws
    if last_dmx_ts is _HEALTHZ_UNSET:
        last_dmx_ts = _last_dmx_write_ts
    if workspace_path is None:
        workspace_path = WORKSPACE_PATH
    if now is None:
        now = time.time()
    if dmx_readable_fn is None:
        dmx_readable_fn = _dmx_device_readable

    ws_ok = False
    try:
        if qlc_ws is not None and not getattr(qlc_ws, "closed", False):
            ws_ok = True
    except Exception:
        pass

    dmx_device = None
    try:
        devices = (
            dmx_device_glob
            if dmx_device_glob is not None
            else _glob.glob("/dev/ttyUSB*") + _glob.glob("/dev/ttyACM*")
        )
        if devices:
            dev = devices[0]
            dmx_device = dev if dmx_readable_fn(dev) else None
    except Exception:
        pass

    dmx_age = None
    if last_dmx_ts is not None:
        dmx_age = round(now - last_dmx_ts, 1)

    workspace_ok = False
    try:
        if workspace_path.exists():
            ET.parse(str(workspace_path))
            workspace_ok = True
    except Exception:
        pass

    payload = {
        "flask": True,
        "qlc_ws": ws_ok,
        "dmx_device": dmx_device or False,
        "last_dmx_write_age_s": dmx_age,
        "workspace_loaded": workspace_ok,
    }

    all_ok = ws_ok and workspace_ok
    return payload, all_ok


@app.route("/healthz", methods=["GET"])
def healthz():
    """Deep health endpoint. 200 = all critical checks green, 503 = any red."""
    payload, all_ok = _healthz_status()
    return jsonify(payload), 200 if all_ok else 503


@app.route("/api/events", methods=["GET"])
def sse_events():
    """Server-Sent Events stream for real-time rig state changes.

    Optional query param:
        ?filter=scenes,channels,groups,qlc,status
    Omit *filter* (or leave empty) to receive all event types.

    Event types:
        channel_change  — {channels: [{channel, value}, ...]}
        scene_activated — {scene_id, scene_name}
        group_modified  — {group_name, action: created|updated|deleted}
        qlc_disconnect  — {url}
        qlc_reconnect   — {url}
        service_status  — {uptime_s, qlc_connected}  (heartbeat, ≤15 s)
    """
    allowed = parse_filter(request.args.get("filter"))

    @stream_with_context
    def _generate():
        q = EVENT_BUS.subscribe()
        try:
            while True:
                try:
                    envelope = q.get(timeout=15)
                    evt_type = envelope["type"]
                    if allowed is None or evt_type in allowed:
                        yield format_sse(evt_type, envelope["data"])
                except queue.Empty:
                    # Heartbeat keeps the connection alive through idle periods
                    # and through proxies that close idle TCP connections.
                    uptime = round(time.time() - START_TIME)
                    qlc_connected = _qlc_ws is not None and not getattr(_qlc_ws, "closed", False)
                    yield format_sse("service_status", {
                        "uptime_s": uptime,
                        "qlc_connected": qlc_connected,
                    })
        except GeneratorExit:
            pass
        finally:
            EVENT_BUS.unsubscribe(q)

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return Response(_generate(), mimetype="text/event-stream", headers=headers)


@app.route("/api/status", methods=["GET"])
def get_status():
    """Get detailed multi-service health status"""
    import time as _time

    services = {}

    # 1. AI Provider
    ai_ok = False
    ai_detail = ""
    ai_latency = None
    if AI_PROVIDER == "ollama":
        try:
            t0 = _time.time()
            import requests as _req
            r = _req.get("http://localhost:11434/api/tags", timeout=3)
            ai_latency = round((_time.time() - t0) * 1000)
            ai_ok = r.status_code == 200
            ai_detail = "running" if ai_ok else f"HTTP {r.status_code}"
        except Exception as e:
            ai_detail = str(e)
    elif AI_PROVIDER in ("openai", "anthropic"):
        ai_ok = bool(AI_API_KEY)
        ai_detail = "key configured" if ai_ok else "API key missing"
    else:
        ai_detail = f"unknown provider: {AI_PROVIDER}"

    services["ai"] = {
        "name": f"AI ({AI_PROVIDER})",
        "ok": ai_ok,
        "detail": ai_detail,
        "model": AI_MODEL,
        "latency_ms": ai_latency,
    }

    # 2. QLC+ WebSocket health. Inspect our persistent connection rather than
    # opening a fresh TCP probe — under load, QLC+ may not accept new TCP
    # connections within a tight timeout even though the existing WebSocket
    # is fully functional. Report the persistent connection's live state.
    ws_ok = False
    ws_detail = "unknown"
    try:
        if MOCK_DMX:
            ws_ok = True
            ws_detail = "connected (mock)"
        elif _qlc_ws is None:
            ws_detail = "not connected (will reconnect on next command)"
        elif getattr(_qlc_ws, "closed", False):
            ws_detail = "closed (will reconnect on next command)"
        else:
            ws_ok = True
            ws_detail = f"connected at {QLC_WS_URL}"
    except Exception as e:
        ws_detail = f"check failed: {e}"
    services["qlc_ws"] = {
        "name": "QLC+ WebSocket",
        "ok": ws_ok,
        "detail": ws_detail,
        "url": QLC_WS_URL if not MOCK_DMX else "mock",
    }

    # 3. QLC+ Service
    qlc_running = False
    qlc_detail = "unknown"
    try:
        if IS_LOCAL:
            # Running on the Pi — check systemd directly, no SSH
            result = execute_command(f"systemctl is-active {SERVICE_NAME}")
            qlc_running = result["success"] and "active" in result.get("output", "").strip()
            qlc_detail = result.get("output", "").strip() if result["success"] else "stopped"
        else:
            # Remote workstation — SSH via lightsctl
            result = execute_command(f"{LIGHTSCTL} status")
            qlc_running = result["success"] and "running" in result.get("output", "").lower()
            qlc_detail = "running" if qlc_running else "stopped / unreachable"
    except Exception:
        qlc_detail = "check failed"

    services["qlc_service"] = {
        "name": "QLC+ Service",
        "ok": qlc_running,
        "detail": qlc_detail,
    }

    # 4. Workspace file
    ws_exists = WORKSPACE_PATH.exists()
    services["workspace"] = {
        "name": "Workspace",
        "ok": ws_exists,
        "detail": str(WORKSPACE_PATH) if ws_exists else "file not found",
        "path": str(WORKSPACE_PATH),
    }

    overall_ok = all(s["ok"] for s in services.values())

    return jsonify({
        # Legacy fields for backward compat
        "qlc_running": qlc_running,
        "workspace": str(WORKSPACE_PATH),
        "workspace_exists": ws_exists,
        "ai_provider": AI_PROVIDER,
        "ai_model": AI_MODEL,
        # New rich status
        "ok": overall_ok,
        "services": services,
        "is_local": IS_LOCAL,
    })


@app.route("/api/templates", methods=["GET"])
def list_templates():
    """List available templates"""
    templates = [
        {"name": "youtube-studio", "description": "Bright neutral white for video recording"},
        {"name": "party", "description": "Vibrant alternating colors"},
        {"name": "ambient", "description": "Soft warm glow"},
        {"name": "spotlight", "description": "Single fixture at full"},
        {"name": "work-light", "description": "Bright neutral white"},
        {"name": "warm-white", "description": "Warm white (2700K-3000K)"},
        {"name": "cool-white", "description": "Cool white (5000K-6500K)"}
    ]

    return jsonify({"templates": templates})


@app.route("/api/scenes", methods=["GET"])
def list_scenes():
    """List existing scene functions from the loaded workspace, each with a swatch URI."""
    try:
        root = _workspace_root() if WORKSPACE_PATH.exists() else None
        scenes = get_workspace_scenes(root=root)
        fixtures = get_workspace_fixtures(root=root) if root is not None else []
        try:
            mtime = WORKSPACE_PATH.stat().st_mtime
        except OSError:
            mtime = 0.0

        engine = _engine_element(root) if root is not None else None
        elems_by_id = dict(_iter_scene_functions(engine)) if engine is not None else {}

        for s in scenes:
            try:
                elem = elems_by_id.get(s["id"])
                s["swatch"] = _get_scene_swatch(s["id"], elem, mtime, fixtures=fixtures) if elem is not None else None
            except Exception:
                s["swatch"] = None
        return jsonify({"scenes": scenes})
    except Exception as e:
        return jsonify({"error": str(e), "scenes": []}), 500


@app.route("/api/scenes/<scene_id>/activate", methods=["POST"])
def activate_scene(scene_id):
    """Apply an existing workspace scene live via WebSocket channel updates."""
    try:
        result = apply_existing_scene_live(scene_id)
        status = 200 if result["success"] else 404
        return jsonify({
            "success": result["success"],
            "action": {
                "action": "activate_scene",
                "parameters": {"scene": scene_id},
                "explanation": result["output"] if result["success"] else result["error"],
            },
            "output": result.get("output", ""),
            "error": result.get("error", "") if not result["success"] else "",
        }), status
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/scenes/save", methods=["POST"])
def save_scene():
    """Save a scene to the workspace permanently.

    Accepts either:
      - scene_xml: raw QLC+ scene XML to inject directly
      - snapshot: true — captures the current live channel state as a new scene

    Required: name (the scene name to save as)
    Optional: path (folder path within QLC+, e.g. "AI Generated")
    """
    try:
        data = request.get_json()
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"success": False, "error": "Scene name is required"}), 400

        scene_xml = data.get("scene_xml", "").strip()
        snapshot = data.get("snapshot", False)
        path = data.get("path", "AI Generated").strip()

        if not scene_xml and snapshot:
            # Build scene XML from current live channel values
            scene_xml = _snapshot_current_state_as_xml(name, path)
        elif not scene_xml and not snapshot:
            return jsonify({
                "success": False,
                "error": "Provide scene_xml or set snapshot=true"
            }), 400

        # If scene_xml was provided but doesn't have the right Name, patch it
        if scene_xml and name:
            scene_xml = _patch_scene_name(scene_xml, name, path)

        # Inject into workspace
        if not WORKSPACE_PATH.exists():
            return jsonify({"success": False, "error": "Workspace file not found"}), 500

        with _WORKSPACE_LOCK:
            next_id = get_next_scene_id()
            success = _inject_scene_into_workspace(scene_xml, next_id)

        if success:
            return jsonify({
                "success": True,
                "scene": {"id": next_id, "name": name, "path": path},
                "message": f"Scene '{name}' saved (ID {next_id})",
            })
        else:
            return jsonify({"success": False, "error": "Failed to inject scene into workspace"}), 500

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/scenes/snapshot", methods=["POST"])
def snapshot_scene():
    """Capture the current live channel state as a new saved scene.

    Body: { "name": "My Scene Name", "path": "AI Generated" }
    """
    try:
        data = request.get_json()
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"success": False, "error": "Scene name is required"}), 400
        path = data.get("path", "AI Generated").strip()

        scene_xml = _snapshot_current_state_as_xml(name, path)
        if not scene_xml:
            return jsonify({"success": False, "error": "Could not read current channel values"}), 500

        with _WORKSPACE_LOCK:
            next_id = get_next_scene_id()
            success = _inject_scene_into_workspace(scene_xml, next_id)

        if success:
            return jsonify({
                "success": True,
                "scene": {"id": next_id, "name": name, "path": path},
                "message": f"Snapshot saved as '{name}' (ID {next_id})",
            })
        else:
            return jsonify({"success": False, "error": "Failed to inject scene into workspace"}), 500

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def _snapshot_current_state_as_xml(name: str, path: str = "AI Generated") -> str:
    """Build a QLC+ scene XML from the current live channel values."""
    fixtures = get_workspace_fixtures()
    values = get_current_channel_values()
    if not values:
        return ""

    fixture_vals = []
    for fixture in fixtures:
        pairs = []
        for offset in range(fixture["channels"]):
            abs_ch = fixture["universe"] * 512 + fixture["address"] + offset + 1
            val = values.get(abs_ch, 0)
            # Use 1-based channel numbering for generated scenes
            pairs.append(f"{offset + 1},{val}")
        if pairs:
            fixture_vals.append(
                f'  <FixtureVal ID="{fixture["id"]}">{",".join(pairs)}</FixtureVal>'
            )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE Function>\n'
        f'<Function Type="Scene" Name="{_xml_escape(name)}" Path="{_xml_escape(path)}">\n'
        '  <Speed FadeIn="0" FadeOut="0" Duration="0"/>\n'
        + "\n".join(fixture_vals) + "\n"
        '</Function>'
    )
    return xml


def _patch_scene_name(scene_xml: str, name: str, path: str) -> str:
    """Ensure the scene XML has the correct Name and Path attributes."""
    import re
    # Replace Name attribute
    scene_xml = re.sub(
        r'Name="[^"]*"',
        f'Name="{_xml_escape(name)}"',
        scene_xml,
        count=1,
    )
    # Add or replace Path attribute
    if 'Path="' in scene_xml:
        scene_xml = re.sub(
            r'Path="[^"]*"',
            f'Path="{_xml_escape(path)}"',
            scene_xml,
            count=1,
        )
    else:
        scene_xml = scene_xml.replace(
            'Type="Scene"',
            f'Type="Scene" Path="{_xml_escape(path)}"',
            1,
        )
    return scene_xml


def _xml_escape(text: str) -> str:
    """Escape text for use in XML attributes."""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;"))


def _inject_scene_into_workspace(scene_xml: str, scene_id: int) -> bool:
    """Inject scene XML into the workspace file's Engine element.

    Uses Python XML manipulation directly (no external scripts needed).
    """
    try:
        with _WORKSPACE_LOCK:
            tree = ET.parse(WORKSPACE_PATH)
            root = tree.getroot()
            engine = _engine_element(root)
            if engine is None:
                log.error("workspace_missing_engine")
                return False

            # Parse the scene XML
            scene_root = ET.fromstring(scene_xml.strip().split("<!DOCTYPE Function>")[-1].strip()
                                       if "<!DOCTYPE" in scene_xml else scene_xml.strip())

            # Set the ID attribute
            scene_root.set("ID", str(scene_id))

            # Append to Engine
            engine.append(scene_root)

            # Write back
            _atomic_write_tree(tree)
        return True
    except Exception as e:
        log.error("scene_inject_failed", error=str(e))
        return False


@app.route("/api/groups", methods=["GET"])
def list_groups():
    """List fixture groups"""
    try:
        if not GROUPS_FILE.exists():
            return jsonify({"groups": []})

        with open(GROUPS_FILE) as f:
            groups_data = json.load(f)

        # Handle both formats: {"groups": {...}} and direct {...}
        if "groups" in groups_data:
            groups_dict = groups_data["groups"]
        else:
            groups_dict = groups_data

        groups = []
        for group_name, group_info in groups_dict.items():
            groups.append({
                "name": group_name,
                "fixtures": group_info.get("fixtures", []),
                "description": group_info.get("description", "")
            })

        return jsonify({"groups": groups})
    except Exception as e:
        return jsonify({"error": str(e), "groups": []}), 500


@app.route("/api/groups/<group_name>/template", methods=["POST"])
def apply_group_template(group_name):
    """Apply a template to a specific fixture group"""
    try:
        data = request.get_json()
        template = data.get("template")

        if not template:
            return jsonify({"success": False, "error": "Template name required"}), 400

        # Validate template against known whitelist (audit item #5)
        VALID_TEMPLATES = {
            "youtube-studio", "party", "ambient", "spotlight",
            "work-light", "warm-white", "cool-white",
        }
        if template not in VALID_TEMPLATES:
            return jsonify({
                "success": False,
                "error": f"Unknown template: {template}. Valid: {sorted(VALID_TEMPLATES)}",
            }), 400

        safe_name = group_name.replace("'", "'\\''")
        scene_file = tempfile.NamedTemporaryFile(prefix="qlc-group-template-", suffix=".xml", delete=False)
        scene_path = Path(scene_file.name)
        scene_file.close()
        if IS_LOCAL and WORKSPACE_PATH.exists():
            cmd = (f"{LIGHTSCTL} group-template '{safe_name}' {template}"
                   f" --workspace '{WORKSPACE_PATH}'"
                   f" --output '{scene_path}'")
        else:
            cmd = f"{LIGHTSCTL} group-template '{safe_name}' {template} --add-to-workspace"

        result = execute_command(cmd)

        # If local, apply the generated template scene immediately with no QLC+ restart.
        if IS_LOCAL and WORKSPACE_PATH.exists() and result["success"]:
            if scene_path.exists() and scene_path.read_text().strip():
                apply_result = apply_scene_xml_live(scene_path.read_text())
                result["success"] = apply_result["success"]
                result["output"] = (result.get("output", "") +
                                    f"\n{apply_result['output']}").strip()
                if not apply_result["success"]:
                    result["error"] = apply_result["error"]
            else:
                result["success"] = False
                result["error"] = "Scene file not created"
        scene_path.unlink(missing_ok=True)

        success = result["success"]
        return jsonify({
            "success": success,
            "action": {"action": "apply_template", "explanation": f"Applied {template} to {group_name}",
                       "parameters": {"template": template, "group": group_name}},
            "output": result.get("output", ""),
            "error": result.get("error", "") if not success else "",
            "log": result.get("error", "") if success else "",
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/fixtures", methods=["GET"])
def list_fixtures():
    """List all fixtures from workspace, with resolved per-channel info.

    Each fixture now includes a `channel_info` array with the channel's
    name, offset, role, preset, group, and colour metadata sourced from
    the QLC+ .qxf fixture definitions when available.
    """
    try:
        fixtures = get_workspace_fixtures()
        for fixture in fixtures:
            fixture["channel_info"] = _fixture_channels_info(fixture)
        return jsonify({"fixtures": fixtures})
    except Exception as e:
        return jsonify({"error": str(e), "fixtures": []}), 500


@app.route("/api/fixture_channels/<int:fixture_id>", methods=["GET"])
def fixture_channels(fixture_id):
    """Return resolved channel info for a single fixture.

    Reads the fixture's .qxf definition (manufacturer + model + mode) and
    returns each channel's offset, name, role, preset, group, and colour.
    Useful for the UI to render correct labels and color hints.
    """
    try:
        fixtures = get_workspace_fixtures()
        match = next((f for f in fixtures if str(f["id"]) == str(fixture_id)), None)
        if match is None:
            return jsonify({"error": f"Fixture {fixture_id} not found"}), 404
        return jsonify({
            "fixture": {
                "id": match["id"],
                "name": match["name"],
                "manufacturer": match["manufacturer"],
                "model": match["model"],
                "mode": match["mode"],
                "channels": match["channels"],
            },
            "channel_info": _fixture_channels_info(match),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/fixture_definitions/reload", methods=["POST"])
def reload_fixture_definitions():
    """Force reload of cached .qxf fixture definitions from disk."""
    try:
        count = fixture_definitions.reload_definitions()
        return jsonify({"success": True, "indexed": count})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/channel", methods=["POST"])
def set_channel():
    """Set DMX channel value"""
    try:
        data = request.get_json()
        fixture_id = data.get("fixture_id")
        channel_offset = data.get("channel", 0)  # 0-based offset within fixture
        value = data.get("value", 0)

        if fixture_id is None or value is None:
            return jsonify({"success": False, "error": "Missing fixture_id or value"}), 400

        # Get fixture info from workspace
        tree = ET.parse(WORKSPACE_PATH)
        root = tree.getroot()
        ns = {'qlc': 'http://www.qlcplus.org/Workspace'}

        fixture = root.find(f".//qlc:Fixture[qlc:ID='{fixture_id}']", ns)
        if fixture is None:
            return jsonify({"success": False, "error": f"Fixture {fixture_id} not found"}), 404

        universe_elem = fixture.find("qlc:Universe", ns)
        address_elem = fixture.find("qlc:Address", ns)

        universe = int(universe_elem.text) if universe_elem is not None else 0
        base_address = int(address_elem.text) if address_elem is not None else 0

        # Calculate actual DMX address (1-based)
        dmx_address = base_address + channel_offset + 1

        # Set channel value
        success = set_channel_value(universe, dmx_address, int(value))

        return jsonify({
            "success": success,
            "fixture_id": fixture_id,
            "universe": universe,
            "address": dmx_address,
            "value": value
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/channel_values", methods=["GET"])
def get_channel_values():
    """Get current DMX channel values from QLC+ via WebSocket.

    QLC+ 4.x response format for getChannelsValues:
        QLC+API|getChannelsValues|<universe>|<ch>|<val>|<pct>.<color>|<ch>|<val>|...
    where universe and ch are 1-based.

    Returns dict keyed by 1-based absolute channel number within universe 0:
        { "1": 0, "4": 241, "7": 255, ... }
    """
    # Determine how many channels we need (highest fixture end address)
    max_ch = 32
    try:
        for fixture in get_workspace_fixtures():
            top = fixture["universe"] * 512 + fixture["address"] + fixture["channels"]
            max_ch = max(max_ch, top)
    except Exception:
        pass

    try:
        values = {str(k): v for k, v in get_current_channel_values(max_ch).items()}
        return jsonify({"values": values})
    except Exception as e:
        return jsonify({"values": {}, "error": str(e)})


# =============================================================================
# Tier 1 endpoints — group CRUD, scene management, identify, blackout, batch
# =============================================================================
#
# These extend the API surface without changing existing behavior. Each block
# is bracketed with a short rationale so it's easy to find later.


# ----------------------------------------------------------------------------
# Group CRUD
# ----------------------------------------------------------------------------
# Storage: ~/.qlcplus/fixture_groups.json. The on-disk format is
#     {"groups": {<name>: {"fixtures": [<id>...], "description": "..."}}}
# Some legacy files store the inner dict directly without the "groups" key —
# _load_groups handles both, _save_groups always writes the wrapped form.

def _load_groups() -> dict:
    """Return the groups dict in {name: {fixtures, description}} form.

    Tolerates the legacy unwrapped format. Returns an empty dict if the file
    doesn't exist yet.
    """
    if not GROUPS_FILE.exists():
        return {}
    try:
        data = json.loads(GROUPS_FILE.read_text())
    except json.JSONDecodeError:
        return {}
    if isinstance(data, dict) and "groups" in data and isinstance(data["groups"], dict):
        return data["groups"]
    return data if isinstance(data, dict) else {}


def _save_groups(groups: dict) -> None:
    """Persist the groups dict in the canonical wrapped format."""
    GROUPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    GROUPS_FILE.write_text(json.dumps({"groups": groups}, indent=2))


def _existing_fixture_ids() -> set:
    """Set of int fixture IDs declared in the current workspace."""
    return {int(f["id"]) for f in get_workspace_fixtures()}


def _normalize_fixture_ids(raw):
    """Coerce a fixture_ids input into a deduped list of ints.

    Accepts list of ints / numeric strings, drops anything unparseable.
    """
    result = []
    seen = set()
    for v in raw or []:
        try:
            fid = int(v)
        except (TypeError, ValueError):
            continue
        if fid not in seen:
            seen.add(fid)
            result.append(fid)
    return result


@app.route("/api/groups", methods=["POST"])
def create_group():
    """Create a new fixture group.

    Body:
        {
          "name": "key-lights",
          "fixtures": [0, 3, 4],
          "description": "Front key wash"   # optional
        }
    """
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "name is required"}), 400

    groups = _load_groups()
    if name in groups:
        return jsonify({"success": False, "error": f"Group '{name}' already exists"}), 409

    fixture_ids = _normalize_fixture_ids(data.get("fixtures"))
    valid_ids = _existing_fixture_ids()
    unknown = [fid for fid in fixture_ids if fid not in valid_ids]
    if unknown:
        return jsonify({
            "success": False,
            "error": f"Unknown fixture IDs: {unknown}",
            "valid_fixture_ids": sorted(valid_ids),
        }), 400

    groups[name] = {
        "fixtures": fixture_ids,
        "description": (data.get("description") or "").strip(),
    }
    _save_groups(groups)
    _emit("group_modified", {"group_name": name, "action": "created"})
    return jsonify({
        "success": True,
        "group": {
            "name": name,
            "fixtures": fixture_ids,
            "description": groups[name]["description"],
        },
    })


@app.route("/api/groups/<group_name>", methods=["DELETE"])
def delete_group(group_name):
    """Remove a fixture group. Idempotent: returns 404 if missing."""
    groups = _load_groups()
    if group_name not in groups:
        return jsonify({"success": False, "error": f"Group '{group_name}' not found"}), 404
    del groups[group_name]
    _save_groups(groups)
    _emit("group_modified", {"group_name": group_name, "action": "deleted"})
    return jsonify({"success": True})


@app.route("/api/groups/<group_name>", methods=["PATCH"])
def update_group(group_name):
    """Rename a group or update its description / fixture list.

    Body (all fields optional):
        {
          "name": "new-name",
          "description": "...",
          "fixtures": [0, 3, 4]   # replaces the full list
        }
    """
    data = request.get_json(silent=True) or {}
    groups = _load_groups()
    if group_name not in groups:
        return jsonify({"success": False, "error": f"Group '{group_name}' not found"}), 404

    group = groups[group_name]
    new_name = (data.get("name") or "").strip()
    if new_name and new_name != group_name:
        if new_name in groups:
            return jsonify({
                "success": False,
                "error": f"Group '{new_name}' already exists"
            }), 409
        groups[new_name] = group
        del groups[group_name]
        group_name = new_name

    if "description" in data:
        groups[group_name]["description"] = (data.get("description") or "").strip()

    if "fixtures" in data:
        fixture_ids = _normalize_fixture_ids(data.get("fixtures"))
        valid_ids = _existing_fixture_ids()
        unknown = [fid for fid in fixture_ids if fid not in valid_ids]
        if unknown:
            return jsonify({
                "success": False,
                "error": f"Unknown fixture IDs: {unknown}"
            }), 400
        groups[group_name]["fixtures"] = fixture_ids

    _save_groups(groups)
    _emit("group_modified", {"group_name": group_name, "action": "updated"})
    return jsonify({
        "success": True,
        "group": {
            "name": group_name,
            "fixtures": groups[group_name].get("fixtures", []),
            "description": groups[group_name].get("description", ""),
        },
    })


@app.route("/api/groups/<group_name>/fixtures", methods=["POST"])
def add_fixtures_to_group(group_name):
    """Append fixture IDs to a group (preserving existing members).

    Body: { "fixtures": [0, 3, 4] }
    """
    data = request.get_json(silent=True) or {}
    groups = _load_groups()
    if group_name not in groups:
        return jsonify({"success": False, "error": f"Group '{group_name}' not found"}), 404

    to_add = _normalize_fixture_ids(data.get("fixtures"))
    valid_ids = _existing_fixture_ids()
    unknown = [fid for fid in to_add if fid not in valid_ids]
    if unknown:
        return jsonify({
            "success": False,
            "error": f"Unknown fixture IDs: {unknown}"
        }), 400

    current = list(groups[group_name].get("fixtures") or [])
    seen = set(current)
    for fid in to_add:
        if fid not in seen:
            current.append(fid)
            seen.add(fid)
    groups[group_name]["fixtures"] = current
    _save_groups(groups)
    _emit("group_modified", {"group_name": group_name, "action": "updated"})

    return jsonify({
        "success": True,
        "group": {
            "name": group_name,
            "fixtures": current,
            "description": groups[group_name].get("description", ""),
        },
    })


@app.route("/api/groups/<group_name>/fixtures", methods=["DELETE"])
def remove_fixtures_from_group(group_name):
    """Remove fixture IDs from a group. Missing IDs are ignored silently.

    Body: { "fixtures": [3, 4] }
    """
    data = request.get_json(silent=True) or {}
    groups = _load_groups()
    if group_name not in groups:
        return jsonify({"success": False, "error": f"Group '{group_name}' not found"}), 404

    to_remove = set(_normalize_fixture_ids(data.get("fixtures")))
    current = [fid for fid in (groups[group_name].get("fixtures") or []) if fid not in to_remove]
    groups[group_name]["fixtures"] = current
    _save_groups(groups)
    _emit("group_modified", {"group_name": group_name, "action": "updated"})

    return jsonify({
        "success": True,
        "group": {
            "name": group_name,
            "fixtures": current,
            "description": groups[group_name].get("description", ""),
        },
    })


# ----------------------------------------------------------------------------
# Stage layout — fixture-position persistence
# ----------------------------------------------------------------------------
# Storage: ~/.qlcplus/stage_layout.json, same tolerant load/save pattern as
#     GROUPS_FILE / CUE_LISTS_FILE. Shape:
#     {"room": {"width": <num>, "height": <num>}, "positions": {"<fixture_id>": {"x": <num>, "y": <num>}}}
# Fixture IDs are stored as string keys in the "positions" dict (unlike
# groups, which store fixture IDs as an int list) since JSON object keys are
# always strings.

def _load_stage_layout() -> dict:
    """Return the stage layout dict with "room" and "positions" keys.

    Returns the default empty shape if the file is missing, unreadable, or
    not a JSON object. Positions are returned as stored, with no check
    against the current workspace's fixture list — a position for a fixture
    ID that no longer exists is returned unchanged rather than dropped.
    """
    if not STAGE_LAYOUT_FILE.exists():
        return {"room": {}, "positions": {}}
    try:
        data = json.loads(STAGE_LAYOUT_FILE.read_text())
    except json.JSONDecodeError:
        return {"room": {}, "positions": {}}
    if not isinstance(data, dict):
        return {"room": {}, "positions": {}}
    data.setdefault("room", {})
    data.setdefault("positions", {})
    return data


def _save_stage_layout(layout: dict) -> None:
    """Persist the stage layout dict."""
    STAGE_LAYOUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    STAGE_LAYOUT_FILE.write_text(json.dumps(layout, indent=2))


@app.route("/api/stage_layout", methods=["GET"])
def get_stage_layout():
    """Return the stored stage layout (room dimensions + fixture positions).

    Positions are returned exactly as stored, even for fixture IDs that are
    no longer present in the current workspace — this endpoint never
    cross-checks against the workspace fixture list, so it can't crash on a
    stale position entry.
    """
    return jsonify(_load_stage_layout())


@app.route("/api/stage_layout", methods=["POST"])
def save_stage_layout():
    """Save room dimensions and fixture positions.

    Body:
        {
          "room": {"width": 20, "height": 12},
          "positions": {"0": {"x": 1.2, "y": 3.4}, "3": {"x": 5.0, "y": 2.0}}
        }

    Both fields are optional and default to {}. Entries in "positions" whose
    value isn't a dict with numeric "x"/"y" are dropped rather than failing
    the whole request. Fixture IDs are not validated against the current
    workspace — a position may be saved for a fixture that doesn't exist
    (yet, or anymore).
    """
    data = request.get_json(silent=True) or {}

    room = data.get("room")
    room = room if isinstance(room, dict) else {}

    positions = {}
    for fid, pos in (data.get("positions") or {}).items():
        if not isinstance(pos, dict):
            continue
        try:
            x = float(pos["x"])
            y = float(pos["y"])
        except (KeyError, TypeError, ValueError):
            continue
        positions[str(fid)] = {"x": x, "y": y}

    layout = {"room": room, "positions": positions}
    _save_stage_layout(layout)
    return jsonify({"success": True, **layout})


# ----------------------------------------------------------------------------
# Scene management — describe / delete / rename / duplicate
# ----------------------------------------------------------------------------

def _scene_value_breakdown(scene_root, fixtures=None) -> list:
    """Convert a scene <Function> element to a fixture-keyed value breakdown.

    Returns a list of dicts:
        [{ "fixture_id": 0, "fixture_name": "SlimPAR Pro",
           "channels": [{ "offset": 0, "name": "Dimmer", "value": 200 }, ...] }, ...]

    The channel name comes from the .qxf parser when available.
    """
    fixtures_by_id = {
        str(f["id"]): f for f in (fixtures if fixtures is not None else get_workspace_fixtures())
    }
    out = []
    for fixture_val in _find_children(scene_root, "FixtureVal"):
        fid = fixture_val.get("ID")
        fixture = fixtures_by_id.get(str(fid))
        if not fixture or not fixture_val.text:
            continue

        raw_parts = [p.strip() for p in fixture_val.text.split(",") if p.strip() != ""]
        pairs = []
        for i in range(0, len(raw_parts) - 1, 2):
            try:
                pairs.append((int(raw_parts[i]), int(raw_parts[i + 1])))
            except ValueError:
                continue
        if not pairs:
            continue

        channel_info = _fixture_channels_info(fixture)
        info_by_offset = {ci["offset"]: ci for ci in channel_info}

        channels = []
        for offset, value in _decode_fixture_val_pairs(pairs, int(fixture.get("channels", 0))):
            ci = info_by_offset.get(offset, {})
            channels.append({
                "offset": offset,
                "name": ci.get("name", f"Ch {offset + 1}"),
                "role": ci.get("role"),
                "value": value,
            })

        out.append({
            "fixture_id": int(fid),
            "fixture_name": fixture.get("name", ""),
            "channels": channels,
        })
    return out


def _scene_swatch_svg(scene_root, fixtures=None) -> str:
    """Return a data:image/svg+xml URI with one color band per fixture.

    Falls back to a dark neutral strip when no color roles are resolvable.
    """
    breakdown = _scene_value_breakdown(scene_root, fixtures=fixtures)
    if not breakdown:
        return _neutral_swatch_svg()

    bands = []
    for fix in breakdown:
        rgb = _fixture_values_to_rgb(fix["channels"])
        bands.append(f"rgb({rgb[0]},{rgb[1]},{rgb[2]})" if rgb else "rgb(34,34,34)")

    n = len(bands)
    bw = 100.0 / n
    # Use viewBox="0 0 100 1" coordinate space — no % units needed, no extra encoding.
    rects = "".join(
        f"<rect x='{i * bw:.3f}' y='0' width='{bw:.3f}' height='1' fill='{col}'/>"
        for i, col in enumerate(bands)
    )
    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 1' "
        f"preserveAspectRatio='none'>{rects}</svg>"
    )
    # Encode for safe embedding in CSS url('...') inside an HTML style="" attribute.
    encoded = svg.replace("<", "%3C").replace(">", "%3E").replace("'", "%27")
    return f"data:image/svg+xml;charset=utf-8,{encoded}"


def _neutral_swatch_svg() -> str:
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1 1'>"
        "<rect width='1' height='1' fill='rgb(34,34,34)'/></svg>"
    )
    encoded = svg.replace("<", "%3C").replace(">", "%3E").replace("'", "%27")
    return f"data:image/svg+xml;charset=utf-8,{encoded}"


def _get_scene_swatch(scene_id: int, scene_elem, workspace_mtime: float, fixtures=None) -> str | None:
    """Return cached swatch URI for a scene, re-computing when workspace changed."""
    global _scene_swatch_cache, _scene_swatch_cache_mtime
    if workspace_mtime != _scene_swatch_cache_mtime:
        _scene_swatch_cache.clear()
        _scene_swatch_cache_mtime = workspace_mtime
    if scene_id not in _scene_swatch_cache:
        try:
            _scene_swatch_cache[scene_id] = _scene_swatch_svg(scene_elem, fixtures=fixtures)
        except Exception:
            _scene_swatch_cache[scene_id] = None
    return _scene_swatch_cache[scene_id]


@app.route("/api/scenes/<scene_id>", methods=["GET"])
def describe_scene(scene_id):
    """Return the contents of a saved scene: fixture/channel/value breakdown."""
    scene = _find_scene_element(scene_id)
    if scene is None:
        return jsonify({"success": False, "error": f"Scene not found: {scene_id}"}), 404
    return jsonify({
        "success": True,
        "scene": {
            "id": int(scene.get("ID", "0")) if (scene.get("ID") or "").isdigit() else scene.get("ID"),
            "name": scene.get("Name", ""),
            "path": scene.get("Path", ""),
        },
        "fixtures": _scene_value_breakdown(scene),
    })


@app.route("/api/scenes/<scene_id>", methods=["DELETE"])
def delete_scene(scene_id):
    """Delete a saved scene from the workspace permanently."""
    try:
        with _WORKSPACE_LOCK:
            tree = ET.parse(WORKSPACE_PATH)
            root = tree.getroot()
            engine = _engine_element(root)
            if engine is None:
                return jsonify({"success": False, "error": "No Engine element in workspace"}), 500

            needle = str(scene_id).strip().lower()
            ns = "http://www.qlcplus.org/Workspace"
            target = None
            for func in list(engine.findall(f"{{{ns}}}Function")) + list(engine.findall("Function")):
                if func.get("Type") != "Scene":
                    continue
                if func.get("ID") == str(scene_id) or (func.get("Name") or "").lower() == needle:
                    target = func
                    break
            if target is None:
                return jsonify({"success": False, "error": f"Scene not found: {scene_id}"}), 404

            engine.remove(target)
            _atomic_write_tree(tree)
        return jsonify({
            "success": True,
            "deleted": {"id": target.get("ID"), "name": target.get("Name")},
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/scenes/<scene_id>", methods=["PATCH"])
def rename_scene(scene_id):
    """Rename a scene. Body: { "name": "New Name", "path": "AI Generated" }"""
    data = request.get_json(silent=True) or {}
    new_name = (data.get("name") or "").strip()
    new_path = data.get("path")
    if not new_name and new_path is None:
        return jsonify({"success": False, "error": "Provide name and/or path"}), 400

    try:
        with _WORKSPACE_LOCK:
            tree = ET.parse(WORKSPACE_PATH)
            root = tree.getroot()
            engine = _engine_element(root)
            if engine is None:
                return jsonify({"success": False, "error": "No Engine element in workspace"}), 500

            needle = str(scene_id).strip().lower()
            ns = "http://www.qlcplus.org/Workspace"
            target = None
            for func in list(engine.findall(f"{{{ns}}}Function")) + list(engine.findall("Function")):
                if func.get("Type") != "Scene":
                    continue
                if func.get("ID") == str(scene_id) or (func.get("Name") or "").lower() == needle:
                    target = func
                    break
            if target is None:
                return jsonify({"success": False, "error": f"Scene not found: {scene_id}"}), 404

            if new_name:
                target.set("Name", new_name)
            if new_path is not None:
                target.set("Path", new_path)
            _atomic_write_tree(tree)
        return jsonify({
            "success": True,
            "scene": {
                "id": target.get("ID"),
                "name": target.get("Name"),
                "path": target.get("Path", ""),
            },
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/scenes/<scene_id>/duplicate", methods=["POST"])
def duplicate_scene(scene_id):
    """Duplicate an existing scene under a new name. Body: { "name": "..." }"""
    data = request.get_json(silent=True) or {}
    new_name = (data.get("name") or "").strip()
    if not new_name:
        return jsonify({"success": False, "error": "name is required"}), 400

    try:
        import copy as _copy
        with _WORKSPACE_LOCK:
            tree = ET.parse(WORKSPACE_PATH)
            root = tree.getroot()
            engine = _engine_element(root)
            if engine is None:
                return jsonify({"success": False, "error": "No Engine element in workspace"}), 500

            needle = str(scene_id).strip().lower()
            ns = "http://www.qlcplus.org/Workspace"
            source = None
            for func in list(engine.findall(f"{{{ns}}}Function")) + list(engine.findall("Function")):
                if func.get("Type") != "Scene":
                    continue
                if func.get("ID") == str(scene_id) or (func.get("Name") or "").lower() == needle:
                    source = func
                    break
            if source is None:
                return jsonify({"success": False, "error": f"Scene not found: {scene_id}"}), 404

            clone = _copy.deepcopy(source)
            clone.set("Name", new_name)
            new_id = get_next_scene_id()
            clone.set("ID", str(new_id))
            engine.append(clone)
            _atomic_write_tree(tree)
        return jsonify({
            "success": True,
            "scene": {
                "id": new_id,
                "name": new_name,
                "source_id": source.get("ID"),
                "source_name": source.get("Name"),
            },
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ----------------------------------------------------------------------------
# identify_fixture — visual ping
# ----------------------------------------------------------------------------

async def _identify_fixture_async(fixture, duration=2.0, pulses=4):
    """Flash a fixture's dimmer/brightness channels on-off-on-off then restore.

    Uses the persistent QLC+ WebSocket. Total pattern time ≈ `duration`
    seconds spread across `pulses` on-off cycles, then a final restore frame.
    """
    ws = await _ensure_qlc_ws()
    roles = _fixture_roles(fixture)
    brightness_offsets = roles.get("brightness", [])
    if not brightness_offsets:
        # Fall back to channel 0 if we can't find a brightness channel
        brightness_offsets = [0]

    # Read current values so we can restore them when done
    max_ch = fixture["universe"] * 512 + fixture["address"] + fixture["channels"]
    current = await _fetch_channel_values(max_ch)

    abs_channels = [_absolute_channel(fixture, offset) for offset in brightness_offsets]
    half_period = duration / (pulses * 2) if pulses > 0 else 0.25

    async def _send(value):
        commands = [f"CH|{ch}|{value}" for ch in abs_channels]
        async with _qlc_ws_lock:
            for cmd in commands:
                await ws.send(cmd)

    try:
        for _ in range(pulses):
            await _send(255)
            await asyncio.sleep(half_period)
            await _send(0)
            await asyncio.sleep(half_period)
        # Restore previous values
        restore_commands = [f"CH|{ch}|{int(current.get(ch, 0))}" for ch in abs_channels]
        async with _qlc_ws_lock:
            for cmd in restore_commands:
                await ws.send(cmd)
        return True
    except Exception as e:
        log.error("identify_fixture_failed", error=str(e))
        return False


@app.route("/api/fixtures/<int:fixture_id>/identify", methods=["POST"])
def identify_fixture(fixture_id):
    """Pulse a single fixture so the user can visually identify which is which.

    Body (optional):
        { "duration": 2.0, "pulses": 4 }
    """
    fixtures = get_workspace_fixtures()
    match = next((f for f in fixtures if int(f["id"]) == int(fixture_id)), None)
    if match is None:
        return jsonify({"success": False, "error": f"Fixture {fixture_id} not found"}), 404

    data = request.get_json(silent=True) or {}
    try:
        duration = max(0.5, min(10.0, float(data.get("duration", 2.0))))
    except (TypeError, ValueError):
        duration = 2.0
    try:
        pulses = max(1, min(10, int(data.get("pulses", 4))))
    except (TypeError, ValueError):
        pulses = 4

    try:
        success = _qlc_run(
            _identify_fixture_async(match, duration=duration, pulses=pulses),
            timeout=duration + 5,
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    return jsonify({
        "success": success,
        "fixture": {
            "id": match["id"],
            "name": match["name"],
        },
        "duration": duration,
        "pulses": pulses,
    })


# ----------------------------------------------------------------------------
# blackout — instant zero on all (or grouped) fixtures
# ----------------------------------------------------------------------------

def _do_blackout(target_groups=None):
    """Instantly drive every channel of the targeted fixtures to 0.

    Distinct from fade(target:0, duration:0) because it writes EVERY channel
    on the fixture (not just brightness-role channels), so any active strobe,
    macro, or color state is also cleared. Use for "kill it all" moments.
    """
    fixtures = _target_fixtures(target_groups)

    updates = []
    for fixture in fixtures:
        for offset in range(int(fixture.get("channels", 0))):
            updates.append((_absolute_channel(fixture, offset), 0))

    success = set_channel_values(updates) if updates else True
    return {
        "success": success,
        "fixtures": len(fixtures),
        "channels_zeroed": len(updates),
        "groups": target_groups,
    }


@app.route("/api/blackout", methods=["POST"])
def blackout():
    """Instantly drive every channel of the targeted fixtures to 0.

    Body (optional): { "groups": ["key-lights"] }  # defaults to all fixtures
    """
    data = request.get_json(silent=True) or {}
    target_groups = data.get("groups") or None
    return jsonify(_do_blackout(target_groups))


# ----------------------------------------------------------------------------
# batch_action — dispatch multiple actions in a single HTTP round trip
# ----------------------------------------------------------------------------

@app.route("/api/batch", methods=["POST"])
def batch_action():
    """Execute an ordered list of actions in one request.

    Body:
        {
          "actions": [
            { "action": "adjust_color", "parameters": {"color": "warm"}, "groups": ["key-lights"] },
            { "action": "adjust_color", "parameters": {"color": "cool"}, "groups": ["fill-lights"] },
            { "action": "fade", "parameters": {"target": "0", "duration": "5"} }
          ]
        }

    Returns a per-action result array plus an aggregate success flag.
    Stops on first failure and reports which step broke; remaining
    actions are skipped (stop_on_error: true by default).
    """
    import time as _time
    data = request.get_json(silent=True) or {}
    actions = data.get("actions") or []
    stop_on_error = data.get("stop_on_error", True)

    if not isinstance(actions, list) or not actions:
        return jsonify({"success": False, "error": "Provide non-empty 'actions' array"}), 400

    # Cap batch size to prevent amplification abuse (audit item #32)
    MAX_BATCH_SIZE = 20
    if len(actions) > MAX_BATCH_SIZE:
        return jsonify({
            "success": False,
            "error": f"Too many actions ({len(actions)}). Maximum is {MAX_BATCH_SIZE} per request.",
        }), 400

    results = []
    overall_success = True
    t0 = _time.time()

    for i, step in enumerate(actions):
        if not isinstance(step, dict):
            results.append({"index": i, "success": False, "error": "step must be an object"})
            overall_success = False
            if stop_on_error:
                break
            continue

        action_data = {
            "action": step.get("action"),
            "parameters": step.get("parameters", {}) or {},
            "explanation": step.get("explanation", ""),
        }
        groups = step.get("groups") or None

        try:
            result = execute_lighting_action(action_data, target_groups=groups)
            results.append({
                "index": i,
                "action": action_data["action"],
                "success": result["success"],
                "output": result.get("output", ""),
                "error": result.get("error", "") if not result["success"] else "",
            })
            if not result["success"]:
                overall_success = False
                if stop_on_error:
                    break
        except Exception as e:
            results.append({
                "index": i,
                "action": action_data["action"],
                "success": False,
                "error": str(e),
            })
            overall_success = False
            if stop_on_error:
                break

    return jsonify({
        "success": overall_success,
        "executed": len(results),
        "total_requested": len(actions),
        "results": results,
        "debug": {
            "total_ms": round((_time.time() - t0) * 1000),
        },
    })


# =============================================================================
# Diagnostics surface (issue #9)
# =============================================================================
#
# test_dmx       — visible R/G/B sweep across every color-aware fixture
# get_logs      — read systemd journal for a known service (allowlisted)
# get_system   — Pi-level health: CPU temp, load, mem, disk, uptime, USB,
#                  service status
#
# All three are intended to help an agent / operator debug a misbehaving
# rig without SSHing in. None of them touch persistent state; test_dmx
# saves & restores channel values so the rig returns to its pre-test look.


# ----------------------------------------------------------------------------
# test_dmx
# ----------------------------------------------------------------------------

async def _test_dmx_async(fixtures, duration=5.0):
    """Drive a known-good R → G → B → off sweep across every color-aware
    fixture's channels, then restore the previous values.

    Phase split: 4 phases of duration/4 each (red, green, blue, off-restore).
    Within each phase we send one frame at the start; QLC+ holds it until
    the next frame.
    """
    ws = await _ensure_qlc_ws()

    # Snapshot pre-test channel values so we can restore precisely
    max_ch = 32
    for f in fixtures:
        max_ch = max(max_ch, f["universe"] * 512 + f["address"] + f["channels"])
    pre_values = await _fetch_channel_values(max_ch)

    # Collect (absolute_channel, role) tuples for every targeted fixture's
    # color-relevant channels. Falls back to brightness if no color roles
    # exist (e.g. single-channel dimmers — they'll just pulse on/off).
    color_role_priorities = [
        ("red", "green", "blue"),               # RGB fixtures
        ("warm", "cool", "amber"),              # WWA / tungsten fixtures
        ("white",),                             # white-only
    ]
    fixture_plans = []
    for fixture in fixtures:
        roles = _fixture_roles(fixture)
        plan = {}
        for triplet in color_role_priorities:
            if any(r in roles for r in triplet):
                for r in triplet:
                    if r in roles and isinstance(roles[r], int):
                        plan[r] = _absolute_channel(fixture, roles[r])
                break
        # Fall back to brightness/dimmer for fixtures without color channels
        if not plan and "brightness" in roles:
            plan["dimmer"] = _absolute_channel(fixture, roles["brightness"][0])
        fixture_plans.append((fixture, plan, roles))

    async def _send_frame(commands):
        async with _qlc_ws_lock:
            for cmd in commands:
                await ws.send(cmd)

    # Map phase → role pattern for each fixture type
    def _phase_value(plan, phase):
        """Return a {abs_channel: value} map for the given phase index."""
        out = {}
        for role, abs_ch in plan.items():
            if role == "red":
                out[abs_ch] = 255 if phase == 0 else 0
            elif role == "green":
                out[abs_ch] = 255 if phase == 1 else 0
            elif role == "blue":
                out[abs_ch] = 255 if phase == 2 else 0
            elif role == "warm":
                out[abs_ch] = 255 if phase == 0 else 0
            elif role == "cool":
                out[abs_ch] = 255 if phase == 2 else 0
            elif role == "amber":
                out[abs_ch] = 255 if phase == 1 else 0
            elif role == "white":
                out[abs_ch] = 255 if phase in (0, 1, 2) else 0
            elif role == "dimmer":
                out[abs_ch] = 255 if phase in (0, 1, 2) else 0
        return out

    phase_duration = max(0.5, duration / 4)
    try:
        for phase in range(3):
            commands = []
            for _fixture, plan, roles in fixture_plans:
                # Drive dimmer channel up too if it exists (so RGB-only
                # writes don't appear black due to a closed dimmer)
                if "dimmer" in roles and isinstance(roles["dimmer"], int):
                    dimmer_ch = _absolute_channel(_fixture, roles["dimmer"])
                    commands.append(f"CH|{dimmer_ch}|255")
                for ch, val in _phase_value(plan, phase).items():
                    commands.append(f"CH|{ch}|{val}")
            await _send_frame(commands)
            await asyncio.sleep(phase_duration)

        # Restore phase — push every channel that we touched back to its
        # pre-test value
        restore_commands = []
        seen_channels = set()
        for _fixture, plan, roles in fixture_plans:
            for ch in plan.values():
                seen_channels.add(ch)
            if "dimmer" in roles and isinstance(roles["dimmer"], int):
                seen_channels.add(_absolute_channel(_fixture, roles["dimmer"]))
        for ch in sorted(seen_channels):
            restore_commands.append(f"CH|{ch}|{int(pre_values.get(ch, 0))}")
        await _send_frame(restore_commands)
        return True
    except Exception as e:
        log.error("test_dmx_failed", error=str(e))
        return False


@app.route("/api/diagnostics/test_dmx", methods=["POST"])
def diagnostics_test_dmx():
    """Run a known-good color sweep across every targeted fixture.

    Body (optional):
        { "duration": 5.0, "groups": ["key-lights"] }

    Returns after the sweep completes (typically 5 seconds). Channel state
    is snapshotted before the test and restored at the end, so the rig
    returns to whatever it looked like before the call.
    """
    data = request.get_json(silent=True) or {}
    try:
        duration = max(2.0, min(30.0, float(data.get("duration", 5.0))))
    except (TypeError, ValueError):
        duration = 5.0
    target_groups = data.get("groups") or None
    fixtures = _target_fixtures(target_groups)

    if not fixtures:
        return jsonify({
            "success": False,
            "error": "No fixtures matched the request",
        }), 400

    try:
        success = _qlc_run(
            _test_dmx_async(fixtures, duration=duration),
            timeout=duration + 10,
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    return jsonify({
        "success": success,
        "fixtures_tested": len(fixtures),
        "duration": duration,
        "groups": target_groups,
        "pattern": "red → green → blue → restore",
    })


# ----------------------------------------------------------------------------
# get_logs — read systemd journals (allowlisted services only)
# ----------------------------------------------------------------------------

LOG_ALLOWED_SERVICES = {
    "qlcplus-web": "qlcplus-web.service",
    "lighting-control": "lighting-control.service",
    "lighting-mcp": "lighting-mcp.service",
    "nginx": "nginx.service",
    "dmx-monitor": "dmx-monitor.service",
}


def _parse_systemd_load_state(output: str) -> str:
    """Extract the value of `LoadState=` from `systemctl show` output.

    `systemctl show -p LoadState <unit>` returns a single key=value line on
    its own line, e.g.:
        LoadState=loaded
        LoadState=not-found
        LoadState=masked

    Returns the value (without the prefix), or an empty string if the line
    is missing / malformed. Pure / testable — no subprocess invocation.
    """
    for line in (output or "").splitlines():
        line = line.strip()
        if line.startswith("LoadState="):
            return line.split("=", 1)[1].strip()
    return ""


def _systemd_unit_state(unit: str, exec_fn=execute_command) -> str:
    """Return one of the diagnostic states for a systemd unit:

      - "not_installed"  — unit file is missing (LoadState=not-found)
      - "masked"         — unit file is masked (LoadState=masked)
      - "active"         — running
      - "inactive"       — loaded but stopped
      - "failed"         — start failed
      - "activating" / "deactivating"
      - "unknown"        — couldn't determine

    We check `LoadState` first because `systemctl is-active` returns
    "inactive" for *both* a stopped unit AND a missing unit — which made
    the UI's "lighting-mcp: inactive" warning indistinguishable from
    "you never installed it." Now the UI can render those two cases
    differently.
    """
    load = _parse_systemd_load_state(
        exec_fn(f"systemctl show -p LoadState {unit}").get("output") or ""
    )
    if load == "not-found":
        return "not_installed"
    if load == "masked":
        return "masked"
    active = (exec_fn(f"systemctl is-active {unit}").get("output") or "").strip()
    return active or "unknown"


@app.route("/api/diagnostics/logs/<service>", methods=["GET"])
def diagnostics_logs(service):
    """Return the last N lines of a service's systemd journal.

    Path: /api/diagnostics/logs/<service>?n=50

    Allowed services: qlcplus-web, lighting-control, lighting-mcp, nginx.
    Unknown service names are rejected (allowlist enforced — no shell
    injection surface).
    """
    if service not in LOG_ALLOWED_SERVICES:
        return jsonify({
            "success": False,
            "error": f"Unknown service '{service}'. Allowed: {sorted(LOG_ALLOWED_SERVICES.keys())}",
        }), 400

    try:
        n = int(request.args.get("n", 50))
    except (TypeError, ValueError):
        n = 50
    n = max(1, min(500, n))

    unit = LOG_ALLOWED_SERVICES[service]

    if not IS_LOCAL:
        # When running remotely (e.g. developer workstation pointed at a
        # remote rig) we can't read journals directly — bail out clearly.
        return jsonify({
            "success": False,
            "error": "get_logs is only available when running on the Pi itself",
            "is_local": False,
        }), 503

    result = execute_command(f"journalctl -u {unit} -n {n} --no-pager --output=short-iso")
    raw = result.get("output", "")
    lines = [line for line in raw.splitlines() if line.strip()]

    return jsonify({
        "success": result["success"],
        "service": service,
        "unit": unit,
        "lines_returned": len(lines),
        "lines_requested": n,
        "lines": lines,
        "error": result.get("error", "") if not result["success"] else "",
    })


# ----------------------------------------------------------------------------
# get_system_info — Pi-level health & inventory
# ----------------------------------------------------------------------------

def _read_first_line(path: str) -> str:
    try:
        return Path(path).read_text().strip().splitlines()[0]
    except Exception:
        return ""


def _read_proc_meminfo() -> dict:
    """Parse /proc/meminfo into kB integers. Returns {} on non-Linux."""
    info = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if ":" not in line:
                continue
            key, _, rest = line.partition(":")
            parts = rest.strip().split()
            if parts and parts[0].isdigit():
                info[key.strip()] = int(parts[0])  # kB
    except Exception:
        return {}
    return info


def _filter_dmx_usb_lines(lines: list[str]) -> list[str]:
    """Filter `lsusb` lines down to DMX-interface candidates.

    Matching by name alone is not enough: the ENTTEC DMX USB Pro's FT232
    enumerates in `lsusb` as "Future Technology Devices International, Ltd
    FT232 Serial (UART) IC" — no "FTDI", "ENTTEC", or "DMX" substring —
    so the vendor id 0403 (FTDI) must be matched too.
    """
    keys = ("ftdi", "enttec", "dmx", "0403:")
    return [ln for ln in lines if any(k in ln.lower() for k in keys)]


# Bit positions documented for `vcgencmd get_throttled` (Raspberry Pi firmware).
_THROTTLED_BITS = (
    (0, "undervoltage_now"),
    (1, "freq_capped_now"),
    (2, "throttled_now"),
    (3, "soft_temp_limit_now"),
    (16, "undervoltage_since_boot"),
    (17, "freq_capped_since_boot"),
    (18, "throttled_since_boot"),
    (19, "soft_temp_limit_since_boot"),
)


def _decode_throttled(raw: str) -> dict | None:
    """Decode `vcgencmd get_throttled` output (e.g. "throttled=0x50005").

    Returns None when the input doesn't parse. `ok` is True only when no
    flag has ever been set since boot — the since-boot bits are what make
    an intermittent brownout remotely observable after the fact.
    """
    text = (raw or "").strip()
    if "=" in text:
        text = text.split("=", 1)[1].strip()
    try:
        value = int(text, 16)
    except (TypeError, ValueError):
        return None
    issues = [name for bit, name in _THROTTLED_BITS if value & (1 << bit)]
    return {
        "raw": f"0x{value:x}",
        "ok": value == 0,
        "issues": issues,
    }


def _analyze_boot_history(prev_boot_tail: str, current_kernel_log: str) -> dict:
    """Classify whether the previous boot ended cleanly.

    Two independent signals, both usable without any state file:
      - the tail of the previous boot's journal (`journalctl -b -1 -n 40`):
        a clean shutdown always leaves a shutdown trail (systemd-shutdown,
        "Journal stopped", ...); an abrupt end (power loss, watchdog reset,
        kernel hang) leaves none.
      - the current boot's kernel log: ext4 logs "orphan cleanup" /
        "recovering journal" when the filesystem wasn't cleanly unmounted.

    Returns {"previous_boot_unclean": bool|None, "evidence": [...]} —
    None when there is nothing to judge from (no previous boot journal
    and no kernel evidence, e.g. journals not persistent).
    """
    clean_markers = (
        "journal stopped",
        "systemd-shutdown",
        "reached target final",
        "powering off",
        "rebooting",
        "shutting down",
    )
    fs_markers = ("orphan cleanup", "recovering journal", "not properly unmounted")

    prev = (prev_boot_tail or "").lower()
    kern = (current_kernel_log or "").lower()

    evidence = [m for m in fs_markers if m in kern]
    if prev and not any(m in prev for m in clean_markers):
        evidence.append("previous boot journal ends without a shutdown sequence")

    if not prev and not evidence:
        return {"previous_boot_unclean": None, "evidence": []}
    return {"previous_boot_unclean": bool(evidence), "evidence": evidence}


# Computed once per process — the answer cannot change within a boot, and
# dmx-monitor polls /api/diagnostics/system every 15 s.
_BOOT_HISTORY_CACHE: dict | None = None


def _boot_history() -> dict:
    global _BOOT_HISTORY_CACHE
    if _BOOT_HISTORY_CACHE is None:
        prev = execute_command("journalctl -b -1 -n 40 --no-pager -o cat 2>/dev/null")
        kern = execute_command(
            "journalctl -k -b 0 --no-pager -o cat 2>/dev/null"
            " | grep -iE 'orphan cleanup|recovering journal|not properly unmounted'"
        )
        _BOOT_HISTORY_CACHE = _analyze_boot_history(
            prev.get("output", ""), kern.get("output", "")
        )
    return _BOOT_HISTORY_CACHE


@app.route("/api/diagnostics/system", methods=["GET"])
def diagnostics_system():
    """Return Pi-level health info: CPU temp, load, memory, disk, uptime,
    USB devices (ENTTEC filter), and service status for the three services
    we manage.

    Most fields are best-effort — fields that aren't available on this
    platform (e.g. /sys/class/thermal on macOS dev machines) are reported
    as null rather than failing the whole call.
    """
    import shutil as _shutil

    out: dict = {
        "is_local": IS_LOCAL,
        "platform": sys.platform,
    }

    # CPU temperature — Linux only
    try:
        millic = _read_first_line("/sys/class/thermal/thermal_zone0/temp")
        out["cpu_temp_c"] = round(int(millic) / 1000.0, 1) if millic.isdigit() else None
    except Exception:
        out["cpu_temp_c"] = None

    # Load average — POSIX
    try:
        load1, load5, load15 = os.getloadavg()
        out["load_avg"] = {"1m": round(load1, 2), "5m": round(load5, 2), "15m": round(load15, 2)}
    except (OSError, AttributeError):
        out["load_avg"] = None

    # Memory — /proc/meminfo (Linux only)
    mem = _read_proc_meminfo()
    if mem:
        total_kb = mem.get("MemTotal", 0)
        avail_kb = mem.get("MemAvailable", mem.get("MemFree", 0))
        used_kb = max(0, total_kb - avail_kb)
        out["memory"] = {
            "total_mb": round(total_kb / 1024, 1),
            "used_mb": round(used_kb / 1024, 1),
            "available_mb": round(avail_kb / 1024, 1),
            "used_pct": round((used_kb / total_kb) * 100, 1) if total_kb else None,
        }
    else:
        out["memory"] = None

    # Disk usage — / and /home if present
    disk = {}
    for mount in ("/", "/home"):
        try:
            usage = _shutil.disk_usage(mount)
            disk[mount] = {
                "total_gb": round(usage.total / (1024 ** 3), 1),
                "used_gb": round(usage.used / (1024 ** 3), 1),
                "free_gb": round(usage.free / (1024 ** 3), 1),
                "used_pct": round((usage.used / usage.total) * 100, 1),
            }
        except FileNotFoundError:
            continue
        except Exception:
            disk[mount] = None
    out["disk"] = disk

    # Uptime — /proc/uptime (Linux) seconds since boot
    try:
        uptime_line = _read_first_line("/proc/uptime")
        if uptime_line:
            seconds = float(uptime_line.split()[0])
            out["uptime_seconds"] = int(seconds)
            # Human-readable summary
            d, rem = divmod(int(seconds), 86400)
            h, rem = divmod(rem, 3600)
            m, _ = divmod(rem, 60)
            parts = []
            if d:
                parts.append(f"{d}d")
            if h:
                parts.append(f"{h}h")
            if m or not parts:
                parts.append(f"{m}m")
            out["uptime_human"] = " ".join(parts)
        else:
            out["uptime_seconds"] = None
            out["uptime_human"] = None
    except Exception:
        out["uptime_seconds"] = None
        out["uptime_human"] = None

    # USB devices — filter to FTDI / ENTTEC related lines
    if IS_LOCAL:
        usb = execute_command("lsusb")
        if usb["success"]:
            all_lines = [ln for ln in usb["output"].splitlines() if ln.strip()]
            out["usb"] = {
                "all_count": len(all_lines),
                "dmx_related": _filter_dmx_usb_lines(all_lines),
            }
        else:
            out["usb"] = None
    else:
        out["usb"] = None

    # Power health — undervoltage/throttle flags from the firmware. The
    # since-boot bits latch, so an intermittent brownout stays visible here
    # long after it happened.
    if IS_LOCAL:
        vc = execute_command("vcgencmd get_throttled")
        out["power"] = _decode_throttled(vc["output"]) if vc["success"] else None
    else:
        out["power"] = None

    # Previous-boot forensics — did the last boot end without a shutdown
    # sequence (power loss / watchdog reset / hang)?
    out["boot"] = _boot_history() if IS_LOCAL else None

    # Service status for the three units (only when local).
    # Distinguishes "not_installed" (unit file missing) from "inactive"
    # (loaded but stopped) — see _systemd_unit_state. Lets the UI render
    # an "install MCP" affordance instead of just a red dot.
    services_status = {}
    if IS_LOCAL:
        for label, unit in LOG_ALLOWED_SERVICES.items():
            if label == "nginx":
                continue  # nginx is optional and reporting failure noisy
            services_status[label] = _systemd_unit_state(unit)
    out["services"] = services_status or None

    return jsonify({"success": True, **out})


# ----------------------------------------------------------------------------
# rf_scan — 2.4 GHz WiFi survey for wireless-DMX interference
# ----------------------------------------------------------------------------
#
# Wireless DMX transmitters (D-Fi Hub and similar) share the 2.4 GHz ISM
# band with WiFi. We can survey what the Pi's own WiFi radio hears there,
# but the transmitter itself is broadcast-only — there's no software
# readback of its channel or of what the receiver actually sees. QLC+ has
# no visibility into this either: it only knows DMX universe/channel
# addressing (which fixture gets which DMX slot), a completely separate
# layer from the transmitter's own RF channel, which lives entirely in the
# transmitter's own firmware/display. So the operator has to tell us what
# their transmitter is set to (from its own display) if they want a
# concrete overlap check — see _load_rf_settings / rf_settings routes.

_WIFI_NONOVERLAPPING_CHANNELS = (1, 6, 11)

# Chauvet's D-Fi Hub / Hub 2 manuals document 16 selectable channels
# (CH01-CH16) and an operating range of 2.412-2.484 GHz, but don't publish
# which frequency each channel number maps to. This assumes even spacing
# across that documented range — an ESTIMATE, not a verified table.
_DFI_CHANNEL_COUNT = 16
_DFI_FREQ_RANGE_MHZ = (2412.0, 2484.0)


def _dfi_channel_to_freq_mhz(channel):
    """Estimate a D-Fi-style transmitter's RF frequency for channel 1-16.
    See the module note above — this is a linear-spacing estimate, not a
    Chauvet-published mapping."""
    if channel is None or not (1 <= channel <= _DFI_CHANNEL_COUNT):
        return None
    lo, hi = _DFI_FREQ_RANGE_MHZ
    return lo + (channel - 1) * (hi - lo) / (_DFI_CHANNEL_COUNT - 1)


def _loudest_signal_near_freq(access_points, freq_mhz, half_width_mhz=20):
    """Loudest signal (dBm) among access points within ±half_width_mhz of
    freq_mhz, or None if nothing's nearby. Frequency-domain counterpart to
    the channel-index bleed model in _analyze_rf_channels."""
    if freq_mhz is None:
        return None
    candidates = [
        ap["signal_dbm"] for ap in access_points
        if ap.get("freq_mhz") is not None and ap.get("signal_dbm") is not None
        and abs(ap["freq_mhz"] - freq_mhz) <= half_width_mhz
    ]
    return max(candidates) if candidates else None


def _load_rf_settings() -> dict:
    """Return saved wireless-DMX-transmitter settings ({} if never set).
    This is operator-entered (from the transmitter's own display) — there's
    no software readback of the transmitter's actual channel."""
    if not RF_SETTINGS_FILE.exists():
        return {}
    try:
        data = json.loads(RF_SETTINGS_FILE.read_text())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _save_rf_settings(settings: dict) -> None:
    RF_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    RF_SETTINGS_FILE.write_text(json.dumps(settings, indent=2))


def _wifi_channel_from_freq(freq_mhz):
    """2.4 GHz center frequency (MHz) -> WiFi channel number, or None
    outside the 2.4 GHz band (e.g. 5 GHz results `iw scan` also returns)."""
    if freq_mhz is None:
        return None
    if 2412 <= freq_mhz <= 2472:
        return round((freq_mhz - 2407) / 5)
    if freq_mhz == 2484:
        return 14
    return None


def _parse_iw_scan_output(raw: str) -> list[dict]:
    """Parse `iw dev <iface> scan` text into 2.4 GHz access-point records:
    {ssid, signal_dbm, freq_mhz, channel}, loudest first.

    5 GHz results are dropped — they share no spectrum with wireless DMX.
    Mirrors the awk parser `lightsctl.sh`'s `rf-scan` command already uses.
    """
    access_points = []
    current = None

    def flush():
        if not current:
            return
        ch = _wifi_channel_from_freq(current.get("freq_mhz"))
        if ch is not None:
            access_points.append({
                "ssid": current.get("ssid") or None,
                "signal_dbm": current.get("signal_dbm"),
                "freq_mhz": current["freq_mhz"],
                "channel": ch,
            })

    for line in raw.splitlines():
        if line.startswith("BSS "):
            flush()
            current = {}
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped.startswith("freq:"):
            try:
                current["freq_mhz"] = float(stripped.split()[1])
            except (IndexError, ValueError):
                pass
        elif stripped.startswith("signal:"):
            try:
                current["signal_dbm"] = float(stripped.split()[1])
            except (IndexError, ValueError):
                pass
        elif stripped.startswith("SSID:"):
            current["ssid"] = stripped.split("SSID:", 1)[1].strip()
    flush()

    access_points.sort(key=lambda ap: ap["signal_dbm"] if ap["signal_dbm"] is not None else -999, reverse=True)
    return access_points


def _analyze_rf_channels(access_points: list[dict], transmitter: dict = None) -> dict:
    """Summarize 2.4 GHz occupancy: per-channel congestion (accounting for
    the ~4-channel bleed of adjacent 20 MHz-wide WiFi channels), the
    quietest 3-channel window, and plain-language suggestions.

    `transmitter`, if given, is the operator-entered wireless-DMX-transmitter
    settings from _load_rf_settings(): {"mode": "auto"|"manual"|"unknown",
    "channel": 1-16 or None}. When mode is "manual" with a channel set, this
    adds a concrete overlap check against that channel's estimated
    frequency; "auto" adds a note that channel-avoidance matters less.
    """
    heard = [ap for ap in access_points if ap.get("channel") and 1 <= ap["channel"] <= 11
             and ap.get("signal_dbm") is not None]

    congestion = {}
    for ch in range(1, 12):
        loudest = None
        for ap in heard:
            if abs(ap["channel"] - ch) <= 4:
                if loudest is None or ap["signal_dbm"] > loudest:
                    loudest = ap["signal_dbm"]
        congestion[ch] = loudest

    def window_loudness(start):
        vals = [congestion[c] for c in range(start, start + 3) if congestion.get(c) is not None]
        return max(vals) if vals else -100.0
    quiet_start = min(range(1, 10), key=window_loudness)
    quiet_window = [quiet_start, quiet_start + 2]

    suggestions = []
    if not heard:
        suggestions.append(
            "No 2.4 GHz WiFi networks detected — the band looks clear right now. "
            "Flicker during this window is unlikely to be WiFi-related."
        )
    else:
        loudest_ch, loudest_dbm = max(congestion.items(), key=lambda kv: kv[1] if kv[1] is not None else -100)
        if loudest_dbm is not None and loudest_dbm >= -55:
            suggestions.append(
                f"Channel {loudest_ch} is loud ({loudest_dbm:.0f} dBm). Keep your wireless DMX "
                "transmitter's channel away from it and the 3-4 channels either side."
            )
        suggestions.append(
            f"Quietest window right now: channels {quiet_window[0]}–{quiet_window[1]}. "
            "Check your transmitter's channel/DIP-switch table for the setting closest to that range "
            "— we can't read the transmitter's own channel back in software."
        )
        crowded_offgrid = [ap for ap in heard
                           if ap["channel"] not in _WIFI_NONOVERLAPPING_CHANNELS and ap["signal_dbm"] >= -65]
        if crowded_offgrid:
            names = ", ".join(sorted({ap["ssid"] or "(hidden network)" for ap in crowded_offgrid}))
            suggestions.append(
                f"{names} — loud and not on the standard non-overlapping 1/6/11 channel set. "
                "If it's a network you control, moving it to channel 1, 6, or 11 frees up more of the band."
            )

    # Concrete cross-reference against what the operator told us their
    # transmitter is set to (see _load_rf_settings — no software readback).
    transmitter = transmitter or {}
    t_mode = transmitter.get("mode")
    t_channel = transmitter.get("channel")
    transmitter_note = None
    if t_mode == "auto":
        transmitter_note = (
            "Your transmitter is set to Auto — it already re-scans and picks its own clear "
            "channel, so this WiFi survey matters less for channel choice. If flicker persists "
            "in Auto mode, channel congestion is a less likely cause."
        )
    elif t_mode == "manual" and t_channel:
        est_freq = _dfi_channel_to_freq_mhz(t_channel)
        nearby_dbm = _loudest_signal_near_freq(access_points, est_freq) if est_freq else None
        if est_freq is not None:
            if nearby_dbm is None:
                transmitter_note = (
                    f"Transmitter channel {t_channel} (~{est_freq:.0f} MHz, estimated — Chauvet "
                    "doesn't publish an exact channel table) looks clear right now."
                )
            else:
                band = "loud" if nearby_dbm >= -55 else "moderate" if nearby_dbm >= -70 else "quiet"
                est_wifi_ch = _wifi_channel_from_freq(est_freq)
                already_in_quiet_window = est_wifi_ch is not None and quiet_window[0] <= est_wifi_ch <= quiet_window[1]
                if band == "quiet":
                    verdict = "Looks fine."
                elif already_in_quiet_window:
                    verdict = "That's already about as clear as this WiFi environment gets right now."
                else:
                    verdict = "Consider moving it toward the quiet window below."
                transmitter_note = (
                    f"Transmitter channel {t_channel} (~{est_freq:.0f} MHz, estimated) is sitting near "
                    f"{band} WiFi traffic ({nearby_dbm:.0f} dBm). {verdict}"
                )
    if transmitter_note:
        suggestions.insert(0, transmitter_note)

    return {
        "per_channel_congestion_dbm": congestion,
        "quiet_window": quiet_window,
        "nonoverlapping_channels": list(_WIFI_NONOVERLAPPING_CHANNELS),
        "suggestions": suggestions,
        "transmitter": transmitter or None,
    }


@app.route("/api/diagnostics/rf_scan", methods=["POST"])
def diagnostics_rf_scan():
    """Survey the 2.4 GHz band from the Pi's own WiFi radio and analyze it
    for wireless-DMX interference risk.

    Returns detected access points plus a channel-congestion analysis and
    plain-language suggestions. Local-only — needs `iw` + the wlan0 radio.
    """
    if not IS_LOCAL:
        return jsonify({
            "success": False,
            "error": "rf_scan is only available when running on the Pi itself",
            "is_local": False,
        }), 503

    result = execute_command("sudo -n iw dev wlan0 scan 2>/dev/null")
    if not result["success"]:
        return jsonify({
            "success": False,
            "error": result.get("error") or "iw scan failed — is wlan0 up?",
        }), 500

    access_points = _parse_iw_scan_output(result["output"])
    analysis = _analyze_rf_channels(access_points, transmitter=_load_rf_settings())

    return jsonify({
        "success": True,
        "interface": "wlan0",
        "access_points": access_points,
        "analysis": analysis,
    })


@app.route("/api/diagnostics/rf_settings", methods=["GET"])
def get_rf_settings():
    """Return the operator-entered wireless-DMX-transmitter settings, if any
    have been saved. There's no software readback of the transmitter's
    actual channel — this is only ever what the operator told us."""
    return jsonify({"success": True, "transmitter": _load_rf_settings() or None})


@app.route("/api/diagnostics/rf_settings", methods=["POST"])
def set_rf_settings():
    """Save what the operator says their wireless DMX transmitter is set to
    (read off the transmitter's own display), so rf_scan can cross-check a
    live WiFi survey against it.

    Body: { "mode": "auto"|"manual"|"unknown", "channel": 1-16 or null }
    "channel" is only meaningful (and required) when mode is "manual".
    """
    body = request.get_json(silent=True) or {}
    mode = body.get("mode")
    if mode not in ("auto", "manual", "unknown"):
        return jsonify({"success": False, "error": "mode must be 'auto', 'manual', or 'unknown'"}), 400

    channel = body.get("channel")
    if mode == "manual":
        try:
            channel = int(channel)
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "channel must be an integer 1-16 when mode is 'manual'"}), 400
        if not (1 <= channel <= 16):
            return jsonify({"success": False, "error": "channel must be between 1 and 16"}), 400
    else:
        channel = None

    settings = {"mode": mode, "channel": channel}
    _save_rf_settings(settings)
    return jsonify({"success": True, "transmitter": settings})


# =============================================================================
# Chases / sequences (issue #4)
# =============================================================================
#
# A chase in QLC+ is a <Function Type="Chaser"> in the workspace's Engine
# element. Each chase has function-level Speed (default FadeIn/Hold/FadeOut)
# plus an ordered list of <Step> elements that reference scenes by ID and
# can override per-step timing.
#
# Playback is driven over the QLC+API WebSocket via
#     QLC+API|setFunctionStatus|<id>|1   (start)
#     QLC+API|setFunctionStatus|<id>|0   (stop)
#
# Chase XML format we generate (QLC+ 4.14.x):
#     <Function ID="N" Type="Chaser" Name="..." Path="...">
#       <Speed FadeIn="500" FadeOut="500" Duration="2000"/>
#       <Direction>Forward</Direction>
#       <RunOrder>Loop</RunOrder>
#       <SpeedModes FadeIn="Default" FadeOut="Default" Duration="Default"/>
#       <Step Number="0" FadeIn="500" Hold="2000" FadeOut="500" Values="42"/>
#       <Step Number="1" FadeIn="500" Hold="2000" FadeOut="500" Values="43"/>
#       ...
#     </Function>


# Enum normalization tables — accept lowercase, store canonical
_CHASE_DIRECTIONS = {"forward": "Forward", "backward": "Backward"}
_CHASE_RUN_ORDERS = {
    "loop": "Loop",
    "singleshot": "SingleShot",
    "single-shot": "SingleShot",
    "single_shot": "SingleShot",
    "pingpong": "PingPong",
    "ping-pong": "PingPong",
    "ping_pong": "PingPong",
    "random": "Random",
}


def _normalize_direction(value: str | None, default: str = "Forward") -> str:
    if not value:
        return default
    return _CHASE_DIRECTIONS.get(str(value).strip().lower(), default)


def _normalize_run_order(value: str | None, default: str = "Loop") -> str:
    if not value:
        return default
    return _CHASE_RUN_ORDERS.get(str(value).strip().lower(), default)


_CHASE_TEMPO_SOURCES = {"fixed": "fixed", "tap": "tap", "audio": "audio"}


def _normalize_tempo_source(value, default: str = "fixed") -> str:
    if not value or isinstance(value, bool):
        return default
    return _CHASE_TEMPO_SOURCES.get(str(value).strip().lower(), default)


def _bpm_to_step_ms(bpm: float) -> int:
    """Convert BPM to step hold duration in milliseconds. 120 BPM → 500ms."""
    return round(60000 / bpm)


def _tap_intervals_to_bpm(intervals_ms: list) -> float | None:
    """Average the last 4 tap intervals and return BPM, or None if outside 40–240 range."""
    if not intervals_ms:
        return None
    recent = intervals_ms[-4:]
    avg_ms = sum(recent) / len(recent)
    if avg_ms <= 0:
        return None
    bpm = 60000 / avg_ms
    if bpm < 40 or bpm > 240:
        return None
    return float(bpm)


def _chase_step_scene_ids(chase_element) -> list:
    """Return the ordered list of scene function IDs from a chase's <Step> elements."""
    steps = []
    for step in _find_children(chase_element, "Step"):
        num_str = step.get("Number", "0")
        num = int(num_str) if num_str.isdigit() else 0
        raw = step.get("Values") or (step.text or "").strip()
        if raw and str(raw).isdigit():
            steps.append((num, int(raw)))
    steps.sort(key=lambda x: x[0])
    return [sid for _, sid in steps]


def _update_tap_runner_bpm(chase_id: str, step_ms: float) -> bool:
    """Update the step interval of a live server-side tap runner.

    Returns True if a running tap runner was found and updated.
    Pure state mutation — no I/O; the running asyncio loop reads the updated value.
    """
    state = _tap_runners.get(str(chase_id))
    if state is None:
        return False
    state["step_ms"] = float(step_ms)
    return True


def _scene_channel_commands(scene_id) -> list:
    """Resolve a scene to CH|abs|val commands, mirroring _mock_chase_run's step logic."""
    scene_elem = _find_scene_element(scene_id)
    if scene_elem is None:
        return []
    cvs = scene_to_channel_values(scene_elem)
    return [f"CH|{ch}|{max(0, min(255, val))}" for ch, val in cvs if int(ch) > 0]


def _tap_runner_blackout_commands(scene_ids: list) -> list:
    """CH|<abs>|0 for every channel touched across scene_ids, deduped in first-seen order.

    Used to clear a tap runner's footprint on stop — a surgical blackout that only
    zeroes channels the runner actually wrote, not the whole rig.
    """
    channels = []
    seen = set()
    for scene_id in scene_ids:
        for cmd in _scene_channel_commands(scene_id):
            parts = cmd.split("|")
            if len(parts) != 3:
                continue
            ch = parts[1]
            if ch not in seen:
                seen.add(ch)
                channels.append(ch)
    return [f"CH|{ch}|0" for ch in channels]


def _start_tap_runner(chase_id: str, scene_ids: list, initial_step_ms: float) -> bool:
    """Start a server-side asyncio loop that steps a tap-source chase through scenes.

    Each iteration resolves the next step's scene to channel values and emits
    CH|abs|val frames (same replace-per-step behaviour as _mock_chase_run), then
    sleeps for state['step_ms'] ms so that BPM changes take effect on the very
    next step.

    Returns False without touching any existing runner if there are no playable
    steps, so a failed start never silently kills a runner already in progress.
    """
    if not scene_ids:
        return False
    _stop_tap_runner(chase_id, teardown=False)  # cancel any existing runner for this chase
    _start_qlc_loop()  # ensure background event loop is running
    state: dict = {"step_ms": float(initial_step_ms), "running": True, "scene_ids": list(scene_ids)}
    _tap_runners[str(chase_id)] = state
    n = len(scene_ids)

    async def _loop() -> None:
        idx = 0
        while state["running"]:
            scene_id = scene_ids[idx % n]
            try:
                commands = _scene_channel_commands(scene_id)
                if commands:
                    await _qlc_send_commands(commands)
            except Exception:
                pass
            await asyncio.sleep(max(0.01, state["step_ms"] / 1000.0))
            idx = (idx + 1) % n

    asyncio.run_coroutine_threadsafe(_loop(), _qlc_loop)
    return True


def _stop_tap_runner(chase_id: str, teardown: bool = True) -> bool:
    """Cancel a running server-side tap runner. Returns True if one was active.

    teardown=True (stop/user-facing) blackouts the runner's channel footprint so
    the rig doesn't stay lit at the last step's values. teardown=False (internal
    restart path in _start_tap_runner) skips the blackout so restarting a tap
    chase doesn't clobber the freshly-started runner's first frame.
    """
    state = _tap_runners.pop(str(chase_id), None)
    if not state:
        return False
    state["running"] = False
    if teardown:
        try:
            commands = _tap_runner_blackout_commands(state.get("scene_ids", []))
            if commands:
                _qlc_run(_qlc_send_commands(commands), timeout=5)
        except Exception:
            pass
    return True


def _engine_functions(engine):
    """All <Function> children of Engine, namespace-tolerant."""
    if engine is None:
        return []
    ns = "http://www.qlcplus.org/Workspace"
    return list(engine.findall(f"{{{ns}}}Function")) + list(engine.findall("Function"))


def get_next_function_id() -> int:
    """Return the next unused Function ID across the entire workspace.

    Functions of every type (Scene, Chaser, EFX, Sequence, …) share a single
    ID space in QLC+, so this scans ALL functions — not just scenes.
    Generalization of the existing get_next_scene_id which only saw scenes.
    """
    if not WORKSPACE_PATH.exists():
        return 0
    root = _workspace_root()
    engine = _engine_element(root)
    if engine is None:
        return 0
    max_id = -1
    for func in _engine_functions(engine):
        fid = func.get("ID", "")
        if fid.isdigit():
            n = int(fid)
            if n > max_id:
                max_id = n
    return max_id + 1


def _find_function_element(id_or_name, function_type: str | None = None):
    """Find a Function by ID or by case-insensitive Name.

    If function_type is given (e.g. "Chaser"), only matches functions of
    that type — so "Red" won't accidentally resolve to a chase if a scene
    of the same name exists.
    """
    if not WORKSPACE_PATH.exists():
        return None
    root = _workspace_root()
    engine = _engine_element(root)
    if engine is None:
        return None
    needle = str(id_or_name).strip().lower()
    for func in _engine_functions(engine):
        if function_type and func.get("Type") != function_type:
            continue
        if func.get("ID") == str(id_or_name) or (func.get("Name") or "").lower() == needle:
            return func
    return None


def get_workspace_chases() -> list[dict]:
    """Return chase summaries from the loaded workspace."""
    if not WORKSPACE_PATH.exists():
        return []
    root = _workspace_root()
    engine = _engine_element(root)
    if engine is None:
        return []
    out = []
    for func in _engine_functions(engine):
        if func.get("Type") != "Chaser":
            continue
        fid = func.get("ID", "")
        speed = next(iter(_find_children(func, "Speed")), None)
        direction = next(iter(_find_children(func, "Direction")), None)
        run_order = next(iter(_find_children(func, "RunOrder")), None)
        steps = _find_children(func, "Step")
        out.append({
            "id": int(fid) if fid.isdigit() else fid,
            "name": func.get("Name", ""),
            "path": func.get("Path", ""),
            "steps": len(steps),
            "direction": direction.text if direction is not None else "Forward",
            "run_order": run_order.text if run_order is not None else "Loop",
            "tempo_source": func.get("TempoSource", "fixed"),
            "speed": {
                "fade_in_ms":  int(speed.get("FadeIn", "0"))   if speed is not None else 0,
                "fade_out_ms": int(speed.get("FadeOut", "0"))  if speed is not None else 0,
                "hold_ms":     int(speed.get("Duration", "0")) if speed is not None else 0,
            },
        })
    return out


def _describe_chase_full(chase_element) -> dict:
    """Return the full chase shape (used by /api/chases/<id> GET)."""
    fid = chase_element.get("ID", "")
    speed = next(iter(_find_children(chase_element, "Speed")), None)
    direction = next(iter(_find_children(chase_element, "Direction")), None)
    run_order = next(iter(_find_children(chase_element, "RunOrder")), None)

    # Resolve scene ID → scene name for friendlier output
    scenes_by_id = {str(s["id"]): s for s in get_workspace_scenes()}

    steps = []
    for step in _find_children(chase_element, "Step"):
        # QLC+ writes the scene reference as the Values attribute in 4.12+
        scene_id = step.get("Values") or (step.text or "").strip()
        scene_info = scenes_by_id.get(str(scene_id))
        steps.append({
            "number": int(step.get("Number", "0")) if step.get("Number", "").isdigit() else 0,
            "scene_id": int(scene_id) if scene_id and str(scene_id).isdigit() else scene_id,
            "scene_name": scene_info["name"] if scene_info else None,
            "fade_in_ms":  int(step.get("FadeIn", "-1"))  if step.get("FadeIn", "-1").lstrip("-").isdigit() else -1,
            "hold_ms":     int(step.get("Hold", "-1"))    if step.get("Hold", "-1").lstrip("-").isdigit() else -1,
            "fade_out_ms": int(step.get("FadeOut", "-1")) if step.get("FadeOut", "-1").lstrip("-").isdigit() else -1,
        })
    steps.sort(key=lambda s: s["number"])

    return {
        "id": int(fid) if fid.isdigit() else fid,
        "name": chase_element.get("Name", ""),
        "path": chase_element.get("Path", ""),
        "direction": direction.text if direction is not None else "Forward",
        "run_order": run_order.text if run_order is not None else "Loop",
        "tempo_source": chase_element.get("TempoSource", "fixed"),
        "speed": {
            "fade_in_ms":  int(speed.get("FadeIn", "0"))   if speed is not None else 0,
            "fade_out_ms": int(speed.get("FadeOut", "0"))  if speed is not None else 0,
            "hold_ms":     int(speed.get("Duration", "0")) if speed is not None else 0,
        },
        "steps": steps,
    }


def _resolve_scene_id(scene_ref) -> int | None:
    """Coerce a step's scene_ref (int ID or string name) to a numeric scene ID."""
    if isinstance(scene_ref, bool):
        return None
    if isinstance(scene_ref, (int, float)):
        return int(scene_ref)
    if isinstance(scene_ref, str):
        text = scene_ref.strip()
        if text.isdigit():
            return int(text)
        # Look up by case-insensitive name
        needle = text.lower()
        for scene in get_workspace_scenes():
            if scene["name"].lower() == needle:
                return scene["id"]
    return None


def _build_chase_xml(
    name: str,
    steps: list,
    fade_in_ms: int,
    hold_ms: int,
    fade_out_ms: int,
    direction: str,
    run_order: str,
    path: str,
    chase_id: int,
    tempo_source: str = "fixed",
) -> str:
    """Generate the <Function Type="Chaser"> XML to inject into the workspace."""
    func_tag = (
        f'<Function ID="{chase_id}" Type="Chaser"'
        f' Name="{_xml_escape(name)}" Path="{_xml_escape(path)}" TempoSource="{tempo_source}">'
    )
    lines = [
        func_tag,
        f'  <Speed FadeIn="{fade_in_ms}" FadeOut="{fade_out_ms}" Duration="{hold_ms}"/>',
        f'  <Direction>{direction}</Direction>',
        f'  <RunOrder>{run_order}</RunOrder>',
        '  <SpeedModes FadeIn="Default" FadeOut="Default" Duration="Default"/>',
    ]
    for i, step in enumerate(steps):
        # step is { scene_id, fade_in_ms?, hold_ms?, fade_out_ms? } (already
        # normalized by the caller)
        sfi = step.get("fade_in_ms", fade_in_ms)
        sh  = step.get("hold_ms",    hold_ms)
        sfo = step.get("fade_out_ms", fade_out_ms)
        lines.append(
            f'  <Step Number="{i}" FadeIn="{sfi}" Hold="{sh}" FadeOut="{sfo}" Values="{step["scene_id"]}"/>'
        )
    lines.append('</Function>')
    return "\n".join(lines)


def _inject_chase_into_workspace(chase_xml: str) -> bool:
    """Inject the chase XML into the workspace's Engine element."""
    try:
        with _WORKSPACE_LOCK:
            tree = ET.parse(WORKSPACE_PATH)
            root = tree.getroot()
            engine = _engine_element(root)
            if engine is None:
                return False
            chase_root = ET.fromstring(chase_xml.strip())
            engine.append(chase_root)
            _atomic_write_tree(tree)
        return True
    except Exception as e:
        log.error("chase_inject_failed", error=str(e))
        return False


async def _set_function_status_async(function_id: int, running: bool) -> str:
    """Tell QLC+ to start or stop a function via the persistent WebSocket."""
    flag = "1" if running else "0"
    return await _qlc_request_reply(
        f"QLC+API|setFunctionStatus|{function_id}|{flag}",
        response_marker="setFunctionStatus",
        timeout=2.0,
    )


def set_function_status(function_id: int, running: bool) -> tuple[bool, str]:
    """Sync wrapper around setFunctionStatus. Returns (ok, raw_response)."""
    if MOCK_DMX:
        if running:
            started = _mock_chase_start(function_id)
            if not started:
                return False, f"mock chase {function_id} not started (missing or empty)"
            return True, "QLC+API|setFunctionStatus|OK"
        _mock_chase_stop(function_id)
        return True, "QLC+API|setFunctionStatus|OK"
    try:
        raw = _qlc_run(_set_function_status_async(function_id, running), timeout=4)
        return True, raw
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# Mock chase stepper — runs in-process when MOCK_DMX=1
# ---------------------------------------------------------------------------
# Chases in production are executed by QLC+ itself; in mock mode we need an
# in-process stepper so the bus reflects what would be on the wire.

_mock_chase_tasks: dict[int, "concurrent.futures.Future"] = {}

# Bumped on every start/stop for a function_id. `_mock_chase_run` checks this
# before every bus write, so a racing/stale task self-terminates even if
# `Future.cancel()` hasn't been delivered yet — see the note in
# `_mock_chase_start` about why cancellation alone isn't sufficient.
_mock_chase_generation: dict[int, int] = {}

# Guards the generation-bump + registry read-modify-write in
# `_mock_chase_start`/`_mock_chase_stop` so two genuinely concurrent
# start/stop calls for the same function_id (Flask runs threaded) can't both
# read the same prior generation and register with an identical value.
# A plain Lock suffices: neither function calls `Future.cancel()` while
# holding it (that's deferred until after release — see the comment in
# `_mock_chase_start`), so there's no reentrant path.
_mock_chase_lock = threading.Lock()

# Floor on each mock chase step's sleep so a zero-fade/zero-hold step (or a
# chase with absent/non-numeric timing that falls back to 0) still yields to
# the shared _qlc_loop every iteration instead of busy-spinning it forever.
_MOCK_CHASE_MIN_STEP_S = 0.02


# Dedicated RNG for `Random` run_order playback, kept separate from the
# `random` module's global state so tests can get deterministic picks via
# `_mock_chase_random.seed(...)` without disturbing (or being disturbed by)
# unrelated code that calls `random.seed()`/`random.random()` elsewhere.
_mock_chase_random = random.Random()


def _chase_index_sequence(n: int, run_order: str):
    """Yield step indices in playback order for the given run_order.

    Loop: 0..n-1 repeating. SingleShot: 0..n-1 once. PingPong: bounces
    forward then back without repeating the endpoints. Random: an endless
    stream of `_mock_chase_random.randrange(n)` picks.
    """
    if n <= 0:
        return
    if run_order == "Random":
        while True:
            yield _mock_chase_random.randrange(n)
    elif run_order == "PingPong":
        while True:
            yield from range(n)
            yield from range(n - 2, 0, -1)
    elif run_order == "SingleShot":
        yield from range(n)
    else:  # Loop (and anything unrecognized)
        while True:
            yield from range(n)


def _mock_chase_start(function_id: int) -> bool:
    """Start an in-process chase stepper for the given function ID.

    Returns True if a stepper was actually spawned.
    """
    chase_elem = None
    try:
        for func in _engine_functions(_engine_element(_workspace_root())):
            if func.get("Type") == "Chaser" and func.get("ID") == str(function_id):
                chase_elem = func
                break
    except Exception:
        pass
    if chase_elem is None:
        _mock_chase_stop(function_id)  # cancel any existing stepper anyway
        return False

    chase_info = _describe_chase_full(chase_elem)
    if not chase_info["steps"]:
        _mock_chase_stop(function_id)  # cancel any existing stepper anyway
        return False

    if _qlc_loop is None:
        _start_qlc_loop()

    # Single atomic section: pop the old registration, bump the generation,
    # and register the new future all under one lock acquisition, so a
    # concurrent start/stop for the same function_id can never observe (or
    # race into) a torn state — e.g. an old task still registered under a
    # newer generation. The old future's cancel() is deliberately deferred
    # until AFTER the lock is released: concurrent.futures.Future.cancel()
    # invokes done-callbacks synchronously (in the calling thread) when the
    # future hasn't started running yet, and our own done-callback below
    # also acquires this lock — cancelling while still holding it would
    # deadlock (this is a plain Lock, not reentrant).
    with _mock_chase_lock:
        old_fut = _mock_chase_tasks.pop(function_id, None)
        gen = _mock_chase_generation.get(function_id, 0) + 1
        _mock_chase_generation[function_id] = gen

        fut = asyncio.run_coroutine_threadsafe(
            _mock_chase_run(function_id, chase_info, gen),
            _qlc_loop,
        )
        _mock_chase_tasks[function_id] = fut

    if old_fut is not None:
        old_fut.cancel()

    def _cleanup(_done, _fid=function_id, _fut=fut):
        # Identity-guarded: only remove OUR registration. Without this, an
        # old (cancelled) task's cleanup can run after a restart has already
        # registered a newer task under the same function_id, popping the
        # new one and leaking the old stepper forever.
        with _mock_chase_lock:
            if _mock_chase_tasks.get(_fid) is _fut:
                _mock_chase_tasks.pop(_fid, None)

    fut.add_done_callback(_cleanup)
    return True


def _mock_chase_stop(function_id: int) -> None:
    """Cancel the in-process chase stepper for the given function ID."""
    # Bump the generation unconditionally (even with no task registered) so
    # a task that hasn't been scheduled onto the loop yet — and therefore
    # has nothing to cancel() — still notices it's stale on its first turn.
    with _mock_chase_lock:
        _mock_chase_generation[function_id] = _mock_chase_generation.get(function_id, 0) + 1
        task = _mock_chase_tasks.pop(function_id, None)
    if task is not None:
        task.cancel()


async def _mock_chase_run(function_id: int, chase_info: dict, gen: int) -> None:
    """Async chase stepper: apply each step's scene to the mock bus on schedule.

    `asyncio.Task.cancel()` delivered via `run_coroutine_threadsafe` is only
    honoured at the next `await` point — but a brand-new task always runs its
    body synchronously up to its *first* await regardless of when cancel()
    was requested (cancellation propagation itself is one loop-tick behind
    task creation). That lets a stale task from a start->restart->stop burst
    slip in a bus write before it notices it's cancelled. Checking the
    generation counter both before and after building the write (scene
    lookup re-parses the workspace XML from disk, which is slow enough for a
    stop() on another thread to land in between) closes that gap.
    """
    steps = chase_info["steps"]
    default_speed = chase_info.get("speed", {})
    run_order = chase_info.get("run_order", "Loop")

    try:
        for i in _chase_index_sequence(len(steps), run_order):
            if _mock_chase_generation.get(function_id) != gen:
                return
            step = steps[i]
            scene_id = step.get("scene_id")
            hold_ms = step.get("hold_ms", -1)
            if hold_ms < 0:
                hold_ms = default_speed.get("hold_ms", 2000)
            fade_in_ms = step.get("fade_in_ms", -1)
            if fade_in_ms < 0:
                fade_in_ms = default_speed.get("fade_in_ms", 500)

            # Apply the scene to the mock bus
            if scene_id is not None:
                try:
                    # Build CH commands and apply directly — calling
                    # set_channel_values() here would deadlock because
                    # _qlc_run uses run_coroutine_threadsafe on the same
                    # loop this coroutine is running on.
                    commands = _scene_channel_commands(scene_id)
                    # Re-check freshness: the scene lookup above re-parses
                    # the workspace XML from disk, which is slow enough
                    # for a stop() on another thread to have landed while
                    # we were building `commands`.
                    if commands and _mock_chase_generation.get(function_id) == gen:
                        _mock_dmx.apply_commands(commands)
                except Exception as e:
                    print(f"[mock-chase {function_id}] step {i} apply error: {e}")

            # Honour timing (fade_in + hold). Always yield at least once per
            # iteration — a zero-timing step must not starve the shared
            # _qlc_loop (see OSS-885 / lights-pi#65). SingleShot termination
            # is handled by _chase_index_sequence itself (it simply stops
            # yielding), so no separate break condition is needed here.
            total_sleep = max((fade_in_ms + hold_ms) / 1000, _MOCK_CHASE_MIN_STEP_S)
            await asyncio.sleep(total_sleep)
    except asyncio.CancelledError:
        pass


# ------------------------------- endpoints ----------------------------------


@app.route("/api/chases", methods=["GET"])
def list_chases():
    """List all chases (Chaser functions) in the loaded workspace."""
    try:
        return jsonify({"chases": get_workspace_chases()})
    except Exception as e:
        return jsonify({"error": str(e), "chases": []}), 500


@app.route("/api/chases/<chase_id>", methods=["GET"])
def describe_chase(chase_id):
    """Return the full chase definition, including resolved per-step scene names."""
    chase = _find_function_element(chase_id, function_type="Chaser")
    if chase is None:
        return jsonify({"success": False, "error": f"Chase not found: {chase_id}"}), 404
    return jsonify({"success": True, "chase": _describe_chase_full(chase)})


@app.route("/api/chases", methods=["POST"])
def create_chase():
    """Create a new chase.

    Body:
        {
          "name": "Sunset",
          "steps": [                # required, ordered
            "Warm",                  # scene name (or numeric ID)
            42,                      # scene ID
            { "scene": "Amber", "hold_ms": 4000 }
          ],
          "fade_in_ms":  500,       # default per step
          "hold_ms":     2000,
          "fade_out_ms": 500,
          "direction":   "Forward",  # Forward | Backward
          "run_order":   "Loop",     # Loop | SingleShot | PingPong | Random
          "path":        "AI Generated"
        }
    """
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "name is required"}), 400

    raw_steps = data.get("steps") or []
    if not isinstance(raw_steps, list) or not raw_steps:
        return jsonify({"success": False, "error": "steps must be a non-empty array"}), 400

    fade_in_ms   = max(0, int(data.get("fade_in_ms",  500)))
    hold_ms      = max(0, int(data.get("hold_ms",     2000)))
    fade_out_ms  = max(0, int(data.get("fade_out_ms", 500)))
    direction    = _normalize_direction(data.get("direction"))
    run_order    = _normalize_run_order(data.get("run_order"))
    tempo_source = _normalize_tempo_source(data.get("tempo_source"))
    path         = (data.get("path") or "AI Generated").strip()

    # Normalize and validate steps. Each step becomes
    # { scene_id, fade_in_ms?, hold_ms?, fade_out_ms? }.
    normalized_steps = []
    unknown_refs = []
    for i, raw in enumerate(raw_steps):
        if isinstance(raw, dict):
            scene_ref = raw.get("scene") or raw.get("scene_id") or raw.get("id")
            step_fade_in  = raw.get("fade_in_ms")
            step_hold     = raw.get("hold_ms")
            step_fade_out = raw.get("fade_out_ms")
        else:
            scene_ref = raw
            step_fade_in = step_hold = step_fade_out = None

        scene_id = _resolve_scene_id(scene_ref)
        if scene_id is None:
            unknown_refs.append({"step": i, "ref": scene_ref})
            continue

        normalized = {"scene_id": scene_id}
        if step_fade_in is not None:
            normalized["fade_in_ms"] = max(0, int(step_fade_in))
        if step_hold is not None:
            normalized["hold_ms"] = max(0, int(step_hold))
        if step_fade_out is not None:
            normalized["fade_out_ms"] = max(0, int(step_fade_out))
        normalized_steps.append(normalized)

    if unknown_refs:
        return jsonify({
            "success": False,
            "error": "One or more steps reference unknown scenes",
            "unknown": unknown_refs,
        }), 400

    with _WORKSPACE_LOCK:
        # Reject duplicate names — chase Name is the agent-friendly key.
        # Checked inside the lock so a racing create_chase can't slip a
        # second chase in under the same name between check and write.
        if _find_function_element(name, function_type="Chaser") is not None:
            return jsonify({"success": False, "error": f"Chase '{name}' already exists"}), 409

        chase_id = get_next_function_id()
        chase_xml = _build_chase_xml(
            name=name, steps=normalized_steps,
            fade_in_ms=fade_in_ms, hold_ms=hold_ms, fade_out_ms=fade_out_ms,
            direction=direction, run_order=run_order, path=path,
            chase_id=chase_id, tempo_source=tempo_source,
        )
        if not _inject_chase_into_workspace(chase_xml):
            return jsonify({"success": False, "error": "Failed to write chase to workspace"}), 500

    return jsonify({
        "success": True,
        "chase": {
            "id": chase_id,
            "name": name,
            "path": path,
            "steps": len(normalized_steps),
            "direction": direction,
            "run_order": run_order,
            "tempo_source": tempo_source,
            "speed": {
                "fade_in_ms":  fade_in_ms,
                "hold_ms":     hold_ms,
                "fade_out_ms": fade_out_ms,
            },
        },
    })


@app.route("/api/chases/<chase_id>", methods=["DELETE"])
def delete_chase(chase_id):
    """Remove a chase from the workspace."""
    try:
        with _WORKSPACE_LOCK:
            tree = ET.parse(WORKSPACE_PATH)
            root = tree.getroot()
            engine = _engine_element(root)
            if engine is None:
                return jsonify({"success": False, "error": "No Engine element in workspace"}), 500

            needle = str(chase_id).strip().lower()
            target = None
            for func in _engine_functions(engine):
                if func.get("Type") != "Chaser":
                    continue
                if func.get("ID") == str(chase_id) or (func.get("Name") or "").lower() == needle:
                    target = func
                    break
            if target is None:
                return jsonify({"success": False, "error": f"Chase not found: {chase_id}"}), 404

            engine.remove(target)
            _atomic_write_tree(tree)
        return jsonify({
            "success": True,
            "deleted": {"id": target.get("ID"), "name": target.get("Name")},
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def _start_chase_by_ref(chase_id):
    """Start chase playback. Returns (result_dict, http_status).

    For tap-source chases the server drives the step loop so that BPM changes
    take effect immediately without touching QLC+'s in-memory timing.
    Fixed/audio chases delegate to QLC+ via setFunctionStatus as before.
    """
    chase = _find_function_element(chase_id, function_type="Chaser")
    if chase is None:
        return {"success": False, "error": f"Chase not found: {chase_id}"}, 404
    fid = chase.get("ID")
    if not (fid and fid.isdigit()):
        return {"success": False, "error": f"Chase has no numeric ID: {chase.get('Name')}"}, 500
    name = chase.get("Name")

    if chase.get("TempoSource", "fixed") == "tap":
        scene_ids = _chase_step_scene_ids(chase)
        speed = next(iter(_find_children(chase, "Speed")), None)
        initial_step_ms = float(speed.get("Duration", "500")) if speed is not None else 500.0
        started = _start_tap_runner(fid, scene_ids, initial_step_ms)
        if not started:
            return {
                "success": False,
                "chase": {"id": int(fid), "name": name},
                "response": "",
                "error": "chase has no playable steps",
            }, 400
        _emit("chase_started", {"chase_id": int(fid), "chase_name": name})
        return {
            "success": True,
            "chase": {"id": int(fid), "name": name},
            "response": "tap runner started",
            "error": "",
        }, 200

    ok, raw = set_function_status(int(fid), running=True)
    if ok:
        _emit("chase_started", {"chase_id": int(fid), "chase_name": name})
    return {
        "success": ok,
        "chase": {"id": int(fid), "name": name},
        "response": raw,
        "error": "" if ok else raw,
    }, 200


def _stop_chase_by_ref(chase_id):
    """Stop chase playback. Returns (result_dict, http_status).

    Cancels the server-side tap runner if active, and also sends a
    setFunctionStatus stop to QLC+ (harmless if the chaser wasn't running there).
    """
    chase = _find_function_element(chase_id, function_type="Chaser")
    if chase is None:
        return {"success": False, "error": f"Chase not found: {chase_id}"}, 404
    fid = chase.get("ID")
    if not (fid and fid.isdigit()):
        return {"success": False, "error": f"Chase has no numeric ID: {chase.get('Name')}"}, 500
    name = chase.get("Name")
    tap_was_running = _stop_tap_runner(fid)
    ok, raw = set_function_status(int(fid), running=False)
    if ok or tap_was_running:
        _emit("chase_stopped", {"chase_id": int(fid), "chase_name": name})
    return {
        "success": ok or tap_was_running,
        "chase": {"id": int(fid), "name": name},
        "response": "tap runner stopped" if tap_was_running else raw,
        "error": "" if (ok or tap_was_running) else raw,
    }, 200


@app.route("/api/chases/<chase_id>/start", methods=["POST"])
def start_chase(chase_id):
    """Start chase playback."""
    result, status = _start_chase_by_ref(chase_id)
    return jsonify(result), status


@app.route("/api/chases/<chase_id>/stop", methods=["POST"])
def stop_chase(chase_id):
    """Stop chase playback."""
    result, status = _stop_chase_by_ref(chase_id)
    return jsonify(result), status


@app.route("/api/chases/<chase_id>/tempo", methods=["POST"])
def set_chase_tempo(chase_id):
    """Rewrite step Hold times for a chase from a tap-tempo BPM value.

    Body: { "bpm": 120 }
    Validates 40–240 BPM. Updates Speed Duration + every Step Hold in the workspace XML.
    """
    try:
        data = request.get_json(silent=True) or {}
        bpm_raw = data.get("bpm")
        if bpm_raw is None:
            return jsonify({"success": False, "error": "bpm is required"}), 400
        try:
            bpm = float(bpm_raw)
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "bpm must be a number"}), 400
        if not math.isfinite(bpm) or bpm < 40 or bpm > 240:
            return jsonify({
                "success": False,
                "error": f"BPM must be between 40 and 240, got {bpm}",
            }), 400

        step_ms = _bpm_to_step_ms(bpm)

        with _WORKSPACE_LOCK:
            tree = ET.parse(WORKSPACE_PATH)
            root = tree.getroot()
            engine = _engine_element(root)
            if engine is None:
                return jsonify({"success": False, "error": "No Engine element in workspace"}), 500

            needle = str(chase_id).strip().lower()
            target = None
            for func in _engine_functions(engine):
                if func.get("Type") != "Chaser":
                    continue
                if func.get("ID") == str(chase_id) or (func.get("Name") or "").lower() == needle:
                    target = func
                    break
            if target is None:
                return jsonify({"success": False, "error": f"Chase not found: {chase_id}"}), 404

            speed = next(iter(_find_children(target, "Speed")), None)
            if speed is not None:
                speed.set("Duration", str(step_ms))

            for step in _find_children(target, "Step"):
                step.set("Hold", str(step_ms))

            _atomic_write_tree(tree)

        # Update the live server-side tap runner if this chase is currently running.
        # The async loop reads state['step_ms'] on every iteration so the next step
        # fires at the new BPM without any restart or QLC+ reload.
        live_updated = _update_tap_runner_bpm(target.get("ID", ""), step_ms)

        return jsonify({
            "success": True,
            "bpm": bpm,
            "step_ms": step_ms,
            "live_updated": live_updated,
            "chase": {"id": target.get("ID"), "name": target.get("Name")},
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# =============================================================================
# Cue lists — audio-synced show programming (issue #8)
# =============================================================================
#
# A *cue list* is an ordered list of cues; each cue has an absolute timestamp
# relative to GO and an action to dispatch. When the operator (or agent)
# presses GO, the server starts an internal clock and fires each cue at its
# at_ms timestamp. This is the QLab / ETC Ion model — the "cue stack" that
# every live show is built on.
#
# Sync-mode only for v1: the user runs their audio in OBS / Logic / whatever
# and presses GO on the cue list at the same moment. The server doesn't play
# audio itself. Future work could add player-mode.
#
# Persistence: ~/.qlcplus/cue_lists.json (separate from QLC+ workspace —
# cue lists are a control-server concept, not a QLC+ function type).
#
# Cue shape (all forms accepted by the API):
#
#   { "at": "0:32",        "scene": "Chorus" }
#   { "at": "1:45.500",    "chase": "Sunset" }
#   { "at_ms": 32000,      "action": "strobe", "parameters": {"rate": 12} }
#   { "at": "0:08",        "scene": "Daylight", "groups": ["key-lights"] }
#
# Internally everything is normalized to:
#   { "at_ms": int, "action": str, "parameters": dict, "groups": list|null }


def _load_cue_lists() -> dict:
    """Return the persisted cue-list registry, tolerant of missing file."""
    if not CUE_LISTS_FILE.exists():
        return {"next_id": 1, "cue_lists": []}
    try:
        data = json.loads(CUE_LISTS_FILE.read_text())
    except json.JSONDecodeError:
        return {"next_id": 1, "cue_lists": []}
    if not isinstance(data, dict):
        return {"next_id": 1, "cue_lists": []}
    data.setdefault("next_id", 1)
    data.setdefault("cue_lists", [])
    return data


def _save_cue_lists(data: dict) -> None:
    CUE_LISTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CUE_LISTS_FILE.write_text(json.dumps(data, indent=2))


def _parse_time_ms(value) -> int | None:
    """Coerce a time input to milliseconds.

    Accepts:
        12345           int / float → ms
        "12345"         numeric string → ms
        "12345ms"       explicit ms
        "32s"           seconds
        "0:32"          MM:SS
        "0:32.5"        MM:SS.fff
        "1:23:45"       HH:MM:SS
        "1:23:45.250"   HH:MM:SS.fff
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0, int(value))

    text = str(value).strip().lower()
    if not text:
        return None

    # Handle suffix forms
    if text.endswith("ms"):
        try:
            return max(0, int(float(text[:-2].strip())))
        except ValueError:
            return None
    if text.endswith("s") and not text.endswith("ms"):
        try:
            return max(0, int(float(text[:-1].strip()) * 1000))
        except ValueError:
            return None

    # Colon-separated forms
    if ":" in text:
        parts = text.split(":")
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return None
        if len(nums) == 2:
            minutes, seconds = nums
            return max(0, int((minutes * 60 + seconds) * 1000))
        if len(nums) == 3:
            hours, minutes, seconds = nums
            return max(0, int((hours * 3600 + minutes * 60 + seconds) * 1000))
        return None

    # Plain numeric → ms
    try:
        return max(0, int(float(text)))
    except ValueError:
        return None


def _format_time_ms(ms: int) -> str:
    """Format ms as M:SS.mmm or H:MM:SS.mmm for display."""
    if ms is None:
        return "—"
    ms = max(0, int(ms))
    millis = ms % 1000
    total_s = ms // 1000
    h = total_s // 3600
    m = (total_s % 3600) // 60
    s = total_s % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}.{millis:03d}"
    return f"{m}:{s:02d}.{millis:03d}"


def _normalize_cue(raw_cue) -> dict:
    """Resolve a raw cue dict into the canonical internal form.

    Returns a dict with one of:
        {"error": "..."}           — couldn't parse
        {"at_ms", "action", "parameters", "groups"}

    Accepted shapes for the action portion:
        {"scene": "Name"}                  → activate_scene
        {"chase": "Name"}                  → start_chase
        {"action": "strobe", "parameters": {...}}
        {"action": "blackout"}
    """
    if not isinstance(raw_cue, dict):
        return {"error": f"Cue must be an object, got {type(raw_cue).__name__}"}

    at_input = raw_cue.get("at") if "at" in raw_cue else raw_cue.get("at_ms")
    if at_input is None:
        return {"error": "Cue missing 'at' or 'at_ms'"}
    at_ms = _parse_time_ms(at_input)
    if at_ms is None:
        return {"error": f"Cue 'at' is not parseable: {at_input!r}"}

    groups = raw_cue.get("groups") or None

    # Resolve the action portion
    if "scene" in raw_cue:
        return {
            "at_ms": at_ms,
            "action": "activate_scene",
            "parameters": {"scene": raw_cue["scene"]},
            "groups": groups,
        }
    if "chase" in raw_cue:
        return {
            "at_ms": at_ms,
            "action": "start_chase",
            "parameters": {"chase": raw_cue["chase"]},
            "groups": groups,
        }
    if "action" in raw_cue:
        return {
            "at_ms": at_ms,
            "action": str(raw_cue["action"]),
            "parameters": raw_cue.get("parameters", {}) or {},
            "groups": groups,
        }
    return {"error": "Cue must have 'scene', 'chase', or 'action'"}


def _validate_cue_action(cue: dict) -> str | None:
    """Cross-check that the cue references existing scenes/chases. Returns
    an error string or None.

    Doesn't validate action names since execute_lighting_action will reject
    unknown ones at fire time — but the agent gets a much better signal if
    we catch broken refs before storage.
    """
    action = cue["action"]
    params = cue["parameters"]
    if action == "activate_scene":
        scene_ref = params.get("scene") or params.get("name") or params.get("id")
        if _find_scene_element(scene_ref) is None:
            return f"Scene not found: {scene_ref!r}"
    elif action in ("start_chase", "stop_chase"):
        chase_ref = params.get("chase") or params.get("name") or params.get("id")
        if _find_function_element(chase_ref, function_type="Chaser") is None:
            return f"Chase not found: {chase_ref!r}"
    return None


def _serialize_cue(cue: dict) -> dict:
    """Cue → API-friendly form with both numeric and human-readable time."""
    return {
        "at_ms": cue["at_ms"],
        "at": _format_time_ms(cue["at_ms"]),
        "action": cue["action"],
        "parameters": cue["parameters"],
        "groups": cue.get("groups"),
    }


def _serialize_cue_list(cl: dict, include_runtime: bool = False) -> dict:
    out = {
        "id": cl["id"],
        "name": cl["name"],
        "description": cl.get("description", ""),
        "duration_ms": cl.get("duration_ms", 0),
        "duration": _format_time_ms(cl.get("duration_ms", 0)),
        "cue_count": len(cl.get("cues", [])),
        "cues": [_serialize_cue(c) for c in cl.get("cues", [])],
        "audio_file": cl.get("audio_file"),
    }
    if include_runtime:
        runtime = _active_cue_lists.get(cl["id"])
        if runtime:
            elapsed_ms = int((time.time() - runtime["started_at"]) * 1000)
            out["runtime"] = {
                "running": True,
                "elapsed_ms": elapsed_ms,
                "elapsed": _format_time_ms(elapsed_ms),
                "cues_fired": len(runtime["cues_fired"]),
                "started_at": runtime["started_at"],
            }
        else:
            out["runtime"] = {"running": False}
    return out


def _find_cue_list(id_or_name) -> tuple[dict, dict] | tuple[None, None]:
    """Return (full_store, found_cue_list) or (None, None) if not found.

    Accepts either an integer ID or a case-insensitive name match.
    """
    data = _load_cue_lists()
    needle = str(id_or_name).strip().lower()
    for cl in data["cue_lists"]:
        if str(cl["id"]) == str(id_or_name) or cl["name"].lower() == needle:
            return data, cl
    return None, None


def _cue_active_at(cues: list[dict], at_ms: int) -> dict | None:
    """Return the last cue whose at_ms <= at_ms, or None if at_ms is
    before the first cue (or there are no cues)."""
    candidates = [c for c in cues if c["at_ms"] <= at_ms]
    if not candidates:
        return None
    return max(candidates, key=lambda c: c["at_ms"])


def _audio_file_path(cl: dict) -> Path | None:
    """Resolve a cue list's associated audio file within CUE_AUDIO_DIR.

    Returns None if there's no audio_file set, it's an absolute path, or it
    resolves outside CUE_AUDIO_DIR (path traversal guard).
    """
    audio_file = cl.get("audio_file")
    if not audio_file:
        return None
    if Path(audio_file).is_absolute():
        return None
    candidate = (CUE_AUDIO_DIR / audio_file).resolve()
    audio_dir = CUE_AUDIO_DIR.resolve()
    if audio_dir not in candidate.parents and candidate != audio_dir:
        return None
    return candidate


def _wav_peaks(path: Path, resolution_ms: int = 50) -> list[dict]:
    """Return per-bucket {"peak", "rms"} amplitude data (normalized 0-1)
    for a PCM WAV file, one bucket per resolution_ms of audio.

    Pure stdlib (wave + array) — deliberately avoids numpy so this helper
    (and CI) never depends on it.
    """
    import array

    with wave.open(str(path), "rb") as wf:
        n_channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        frame_rate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    type_codes = {1: "b", 2: "h", 4: "i"}
    if sample_width not in type_codes or n_channels < 1 or frame_rate <= 0:
        return []

    samples = array.array(type_codes[sample_width])
    samples.frombytes(raw[: len(raw) - (len(raw) % (sample_width * n_channels))])
    max_val = float(2 ** (8 * sample_width - 1))

    frames_per_bucket = max(1, int(frame_rate * resolution_ms / 1000))
    samples_per_bucket = frames_per_bucket * n_channels

    peaks = []
    for start in range(0, len(samples), samples_per_bucket):
        bucket = samples[start:start + samples_per_bucket]
        if not bucket:
            continue
        peak = max(abs(s) for s in bucket) / max_val
        rms = math.sqrt(sum((s / max_val) ** 2 for s in bucket) / len(bucket))
        peaks.append({"peak": round(min(1.0, peak), 4), "rms": round(min(1.0, rms), 4)})
    return peaks


# ----------------------------------------------------------------------------
# Playback engine — one asyncio task per running cue list
# ----------------------------------------------------------------------------

# Registry of currently-playing cue lists.
# Shape: { cue_list_id: { 'task', 'started_at', 'cues_fired', 'cue_list' } }
_active_cue_lists: dict[int, dict] = {}
_active_cue_lists_lock = threading.Lock()


async def _run_cue_list_async(
    cue_list_id: int,
    cues: list[dict],
    *,
    now=time.time,
    sleep=asyncio.sleep,
):
    """Play a cue list — fire each cue at its at_ms relative to GO.

    Designed to tolerate cue dispatch failures: one bad cue prints a warning
    but the remaining cues still fire on schedule.

    ``now`` and ``sleep`` are injectable for testing with a fake clock.
    """
    started_at = now()
    fired_indexes: list[int] = []

    with _active_cue_lists_lock:
        _active_cue_lists[cue_list_id] = {
            "started_at": started_at,
            "cues_fired": fired_indexes,
        }

    # Stable order in case caller passed cues out of timestamp order
    sorted_cues = sorted(enumerate(cues), key=lambda item: item[1]["at_ms"])

    try:
        for idx, cue in sorted_cues:
            elapsed_ms = (now() - started_at) * 1000
            wait_ms = max(0, cue["at_ms"] - elapsed_ms)
            if wait_ms > 0:
                await sleep(wait_ms / 1000)

            action_data = {
                "action": cue["action"],
                "parameters": cue["parameters"],
            }
            try:
                # execute_lighting_action is sync but does its own _qlc_run
                # internally for channel writes; it's safe to call from
                # this async context (it'll just submit further work to the
                # same event loop without re-entering).
                execute_lighting_action(action_data, target_groups=cue.get("groups"), source="cue")
            except Exception as e:
                log.warning("cue_step_failed", cue_list_id=cue_list_id, cue_idx=idx, action=cue["action"], error=str(e))
            fired_indexes.append(idx)
    except asyncio.CancelledError:
        # Normal path for stop_cue_list — just exit cleanly
        raise
    except Exception as e:
        log.error("cue_list_playback_failed", cue_list_id=cue_list_id, error=str(e))
    finally:
        with _active_cue_lists_lock:
            _active_cue_lists.pop(cue_list_id, None)


def _go_cue_list(cue_list: dict) -> bool:
    """Start playback. If already running, cancel the old task and start
    fresh (matches "press GO twice = restart")."""
    cl_id = cue_list["id"]
    with _active_cue_lists_lock:
        existing = _active_cue_lists.get(cl_id)
    if existing and existing.get("task"):
        existing["task"].cancel()

    # Schedule the coroutine on the QLC+ background loop so we share an
    # event loop with the WebSocket writes (no cross-loop weirdness).
    if _qlc_loop is None:
        _start_qlc_loop()

    cues = list(cue_list.get("cues", []))
    coro = _run_cue_list_async(cl_id, cues)
    task = asyncio.run_coroutine_threadsafe(coro, _qlc_loop)

    # Stash the task handle so we can cancel later
    with _active_cue_lists_lock:
        entry = _active_cue_lists.setdefault(cl_id, {})
        entry["task"] = task
    return True


def _stop_cue_list(cl_id: int) -> bool:
    with _active_cue_lists_lock:
        entry = _active_cue_lists.get(cl_id)
    if not entry:
        return False
    task = entry.get("task")
    if task is not None:
        task.cancel()
    return True


# ----------------------------------------------------------------------------
# endpoints
# ----------------------------------------------------------------------------


@app.route("/api/cue_lists", methods=["GET"])
def list_cue_lists():
    """List every saved cue list with runtime status for any currently playing."""
    data = _load_cue_lists()
    return jsonify({
        "cue_lists": [_serialize_cue_list(cl, include_runtime=True) for cl in data["cue_lists"]],
    })


@app.route("/api/cue_lists/active", methods=["GET"])
def list_active_cue_lists():
    """List only cue lists that are currently playing, with elapsed time."""
    data = _load_cue_lists()
    by_id = {cl["id"]: cl for cl in data["cue_lists"]}
    active = []
    with _active_cue_lists_lock:
        snapshot = dict(_active_cue_lists)
    for cl_id, runtime in snapshot.items():
        cl = by_id.get(cl_id)
        if cl is None:
            continue
        elapsed_ms = int((time.time() - runtime["started_at"]) * 1000)
        active.append({
            "id": cl_id,
            "name": cl["name"],
            "elapsed_ms": elapsed_ms,
            "elapsed": _format_time_ms(elapsed_ms),
            "cues_fired": len(runtime["cues_fired"]),
            "cues_total": len(cl["cues"]),
            "duration_ms": cl.get("duration_ms", 0),
        })
    return jsonify({"active": active})


@app.route("/api/cue_lists/<cl_id_or_name>", methods=["GET"])
def describe_cue_list(cl_id_or_name):
    """Return a single cue list's full definition plus runtime status."""
    _, cl = _find_cue_list(cl_id_or_name)
    if cl is None:
        return jsonify({"success": False, "error": f"Cue list not found: {cl_id_or_name}"}), 404
    return jsonify({"success": True, "cue_list": _serialize_cue_list(cl, include_runtime=True)})


@app.route("/api/cue_lists", methods=["POST"])
def create_cue_list():
    """Create a new cue list.

    Body:
        {
          "name": "YouTube Intro",
          "description": "30-second series intro",      # optional
          "cues": [
            { "at": "0:00",     "scene": "Daylight" },
            { "at": "0:08",     "chase": "Sunset" },
            { "at": "0:15.500", "scene": "Warm" },
            { "at": "0:22",     "action": "strobe",   "parameters": {"rate": 8} },
            { "at": "0:24",     "action": "strobe",   "parameters": {"rate": "off"} },
            { "at": "0:28",     "action": "fade",     "parameters": {"target": "0", "duration": "2"} },
            { "at": "0:30",     "action": "blackout" }
          ]
        }
    """
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "name is required"}), 400

    raw_cues = body.get("cues") or []
    if not isinstance(raw_cues, list) or not raw_cues:
        return jsonify({"success": False, "error": "cues must be a non-empty array"}), 400

    # Normalize + validate
    cues = []
    errors = []
    for i, raw in enumerate(raw_cues):
        norm = _normalize_cue(raw)
        if "error" in norm:
            errors.append({"index": i, "error": norm["error"]})
            continue
        ref_err = _validate_cue_action(norm)
        if ref_err:
            errors.append({"index": i, "error": ref_err})
            continue
        cues.append(norm)

    if errors:
        return jsonify({
            "success": False,
            "error": "One or more cues are invalid",
            "cue_errors": errors,
        }), 400

    cues.sort(key=lambda c: c["at_ms"])
    duration_ms = cues[-1]["at_ms"] if cues else 0

    data = _load_cue_lists()
    if any(cl["name"].lower() == name.lower() for cl in data["cue_lists"]):
        return jsonify({"success": False, "error": f"Cue list '{name}' already exists"}), 409

    cl_id = data["next_id"]
    data["next_id"] = cl_id + 1
    new_cl = {
        "id": cl_id,
        "name": name,
        "description": (body.get("description") or "").strip(),
        "duration_ms": duration_ms,
        "cues": cues,
        "audio_file": (body.get("audio_file") or "").strip() or None,
    }
    data["cue_lists"].append(new_cl)
    _save_cue_lists(data)

    return jsonify({"success": True, "cue_list": _serialize_cue_list(new_cl)})


@app.route("/api/cue_lists/<cl_id_or_name>", methods=["PATCH"])
def update_cue_list(cl_id_or_name):
    """Update a cue list — rename, change description, or replace the cues array."""
    body = request.get_json(silent=True) or {}
    data, cl = _find_cue_list(cl_id_or_name)
    if cl is None:
        return jsonify({"success": False, "error": f"Cue list not found: {cl_id_or_name}"}), 404

    new_name = (body.get("name") or "").strip()
    if new_name and new_name.lower() != cl["name"].lower():
        if any(other["name"].lower() == new_name.lower() for other in data["cue_lists"] if other["id"] != cl["id"]):
            return jsonify({"success": False, "error": f"Cue list '{new_name}' already exists"}), 409
        cl["name"] = new_name

    if "description" in body:
        cl["description"] = (body.get("description") or "").strip()

    if "audio_file" in body:
        cl["audio_file"] = (body.get("audio_file") or "").strip() or None

    if "cues" in body:
        raw_cues = body["cues"]
        if not isinstance(raw_cues, list) or not raw_cues:
            return jsonify({"success": False, "error": "cues must be a non-empty array"}), 400
        cues = []
        errors = []
        for i, raw in enumerate(raw_cues):
            norm = _normalize_cue(raw)
            if "error" in norm:
                errors.append({"index": i, "error": norm["error"]})
                continue
            ref_err = _validate_cue_action(norm)
            if ref_err:
                errors.append({"index": i, "error": ref_err})
                continue
            cues.append(norm)
        if errors:
            return jsonify({
                "success": False,
                "error": "One or more cues are invalid",
                "cue_errors": errors,
            }), 400
        cues.sort(key=lambda c: c["at_ms"])
        cl["cues"] = cues
        cl["duration_ms"] = cues[-1]["at_ms"] if cues else 0

    _save_cue_lists(data)
    return jsonify({"success": True, "cue_list": _serialize_cue_list(cl)})


@app.route("/api/cue_lists/<cl_id_or_name>", methods=["DELETE"])
def delete_cue_list(cl_id_or_name):
    """Remove a cue list. Stops playback first if it's currently running."""
    data, cl = _find_cue_list(cl_id_or_name)
    if cl is None:
        return jsonify({"success": False, "error": f"Cue list not found: {cl_id_or_name}"}), 404
    _stop_cue_list(cl["id"])
    data["cue_lists"] = [c for c in data["cue_lists"] if c["id"] != cl["id"]]
    _save_cue_lists(data)
    return jsonify({"success": True, "deleted": {"id": cl["id"], "name": cl["name"]}})


@app.route("/api/cue_lists/<cl_id_or_name>/go", methods=["POST"])
def go_cue_list(cl_id_or_name):
    """GO — start cue list playback from the top.

    If the list is already running, the old run is cancelled and a fresh
    run starts (matches "press GO twice = restart" intuition).
    """
    _, cl = _find_cue_list(cl_id_or_name)
    if cl is None:
        return jsonify({"success": False, "error": f"Cue list not found: {cl_id_or_name}"}), 404
    if not cl.get("cues"):
        return jsonify({"success": False, "error": "Cue list is empty"}), 400
    _go_cue_list(cl)
    return jsonify({
        "success": True,
        "cue_list": {"id": cl["id"], "name": cl["name"]},
        "started_at": time.time(),
        "duration_ms": cl.get("duration_ms", 0),
        "duration": _format_time_ms(cl.get("duration_ms", 0)),
        "cue_count": len(cl["cues"]),
    })


@app.route("/api/cue_lists/<cl_id_or_name>/stop", methods=["POST"])
def stop_cue_list(cl_id_or_name):
    """Stop a running cue list. Fixtures hold whatever state the last fired
    cue left them in — follow with blackout() or activate_scene() if you
    want a deterministic finish."""
    _, cl = _find_cue_list(cl_id_or_name)
    if cl is None:
        return jsonify({"success": False, "error": f"Cue list not found: {cl_id_or_name}"}), 404
    was_running = _stop_cue_list(cl["id"])
    return jsonify({
        "success": True,
        "cue_list": {"id": cl["id"], "name": cl["name"]},
        "was_running": was_running,
    })


@app.route("/api/cue_lists/<cl_id_or_name>/preview", methods=["POST"])
def preview_cue_list(cl_id_or_name):
    """Preview — apply whatever cue would be active at a given point in
    time, without starting playback or touching any running cue list.

    Body: {"at_ms": 1500}   (also accepts "at" in any _parse_time_ms form)

    Powers "click anywhere on the timeline -> preview this instant".
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"success": False, "error": "Body must be a JSON object"}), 400

    _, cl = _find_cue_list(cl_id_or_name)
    if cl is None:
        return jsonify({"success": False, "error": f"Cue list not found: {cl_id_or_name}"}), 404

    at_input = body.get("at_ms") if "at_ms" in body else body.get("at")
    at_ms = _parse_time_ms(at_input)
    if at_ms is None:
        return jsonify({"success": False, "error": f"'at_ms' is not parseable: {at_input!r}"}), 400

    cue = _cue_active_at(cl.get("cues", []), at_ms)
    if cue is None:
        return jsonify({"success": True, "at_ms": at_ms, "applied": None})

    try:
        execute_lighting_action(
            {"action": cue["action"], "parameters": cue["parameters"]},
            target_groups=cue.get("groups"),
            source="cue-preview",
        )
    except Exception as e:
        log.warning("cue_preview_failed", cue_list_id=cl["id"], action=cue["action"], error=str(e))
        return jsonify({"success": False, "error": f"Failed to apply cue: {e}"}), 500

    return jsonify({"success": True, "at_ms": at_ms, "applied": _serialize_cue(cue)})


@app.route("/api/cue_lists/<cl_id_or_name>/waveform", methods=["GET"])
def cue_list_waveform(cl_id_or_name):
    """Return fixed-resolution amplitude peaks for a cue list's associated
    audio file, for the frontend to render as a static timeline overlay.

    No audio playback — display data only. Returns an empty peaks array
    (200) when there's no associated audio, rather than an error, since
    "no audio yet" is a normal state for a cue list.
    """
    _, cl = _find_cue_list(cl_id_or_name)
    if cl is None:
        return jsonify({"success": False, "error": f"Cue list not found: {cl_id_or_name}"}), 404

    audio_path = _audio_file_path(cl)
    if audio_path is None or not audio_path.exists():
        return jsonify({
            "success": True,
            "audio_file": cl.get("audio_file"),
            "resolution_ms": 50,
            "peaks": [],
        })

    try:
        peaks = _wav_peaks(audio_path, resolution_ms=50)
    except (wave.Error, EOFError, OSError) as e:
        log.warning("cue_waveform_decode_failed", cue_list_id=cl["id"], audio_file=cl.get("audio_file"), error=str(e))
        return jsonify({
            "success": True,
            "audio_file": cl.get("audio_file"),
            "resolution_ms": 50,
            "peaks": [],
        })

    return jsonify({
        "success": True,
        "audio_file": cl.get("audio_file"),
        "resolution_ms": 50,
        "duration_ms": cl.get("duration_ms", 0),
        "peaks": peaks,
    })


# =============================================================================
# Audio reactivity — BPM detection + onset flash (issue #28)
# =============================================================================
#
# Architecture:
#   AudioEngine (audio_engine.py) owns the capture thread and aubio detectors.
#   It publishes {"type":"bpm"|"onset", ...} events to registered subscribers.
#
#   Two subscribers are wired at engine.start() time:
#     1. _audio_socketio_subscriber  — emits "audio_bpm" / "audio_onset" to
#        all connected browser clients so the UI tab stays live.
#     2. _onset_flash_subscriber     — calls apply_brightness_live for the
#        blink-on-onset effect.  Registered only when react_to is set.
#
#   A beat-clock chase engine (_run_audio_chase_async) drives step scenes in
#   time with detected BPM — required because QLC+ has no runtime speed-
#   control command over the WebSocket.
#
# Storage: audio chase metadata lives in AUDIO_CHASES_FILE (sidecar JSON,
#   same pattern as CUE_LISTS_FILE).  QLC+ workspace XML is untouched.


def _audio_socketio_subscriber(event: dict) -> None:
    """Forward audio engine events to connected browser clients."""
    if event.get("type") == "bpm":
        socketio.emit("audio_bpm", {"bpm": event["bpm"]})
    elif event.get("type") == "onset":
        socketio.emit("audio_onset", {
            "onset_ms": event["onset_ms"],
            "rms": event.get("rms", 0),
        })


def _make_onset_flash_subscriber(react_to: str = "any", target_groups=None):
    """Return a subscriber that briefly flashes fixture brightness on onset."""
    def _cb(event: dict) -> None:
        if event.get("type") != "onset":
            return
        try:
            apply_brightness_live(255, target_groups=target_groups)
        except Exception as exc:
            print(f"[onset-flash] error: {exc}")
    return _cb


# Module-level reference so we can remove the onset flash subscriber on disable
_onset_flash_cb = None


def _load_audio_chases() -> dict:
    if not AUDIO_CHASES_FILE.exists():
        return {"audio_chases": {}}
    try:
        data = json.loads(AUDIO_CHASES_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {"audio_chases": {}}
    data.setdefault("audio_chases", {})
    return data


def _save_audio_chases(data: dict) -> None:
    AUDIO_CHASES_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUDIO_CHASES_FILE.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Beat-clock chase engine
# ---------------------------------------------------------------------------


async def _run_audio_chase_async(chase_key: str, step_channel_values: list) -> None:
    """Step through a chase in time with the detected audio BPM.

    step_channel_values is pre-resolved list of [(abs_ch, val), ...] per step,
    so we avoid re-parsing the workspace XML each beat.
    """
    n = len(step_channel_values)
    if n == 0:
        return

    current_step = 0
    with _active_audio_chases_lock:
        _active_audio_chases.setdefault(chase_key, {})["running"] = True

    try:
        while True:
            cvs = step_channel_values[current_step % n]
            if cvs:
                set_channel_values(cvs)
            bpm = _audio_engine.get_bpm()
            interval_ms = bpm_to_interval_ms(bpm) if bpm > 0 else 500.0
            current_step += 1
            await asyncio.sleep(interval_ms / 1000.0)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"[audio-chase {chase_key}] error: {exc}")
    finally:
        with _active_audio_chases_lock:
            _active_audio_chases.pop(chase_key, None)


def _start_audio_chase(chase_id_or_name: str, react_to: str = "any") -> dict:
    """Pre-resolve step scenes and launch the beat-clock task."""
    chase = _find_function_element(chase_id_or_name, function_type="Chaser")
    if chase is None:
        return {"success": False, "error": f"Chase not found: {chase_id_or_name}"}

    chase_key = str(chase.get("ID", chase_id_or_name))

    # Stop any existing beat-clock for this chase
    with _active_audio_chases_lock:
        existing = _active_audio_chases.get(chase_key)
    if existing and existing.get("task"):
        existing["task"].cancel()

    # Pre-resolve each step → channel values
    steps = sorted(
        _find_children(chase, "Step"),
        key=lambda s: int(s.get("Number", "0")) if s.get("Number", "0").isdigit() else 0,
    )
    step_cvs = []
    for step in steps:
        scene_ref = step.get("Values") or (step.text or "").strip()
        scene_el = _find_scene_element(scene_ref) if scene_ref else None
        step_cvs.append(scene_to_channel_values(scene_el) if scene_el else [])

    if _qlc_loop is None:
        _start_qlc_loop()

    coro = _run_audio_chase_async(chase_key, step_cvs)
    task = asyncio.run_coroutine_threadsafe(coro, _qlc_loop)

    with _active_audio_chases_lock:
        _active_audio_chases[chase_key] = {"task": task, "react_to": react_to}

    # Persist to sidecar
    data = _load_audio_chases()
    data["audio_chases"][chase_key] = {
        "chase_name": chase.get("Name", ""),
        "react_to": react_to,
    }
    _save_audio_chases(data)

    return {
        "success": True,
        "chase": {"id": chase_key, "name": chase.get("Name", "")},
        "react_to": react_to,
    }


def _stop_audio_chase(chase_id_or_name: str) -> dict:
    chase = _find_function_element(chase_id_or_name, function_type="Chaser")
    chase_key = str(chase.get("ID", chase_id_or_name)) if chase else str(chase_id_or_name)
    with _active_audio_chases_lock:
        entry = _active_audio_chases.get(chase_key)
    if not entry:
        return {"success": False, "error": f"Audio chase not running: {chase_id_or_name}"}
    task = entry.get("task")
    if task:
        task.cancel()
    return {"success": True, "chase_key": chase_key}


# ---------------------------------------------------------------------------
# REST endpoints — /api/audio
# ---------------------------------------------------------------------------


@app.route("/api/audio", methods=["GET"])
def get_audio_state():
    """Return current audio engine state."""
    return jsonify(_audio_engine.get_state())


@app.route("/api/audio/enable", methods=["POST"])
def enable_audio():
    """Start the audio engine.

    Body (all optional):
        {
          "device":      null | int | "hw:1,0",   # sounddevice device id/name
          "sensitivity": 0.02                      # noise-gate RMS threshold
          "react_to":    "any" | "kick" | "snare" # onset flash mode; null = off
        }
    """
    global _onset_flash_cb
    body = request.get_json(silent=True) or {}

    device = body.get("device")
    sensitivity = body.get("sensitivity")
    react_to = body.get("react_to")

    # Wire SocketIO subscriber (idempotent)
    _audio_engine.unsubscribe(_audio_socketio_subscriber)
    _audio_engine.subscribe(_audio_socketio_subscriber)

    # Remove old onset flash subscriber if any
    if _onset_flash_cb is not None:
        _audio_engine.unsubscribe(_onset_flash_cb)
        _onset_flash_cb = None

    # Register new onset flash subscriber
    if react_to:
        _onset_flash_cb = _make_onset_flash_subscriber(react_to)
        _audio_engine.subscribe(_onset_flash_cb)

    ok = _audio_engine.start(device=device, sensitivity=sensitivity)
    if not ok:
        return jsonify({
            "success": False,
            "error": (
                "Audio engine unavailable — install aubio, sounddevice, and numpy on the Pi. "
                "See scripts/provisioning/setup.sh for the apt packages."
            ),
            "available": False,
        }), 503

    return jsonify({"success": True, **_audio_engine.get_state()})


@app.route("/api/audio/disable", methods=["POST"])
def disable_audio():
    """Stop the audio engine and any running audio chases."""
    global _onset_flash_cb
    _audio_engine.stop()
    if _onset_flash_cb is not None:
        _audio_engine.unsubscribe(_onset_flash_cb)
        _onset_flash_cb = None
    _audio_engine.unsubscribe(_audio_socketio_subscriber)
    return jsonify({"success": True, **_audio_engine.get_state()})


@app.route("/api/audio/calibrate", methods=["POST"])
def calibrate_audio():
    """Auto-set noise gate from recent ambient audio samples.

    The engine must already be enabled so samples are available.
    Sets sensitivity to 3× the measured background RMS.
    """
    result = _audio_engine.calibrate()
    if not result["success"]:
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/audio/chase/<path:chase_id>/start", methods=["POST"])
def start_audio_chase(chase_id):
    """Start a beat-clock-driven chase slaved to the detected audio BPM.

    Body (optional):
        { "react_to": "any" | "kick" | "snare" }
    """
    if not _audio_engine.available:
        return jsonify({"success": False, "error": "Audio engine not available"}), 503
    body = request.get_json(silent=True) or {}
    react_to = body.get("react_to", "any")
    result = _start_audio_chase(chase_id, react_to=react_to)
    if not result["success"]:
        return jsonify(result), 404
    return jsonify(result)


@app.route("/api/audio/chase/<path:chase_id>/stop", methods=["POST"])
def stop_audio_chase(chase_id):
    """Stop a running audio-driven chase."""
    result = _stop_audio_chase(chase_id)
    if not result["success"]:
        return jsonify(result), 404
    return jsonify(result)


@app.route("/api/audio/chases", methods=["GET"])
def list_audio_chases():
    """List audio chases and which are currently running."""
    data = _load_audio_chases()
    with _active_audio_chases_lock:
        running_keys = set(_active_audio_chases.keys())
    chases = []
    for key, meta in data["audio_chases"].items():
        chases.append({
            "chase_id": key,
            "chase_name": meta.get("chase_name", ""),
            "react_to": meta.get("react_to", "any"),
            "running": key in running_keys,
        })
    return jsonify({"audio_chases": chases})


# =============================================================================
# MIDI controller input (OSS-1143)
# =============================================================================
#
# Backend-only slice of #26: a python-rtmidi listener thread (mirrors the
# _qlc_loop background-thread pattern above) feeds parsed messages through
# midi_engine.dispatch_midi_message(), which triggers the SAME call paths
# the web UI / MCP tools already use — set_channel_values(), scene
# activation, chase start/stop. No new lighting logic, just new triggers.
#
# Storage: ~/.qlcplus/midi_mappings.json, same {"mappings": [...]} sidecar
# pattern as fixture_groups.json / audio_chases.json.
#
# The hardware-dependent bits (rtmidi port discovery/hot-plug) live in
# midi_engine.MidiListener and are gated behind `.available`, so this
# section — and the server as a whole — imports and runs cleanly on a
# headless Pi with python-rtmidi absent or no controller plugged in.

_midi_mappings_lock = threading.Lock()
_midi_last_values: dict = {}   # mapping_id -> last raw MIDI value (0-127)
_midi_chase_state: dict = {}   # mapping_id -> bool (chase_toggle running state)


def _load_midi_mappings() -> list:
    if not MIDI_MAPPINGS_FILE.exists():
        return []
    try:
        data = json.loads(MIDI_MAPPINGS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    mappings = data.get("mappings") if isinstance(data, dict) else None
    return mappings if isinstance(mappings, list) else []


def _save_midi_mappings(mappings: list) -> None:
    MIDI_MAPPINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    MIDI_MAPPINGS_FILE.write_text(json.dumps({"mappings": mappings}, indent=2))


def _midi_resolve_channel(fixture_id, channel_offset):
    """fixture_id/channel_offset -> absolute DMX channel, or None if the
    fixture doesn't exist in the current workspace (e.g. a stale mapping)."""
    for fixture in get_workspace_fixtures():
        if fixture.get("id") == fixture_id:
            return _absolute_channel(fixture, channel_offset)
    return None


def _midi_start_chase(chase_id) -> bool:
    """Start chase playback for MIDI dispatch — same primitives as the
    /api/chases/<id>/start route, without the Flask request/response wrapping
    (the dispatch runs on the listener thread, outside a request context)."""
    chase = _find_function_element(chase_id, function_type="Chaser")
    if chase is None:
        return False
    fid = chase.get("ID")
    if not (fid and fid.isdigit()):
        return False
    if chase.get("TempoSource", "fixed") == "tap":
        scene_ids = _chase_step_scene_ids(chase)
        speed = next(iter(_find_children(chase, "Speed")), None)
        initial_step_ms = float(speed.get("Duration", "500")) if speed is not None else 500.0
        _start_tap_runner(fid, scene_ids, initial_step_ms)
        return True
    ok, _raw = set_function_status(int(fid), running=True)
    return ok


def _midi_stop_chase(chase_id) -> bool:
    """Stop chase playback for MIDI dispatch — mirrors /api/chases/<id>/stop."""
    chase = _find_function_element(chase_id, function_type="Chaser")
    if chase is None:
        return False
    fid = chase.get("ID")
    if not (fid and fid.isdigit()):
        return False
    tap_was_running = _stop_tap_runner(fid)
    ok, _raw = set_function_status(int(fid), running=False)
    return ok or tap_was_running


def _midi_actions() -> dict:
    return {
        "set_channel_values": set_channel_values,
        "resolve_channel": _midi_resolve_channel,
        "activate_scene": apply_existing_scene_live,
        "start_chase": _midi_start_chase,
        "stop_chase": _midi_stop_chase,
    }


def _on_midi_message(port_name: str, raw_message: list) -> None:
    """MidiListener callback — runs on the listener thread. Never raises:
    a malformed message or a mapping referencing missing fixtures/scenes/
    chases is dropped, not fatal, so one bad controller can't take down the
    listener thread."""
    try:
        parsed = midi_engine.parse_midi_message(raw_message)
        if parsed is None:
            return
        with _midi_mappings_lock:
            mappings = _load_midi_mappings()
            result = midi_engine.dispatch_midi_message(
                parsed, mappings, _midi_actions(), _midi_chase_state
            )
            if result.get("matched") and result.get("mapping_id"):
                _midi_last_values[result["mapping_id"]] = parsed["value"]
        if result.get("matched"):
            _emit("midi_dispatch", {
                "port": port_name,
                "mapping_id": result.get("mapping_id"),
                "action": result.get("action"),
            })
    except Exception as exc:
        log.error("midi_dispatch_failed", error=str(exc))


_midi_listener = midi_engine.MidiListener(dispatch_fn=_on_midi_message)


@app.route("/api/midi/devices", methods=["GET"])
def midi_devices():
    """List connected MIDI input devices. Always returns 200 with an empty
    list when python-rtmidi isn't installed or nothing is plugged in."""
    return jsonify({
        "devices": _midi_listener.list_device_names(),
        "available": _midi_listener.available,
    })


@app.route("/api/midi/mappings", methods=["GET"])
def list_midi_mappings():
    return jsonify({"mappings": _load_midi_mappings()})


@app.route("/api/midi/mappings", methods=["POST"])
def create_midi_mapping():
    """Create a MIDI mapping.

    Body:
        {
          "name": "Fixture 0 master",           # optional label
          "input": {"type": "cc", "channel": null, "number": 21},
          "action": {"type": "channel", "fixture_id": 0, "channel_offset": 0,
                     "out_min": 0, "out_max": 255, "curve": "linear"}
        }
    """
    data = request.get_json(silent=True) or {}
    mapping, error = midi_engine.build_mapping(data)
    if error:
        return jsonify({"success": False, "error": error}), 400
    with _midi_mappings_lock:
        mappings = _load_midi_mappings()
        mappings.append(mapping)
        _save_midi_mappings(mappings)
    _emit("midi_mapping_modified", {"mapping_id": mapping["id"], "action": "created"})
    return jsonify({"success": True, "mapping": mapping})


@app.route("/api/midi/mappings/<mapping_id>", methods=["PATCH"])
def update_midi_mapping(mapping_id):
    """Replace an existing mapping's input/action/name. Body shape matches POST."""
    data = request.get_json(silent=True) or {}
    with _midi_mappings_lock:
        mappings = _load_midi_mappings()
        existing = next((m for m in mappings if m.get("id") == mapping_id), None)
        if existing is None:
            return jsonify({"success": False, "error": f"Mapping '{mapping_id}' not found"}), 404

        merged = {
            "name": data.get("name", existing.get("name")),
            "input": data.get("input", existing.get("input")),
            "action": data.get("action", existing.get("action")),
        }
        mapping, error = midi_engine.build_mapping(merged, mapping_id=mapping_id)
        if error:
            return jsonify({"success": False, "error": error}), 400

        mappings = [mapping if m.get("id") == mapping_id else m for m in mappings]
        _save_midi_mappings(mappings)
    _emit("midi_mapping_modified", {"mapping_id": mapping_id, "action": "updated"})
    return jsonify({"success": True, "mapping": mapping})


@app.route("/api/midi/mappings/<mapping_id>", methods=["DELETE"])
def delete_midi_mapping(mapping_id):
    with _midi_mappings_lock:
        mappings = _load_midi_mappings()
        remaining = [m for m in mappings if m.get("id") != mapping_id]
        if len(remaining) == len(mappings):
            return jsonify({"success": False, "error": f"Mapping '{mapping_id}' not found"}), 404
        _save_midi_mappings(remaining)
        _midi_last_values.pop(mapping_id, None)
        _midi_chase_state.pop(mapping_id, None)
    _emit("midi_mapping_modified", {"mapping_id": mapping_id, "action": "deleted"})
    return jsonify({"success": True})


@app.route("/api/midi/state", methods=["GET"])
def midi_state():
    """Last CC value (0-127) seen per mapping, for the future UI tab to poll."""
    with _midi_mappings_lock:
        return jsonify({"state": dict(_midi_last_values)})


# =============================================================================
# Agentic chat (issue: agent-driven web UI experience)
# =============================================================================
#
# Replaces the v2.2-era one-shot "natural language → JSON action" pattern with
# a proper conversational agent that has access to the full tool surface.
# Uses the LLM provider's native tool-calling (Anthropic tool_use / OpenAI
# function calling), runs a multi-step tool dispatch loop server-side, and
# returns the final assistant message + a trace of which tools were called.
#
# Why server-side: the browser shouldn't hold the provider API key, and the
# tool execution needs access to our internal helpers (workspace XML write,
# QLC+ WebSocket, persistent storage).
#
# Stateless: the client sends the full conversation history on each turn,
# the server processes one turn and returns the updated history. Same model
# as ChatGPT / Claude.ai SPAs.


# ----------------------------------------------------------------------------
# Tool registry — single source of truth, consumed by both Anthropic + OpenAI
# ----------------------------------------------------------------------------
#
# Each tool has:
#   • name        — what the LLM calls it
#   • description — what shows in the LLM's tool catalog
#   • input_schema — JSON schema for the arguments (Anthropic format)
#   • handler     — (args) → dict; tool execution
#
# Handlers dispatch via the Flask test client where there's existing endpoint
# logic to reuse (POST/PATCH/DELETE paths with validation), or call helpers
# directly for read-only operations.

def _call_self(method: str, path: str, body: dict | None = None) -> dict:
    """Dispatch into our own endpoints via Flask's test client.
    Lets chat tool handlers reuse endpoint validation / response shape
    without re-implementing each one. Negligible perf cost (in-process)."""
    with app.test_client() as client:
        if method == "GET":
            r = client.get(path)
        elif method == "POST":
            r = client.post(path, json=body or {})
        elif method == "PATCH":
            r = client.patch(path, json=body or {})
        elif method == "DELETE":
            r = client.delete(path, json=body or {})
        else:
            return {"success": False, "error": f"Unknown method: {method}"}
        try:
            return r.get_json() or {}
        except Exception:
            return {"success": r.status_code < 400, "status_code": r.status_code}


def _build_chat_tools() -> list[dict]:
    """Build the tool registry. Mirrors the MCP catalog so the agentic chat
    feels identical to using Claude Desktop against the MCP server."""

    GROUPS_OPTIONAL = {
        "type": "array",
        "items": {"type": "string"},
        "description": "Optional list of group names to target. Omit for all fixtures.",
    }

    return [
        # ── Discovery ─────────────────────────────────────────────────────
        {
            "name": "list_fixtures",
            "description": "List every DMX fixture in the workspace with channel metadata.",
            "input_schema": {"type": "object", "properties": {}, "required": []},
            "handler": lambda a: _call_self("GET", "/api/fixtures"),
        },
        {
            "name": "list_groups",
            "description": "List named fixture groups and their members.",
            "input_schema": {"type": "object", "properties": {}, "required": []},
            "handler": lambda a: _call_self("GET", "/api/groups"),
        },
        {
            "name": "list_scenes",
            "description": "List saved scene functions in the workspace.",
            "input_schema": {"type": "object", "properties": {}, "required": []},
            "handler": lambda a: _call_self("GET", "/api/scenes"),
        },
        {
            "name": "list_templates",
            "description": "List built-in scene templates (party, ambient, youtube-studio, etc).",
            "input_schema": {"type": "object", "properties": {}, "required": []},
            "handler": lambda a: _call_self("GET", "/api/templates"),
        },
        {
            "name": "list_chases",
            "description": "List QLC+ chases (ordered scene sequences) in the workspace.",
            "input_schema": {"type": "object", "properties": {}, "required": []},
            "handler": lambda a: _call_self("GET", "/api/chases"),
        },
        {
            "name": "list_cue_lists",
            "description": "List saved cue lists (audio-synced shows) with runtime status.",
            "input_schema": {"type": "object", "properties": {}, "required": []},
            "handler": lambda a: _call_self("GET", "/api/cue_lists"),
        },
        {
            "name": "get_active_cue_lists",
            "description": "List cue lists currently playing with elapsed time and cues fired.",
            "input_schema": {"type": "object", "properties": {}, "required": []},
            "handler": lambda a: _call_self("GET", "/api/cue_lists/active"),
        },
        {
            "name": "get_channel_values",
            "description": "Return the current live DMX channel values from QLC+ as a {channel: value} map.",
            "input_schema": {"type": "object", "properties": {}, "required": []},
            "handler": lambda a: _call_self("GET", "/api/channel_values"),
        },
        {
            "name": "get_status",
            "description": "System health: AI provider, QLC+ service, workspace, persistent WebSocket.",
            "input_schema": {"type": "object", "properties": {}, "required": []},
            "handler": lambda a: _call_self("GET", "/api/status"),
        },

        # ── Quick actions ────────────────────────────────────────────────
        {
            "name": "activate_scene",
            "description": "Apply an existing saved scene immediately. Accepts scene name or numeric ID.",
            "input_schema": {
                "type": "object",
                "properties": {"scene": {"type": "string"}},
                "required": ["scene"],
            },
            "handler": lambda a: _call_self("POST", f"/api/scenes/{a['scene']}/activate"),
        },
        {
            "name": "apply_template",
            "description": (
                "Apply a built-in template (party, ambient, youtube-studio, "
                "spotlight, work-light, warm-white, cool-white)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "template": {"type": "string"},
                    "groups": GROUPS_OPTIONAL,
                },
                "required": ["template"],
            },
            "handler": lambda a: _call_self("POST", "/api/action", {
                "action": "apply_template",
                "parameters": {"template": a["template"]},
                "groups": a.get("groups"),
            }),
        },
        {
            "name": "adjust_brightness",
            "description": "Set or nudge overall brightness. Value: '0-255', '75%', '+30', '-20'.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "value": {"type": "string"},
                    "groups": GROUPS_OPTIONAL,
                },
                "required": ["value"],
            },
            "handler": lambda a: _call_self("POST", "/api/action", {
                "action": "adjust_brightness",
                "parameters": {"value": a["value"]},
                "groups": a.get("groups"),
            }),
        },
        {
            "name": "adjust_color",
            "description": "Set a color preset (red, green, blue, warm, cool, amber, magenta, cyan, white).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "color": {"type": "string"},
                    "intensity": {"type": "string", "description": "Optional 0-255, '%', or '+/-'"},
                    "groups": GROUPS_OPTIONAL,
                },
                "required": ["color"],
            },
            "handler": lambda a: _call_self("POST", "/api/action", {
                "action": "adjust_color",
                "parameters": {"color": a["color"], "intensity": a.get("intensity", "255")},
                "groups": a.get("groups"),
            }),
        },
        {
            "name": "color_temperature",
            "description": "Set Kelvin white balance (1800K candle → 10000K overcast). Role-aware per fixture.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "kelvin": {"type": "number", "minimum": 1800, "maximum": 10000},
                    "intensity": {"type": "string", "description": "Optional 0-255, '%', or '+/-'"},
                    "groups": GROUPS_OPTIONAL,
                },
                "required": ["kelvin"],
            },
            "handler": lambda a: _call_self("POST", "/api/action", {
                "action": "color_temperature",
                "parameters": {"kelvin": a["kelvin"], "intensity": a.get("intensity")},
                "groups": a.get("groups"),
            }),
        },
        {
            "name": "palette",
            "description": (
                "Assign different colors / Kelvin values to different groups in one call. "
                "Values: color preset name (\"warm\"), Kelvin number (3200), "
                "or dict with intensity."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "assignments": {
                        "type": "object",
                        "description": (
                            "Map of group name → value (color preset, Kelvin number, "
                            "or {color/kelvin, intensity})"
                        ),
                        "additionalProperties": True,
                    },
                },
                "required": ["assignments"],
            },
            "handler": lambda a: _call_self("POST", "/api/action", {
                "action": "palette",
                "parameters": {"assignments": a["assignments"]},
            }),
        },
        {
            "name": "strobe",
            "description": "Strobe fixtures at Hz rate (0-20Hz, or 'off' to stop).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "rate": {"oneOf": [{"type": "number"}, {"type": "string"}]},
                    "intensity": {"type": "string"},
                    "groups": GROUPS_OPTIONAL,
                },
                "required": ["rate"],
            },
            "handler": lambda a: _call_self("POST", "/api/action", {
                "action": "strobe",
                "parameters": {"rate": a["rate"], "intensity": a.get("intensity")},
                "groups": a.get("groups"),
            }),
        },
        {
            "name": "fade",
            "description": "Fade brightness to target over duration seconds.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "0-255 or '%'"},
                    "duration": {"type": "string", "description": "Seconds"},
                    "groups": GROUPS_OPTIONAL,
                },
                "required": ["target", "duration"],
            },
            "handler": lambda a: _call_self("POST", "/api/action", {
                "action": "fade",
                "parameters": {"target": a["target"], "duration": a["duration"]},
                "groups": a.get("groups"),
            }),
        },
        {
            "name": "blackout",
            "description": "Instantly zero every channel on targeted fixtures (kill-all, clears strobe/macro too).",
            "input_schema": {
                "type": "object",
                "properties": {"groups": GROUPS_OPTIONAL},
                "required": [],
            },
            "handler": lambda a: _call_self("POST", "/api/blackout", {"groups": a.get("groups")}),
        },
        {
            "name": "generate_scene",
            "description": (
                "AI-synthesize a new scene from a description and apply it live. "
                "Result includes scene_xml — pass to save_scene to persist."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "groups": GROUPS_OPTIONAL,
                },
                "required": ["description"],
            },
            "handler": lambda a: _call_self("POST", "/api/action", {
                "action": "generate_scene",
                "parameters": {"description": a["description"]},
                "groups": a.get("groups"),
            }),
        },
        {
            "name": "snapshot_scene",
            "description": "Save the current live channel state as a new scene.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "path": {"type": "string", "description": "Optional folder, defaults to 'AI Generated'"},
                },
                "required": ["name"],
            },
            "handler": lambda a: _call_self("POST", "/api/scenes/snapshot", {
                "name": a["name"], "path": a.get("path", "AI Generated"),
            }),
        },

        # ── Scene management ─────────────────────────────────────────────
        {
            "name": "describe_scene",
            "description": "Return per-fixture channel breakdown of a saved scene.",
            "input_schema": {
                "type": "object",
                "properties": {"scene": {"type": "string"}},
                "required": ["scene"],
            },
            "handler": lambda a: _call_self("GET", f"/api/scenes/{a['scene']}"),
        },
        {
            "name": "delete_scene",
            "description": "Delete a saved scene from the workspace.",
            "input_schema": {
                "type": "object",
                "properties": {"scene": {"type": "string"}},
                "required": ["scene"],
            },
            "handler": lambda a: _call_self("DELETE", f"/api/scenes/{a['scene']}"),
        },
        {
            "name": "rename_scene",
            "description": "Rename a scene (and/or move its folder path).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "scene": {"type": "string"},
                    "new_name": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["scene", "new_name"],
            },
            "handler": lambda a: _call_self("PATCH", f"/api/scenes/{a['scene']}", {
                "name": a["new_name"], **({"path": a["path"]} if "path" in a else {}),
            }),
        },
        {
            "name": "duplicate_scene",
            "description": "Copy a scene under a new name (basis for variations).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "scene": {"type": "string"},
                    "new_name": {"type": "string"},
                },
                "required": ["scene", "new_name"],
            },
            "handler": lambda a: _call_self("POST", f"/api/scenes/{a['scene']}/duplicate", {
                "name": a["new_name"],
            }),
        },

        # ── Group management ─────────────────────────────────────────────
        {
            "name": "create_group",
            "description": "Create a fixture group from a name + list of fixture IDs.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "fixtures": {"type": "array", "items": {"type": "integer"}},
                    "description": {"type": "string"},
                },
                "required": ["name", "fixtures"],
            },
            "handler": lambda a: _call_self("POST", "/api/groups", {
                "name": a["name"],
                "fixtures": a["fixtures"],
                "description": a.get("description", ""),
            }),
        },
        {
            "name": "delete_group",
            "description": "Remove a fixture group.",
            "input_schema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            "handler": lambda a: _call_self("DELETE", f"/api/groups/{a['name']}"),
        },
        {
            "name": "update_group",
            "description": "Rename a group, change description, or replace its fixture list.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "new_name": {"type": "string"},
                    "description": {"type": "string"},
                    "fixtures": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["name"],
            },
            "handler": lambda a: _call_self("PATCH", f"/api/groups/{a['name']}", {
                **({"name": a["new_name"]} if "new_name" in a else {}),
                **({"description": a["description"]} if "description" in a else {}),
                **({"fixtures": a["fixtures"]} if "fixtures" in a else {}),
            }),
        },
        {
            "name": "add_fixtures_to_group",
            "description": "Append fixture IDs to an existing group.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "fixtures": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["name", "fixtures"],
            },
            "handler": lambda a: _call_self("POST", f"/api/groups/{a['name']}/fixtures", {
                "fixtures": a["fixtures"],
            }),
        },
        {
            "name": "remove_fixtures_from_group",
            "description": "Remove fixture IDs from a group.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "fixtures": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["name", "fixtures"],
            },
            "handler": lambda a: _call_self("DELETE", f"/api/groups/{a['name']}/fixtures", {
                "fixtures": a["fixtures"],
            }),
        },

        # ── Chases ───────────────────────────────────────────────────────
        {
            "name": "describe_chase",
            "description": "Full chase definition with resolved per-step scene names + timing.",
            "input_schema": {
                "type": "object",
                "properties": {"chase": {"type": "string"}},
                "required": ["chase"],
            },
            "handler": lambda a: _call_self("GET", f"/api/chases/{a['chase']}"),
        },
        {
            "name": "create_chase",
            "description": (
                "Build a QLC+ chase from a name + ordered list of scene references. "
                "Steps can be scene names, IDs, or {scene, hold_ms} dicts."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "steps": {
                        "type": "array",
                        "description": "Ordered scene refs (strings, ints, or dicts with per-step timing)",
                    },
                    "fade_in_ms": {"type": "integer"},
                    "hold_ms": {"type": "integer"},
                    "fade_out_ms": {"type": "integer"},
                    "direction": {"type": "string", "enum": ["Forward", "Backward"]},
                    "run_order": {"type": "string", "enum": ["Loop", "SingleShot", "PingPong", "Random"]},
                    "tempo_source": {
                        "type": "string",
                        "enum": ["fixed", "tap", "audio"],
                        "description": "fixed (default), tap (follows live tap-tempo BPM), audio (reserved)",
                    },
                },
                "required": ["name", "steps"],
            },
            "handler": lambda a: _call_self("POST", "/api/chases", a),
        },
        {
            "name": "delete_chase",
            "description": "Delete a chase from the workspace.",
            "input_schema": {
                "type": "object",
                "properties": {"chase": {"type": "string"}},
                "required": ["chase"],
            },
            "handler": lambda a: _call_self("DELETE", f"/api/chases/{a['chase']}"),
        },
        {
            "name": "start_chase",
            "description": "Begin chase playback (loops forever unless run_order is SingleShot).",
            "input_schema": {
                "type": "object",
                "properties": {"chase": {"type": "string"}},
                "required": ["chase"],
            },
            "handler": lambda a: _call_self("POST", f"/api/chases/{a['chase']}/start"),
        },
        {
            "name": "stop_chase",
            "description": "Halt chase playback; fixtures hold their last step.",
            "input_schema": {
                "type": "object",
                "properties": {"chase": {"type": "string"}},
                "required": ["chase"],
            },
            "handler": lambda a: _call_self("POST", f"/api/chases/{a['chase']}/stop"),
        },

        # ── Cue lists ────────────────────────────────────────────────────
        {
            "name": "describe_cue_list",
            "description": "Full cue-list definition with per-cue timestamps and actions.",
            "input_schema": {
                "type": "object",
                "properties": {"cue_list": {"type": "string"}},
                "required": ["cue_list"],
            },
            "handler": lambda a: _call_self("GET", f"/api/cue_lists/{a['cue_list']}"),
        },
        {
            "name": "create_cue_list",
            "description": (
                "Build a cue list (audio-synced show). Each cue: "
                "{at: '0:32', scene/chase/action: ...}. Timestamps accept "
                "'0:32.500', '32s', '32500ms', or integer ms."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "cues": {"type": "array", "description": "Ordered cues — each with at/at_ms + scene/chase/action"},
                },
                "required": ["name", "cues"],
            },
            "handler": lambda a: _call_self("POST", "/api/cue_lists", a),
        },
        {
            "name": "delete_cue_list",
            "description": "Delete a cue list (stops playback first if running).",
            "input_schema": {
                "type": "object",
                "properties": {"cue_list": {"type": "string"}},
                "required": ["cue_list"],
            },
            "handler": lambda a: _call_self("DELETE", f"/api/cue_lists/{a['cue_list']}"),
        },
        {
            "name": "go_cue_list",
            "description": "GO — start cue list playback from the top. Sync with audio by triggering at track start.",
            "input_schema": {
                "type": "object",
                "properties": {"cue_list": {"type": "string"}},
                "required": ["cue_list"],
            },
            "handler": lambda a: _call_self("POST", f"/api/cue_lists/{a['cue_list']}/go"),
        },
        {
            "name": "stop_cue_list",
            "description": "Halt a running cue list; fixtures hold last fired state.",
            "input_schema": {
                "type": "object",
                "properties": {"cue_list": {"type": "string"}},
                "required": ["cue_list"],
            },
            "handler": lambda a: _call_self("POST", f"/api/cue_lists/{a['cue_list']}/stop"),
        },

        # ── Diagnostics ──────────────────────────────────────────────────
        {
            "name": "get_system_info",
            "description": "Pi-level health: CPU temp, load, memory, disk, uptime, USB, service status.",
            "input_schema": {"type": "object", "properties": {}, "required": []},
            "handler": lambda a: _call_self("GET", "/api/diagnostics/system"),
        },
        {
            "name": "test_dmx",
            "description": "Run an R→G→B sweep to verify DMX reaches the rig. Snapshots + restores channel state.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "duration": {"type": "number", "minimum": 2, "maximum": 30},
                    "groups": GROUPS_OPTIONAL,
                },
                "required": [],
            },
            "handler": lambda a: _call_self("POST", "/api/diagnostics/test_dmx", a),
        },
        {
            "name": "get_logs",
            "description": (
                "Tail systemd journal for an allowlisted service: "
                "qlcplus-web, lighting-control, lighting-mcp, nginx."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "service": {"type": "string", "enum": ["qlcplus-web", "lighting-control", "lighting-mcp", "nginx"]},
                    "n": {"type": "integer", "minimum": 1, "maximum": 500},
                },
                "required": ["service"],
            },
            "handler": lambda a: _call_self("GET", f"/api/diagnostics/logs/{a['service']}?n={a.get('n', 50)}"),
        },
        {
            "name": "rf_scan",
            "description": (
                "Survey the 2.4 GHz WiFi band from the Pi's radio and analyze it for wireless-DMX "
                "interference risk — per-channel congestion, the quietest window, and suggestions."
            ),
            "input_schema": {"type": "object", "properties": {}, "required": []},
            "handler": lambda a: _call_self("POST", "/api/diagnostics/rf_scan"),
        },

        # ── Setup utility ────────────────────────────────────────────────
        {
            "name": "identify_fixture",
            "description": (
                "Flash a single fixture so the operator can locate it physically. "
                "Pulses 2s × 4 then restores prior state."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "fixture_id": {"type": "integer"},
                    "duration": {"type": "number"},
                    "pulses": {"type": "integer"},
                },
                "required": ["fixture_id"],
            },
            "handler": lambda a: _call_self("POST", f"/api/fixtures/{a['fixture_id']}/identify", {
                "duration": a.get("duration", 2),
                "pulses": a.get("pulses", 4),
            }),
        },
    ]


# ----------------------------------------------------------------------------
# Failover error classification
# ----------------------------------------------------------------------------

def _is_failover_error(exc) -> bool:
    """True when the exception indicates a transient provider failure worth retrying elsewhere.

    Timeouts and connection errors are always failover-eligible.
    HTTP 5xx and 429 (rate-limit) are failover-eligible.
    HTTP 4xx (except 429) are real errors — not retried.
    """
    import requests
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(exc, requests.HTTPError):
        status = exc.response.status_code if exc.response is not None else 0
        return status >= 500 or status == 429
    return False


# ----------------------------------------------------------------------------
# Anthropic tool-use loop
# ----------------------------------------------------------------------------

_CHAT_SYSTEM_PROMPT = (
    "You are the lighting operator for a QLC+ studio rig. The user is a creator, "
    "photographer, theatre LD, or event producer who wants you to drive the rig. "
    "Use the discovery tools (list_fixtures, list_groups, list_scenes, list_chases, "
    "list_cue_lists, list_templates) when you need to understand the current state — "
    "don't ask the user for context you can fetch yourself. "
    "Prefer high-level abstractions (palette, color_temperature, apply_template, "
    "activate_scene) over low-level set_channel writes. "
    "When the user wants a 'show' or 'timed sequence', use cue lists. "
    "When the user wants a looping motion sequence, use chases. "
    "For Kelvin requests, use color_temperature with the Kelvin number directly. "
    "Be concise — operators don't want long explanations, they want lights changed."
)


def _anthropic_chat_loop(messages: list, tools: list, model: str, api_key: str, max_iters: int = 10) -> dict:
    """Run Anthropic's tool_use loop against /v1/messages.

    Returns:
        {
          "messages": updated history (assistant messages + tool_result user messages),
          "tool_calls": [{name, input, output}, ...] for telemetry,
          "stop_reason": 'end_turn' | 'max_iters' | 'tool_use' | 'error',
        }
    """
    import requests

    anthropic_tools = [
        {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
        for t in tools
    ]
    tool_handlers = {t["name"]: t["handler"] for t in tools}
    tool_calls_made = []
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }

    for _ in range(max_iters):
        payload = {
            "model": model,
            "max_tokens": 4096,
            "system": _CHAT_SYSTEM_PROMPT,
            "messages": messages,
            "tools": anthropic_tools,
        }
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers, json=payload, timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            http_status = None
            if hasattr(e, "response") and e.response is not None:
                http_status = e.response.status_code
            return {
                "messages": messages,
                "tool_calls": tool_calls_made,
                "stop_reason": "error",
                "error": f"Anthropic API error: {e}",
                "should_failover": _is_failover_error(e),
                "http_status": http_status,
            }

        # Append assistant response (preserves tool_use blocks for protocol)
        messages.append({"role": "assistant", "content": data.get("content", [])})

        # Find tool_use blocks
        tool_use_blocks = [b for b in data.get("content", []) if b.get("type") == "tool_use"]
        if not tool_use_blocks:
            return {
                "messages": messages,
                "tool_calls": tool_calls_made,
                "stop_reason": data.get("stop_reason", "end_turn"),
            }

        # Execute each requested tool call
        tool_results = []
        for block in tool_use_blocks:
            name = block.get("name")
            tool_input = block.get("input", {}) or {}
            tool_id = block.get("id")
            handler = tool_handlers.get(name)
            if handler is None:
                output = {"error": f"Unknown tool: {name}"}
            else:
                try:
                    output = handler(tool_input)
                except Exception as e:
                    output = {"error": f"{type(e).__name__}: {e}"}

            tool_calls_made.append({"name": name, "input": tool_input, "output": output})
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": json.dumps(output)[:8000],  # cap to keep context manageable
            })

        # Feed tool results back as a user turn
        messages.append({"role": "user", "content": tool_results})

    return {
        "messages": messages,
        "tool_calls": tool_calls_made,
        "stop_reason": "max_iters",
    }


# ----------------------------------------------------------------------------
# OpenAI tool-calling loop (parallel path)
# ----------------------------------------------------------------------------

def _openai_chat_loop(messages: list, tools: list, model: str, api_key: str, max_iters: int = 10) -> dict:
    """Run OpenAI's function-calling loop against /v1/chat/completions.

    Translates between OpenAI's message shape and our normalized shape on
    the way out — the client always sees Anthropic-style messages.
    """
    import requests

    openai_tools = [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]
    tool_handlers = {t["name"]: t["handler"] for t in tools}
    tool_calls_made = []

    # Translate incoming history (Anthropic-style) to OpenAI-style
    openai_messages = [{"role": "system", "content": _CHAT_SYSTEM_PROMPT}]
    for m in messages:
        if m["role"] == "user":
            content = m["content"]
            if isinstance(content, list):
                # tool_result blocks from a previous turn — flatten to text
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        openai_messages.append({
                            "role": "tool",
                            "tool_call_id": block.get("tool_use_id"),
                            "content": block.get("content", ""),
                        })
                    else:
                        openai_messages.append({"role": "user", "content": str(block)})
            else:
                openai_messages.append({"role": "user", "content": content})
        elif m["role"] == "assistant":
            content = m["content"]
            if isinstance(content, list):
                # Anthropic-style content blocks — convert tool_use to tool_calls
                text_parts = []
                tool_calls = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        tool_calls.append({
                            "id": block.get("id"),
                            "type": "function",
                            "function": {
                                "name": block.get("name"),
                                "arguments": json.dumps(block.get("input", {}) or {}),
                            },
                        })
                msg = {"role": "assistant", "content": "\n".join(text_parts) or None}
                if tool_calls:
                    msg["tool_calls"] = tool_calls
                openai_messages.append(msg)
            else:
                openai_messages.append({"role": "assistant", "content": content})

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    for _ in range(max_iters):
        try:
            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": openai_messages,
                    "tools": openai_tools,
                    "max_tokens": 4096,
                },
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            http_status = None
            if hasattr(e, "response") and e.response is not None:
                http_status = e.response.status_code
            return {
                "messages": messages,
                "tool_calls": tool_calls_made,
                "stop_reason": "error",
                "error": f"OpenAI API error: {e}",
                "should_failover": _is_failover_error(e),
                "http_status": http_status,
            }

        msg = data["choices"][0]["message"]
        openai_messages.append(msg)

        # Mirror into the Anthropic-style history we return
        assistant_blocks = []
        if msg.get("content"):
            assistant_blocks.append({"type": "text", "text": msg["content"]})
        for tc in msg.get("tool_calls") or []:
            try:
                tc_input = json.loads(tc["function"]["arguments"])
            except (ValueError, KeyError):
                tc_input = {}
            assistant_blocks.append({
                "type": "tool_use",
                "id": tc["id"],
                "name": tc["function"]["name"],
                "input": tc_input,
            })
        messages.append({"role": "assistant", "content": assistant_blocks})

        if not msg.get("tool_calls"):
            return {
                "messages": messages,
                "tool_calls": tool_calls_made,
                "stop_reason": data["choices"][0].get("finish_reason", "end_turn"),
            }

        # Execute each tool call
        tool_result_blocks = []
        for tc in msg["tool_calls"]:
            name = tc["function"]["name"]
            try:
                tc_input = json.loads(tc["function"]["arguments"])
            except ValueError:
                tc_input = {}
            handler = tool_handlers.get(name)
            if handler is None:
                output = {"error": f"Unknown tool: {name}"}
            else:
                try:
                    output = handler(tc_input)
                except Exception as e:
                    output = {"error": f"{type(e).__name__}: {e}"}

            tool_calls_made.append({"name": name, "input": tc_input, "output": output})
            output_str = json.dumps(output)[:8000]
            openai_messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": output_str,
            })
            tool_result_blocks.append({
                "type": "tool_result",
                "tool_use_id": tc["id"],
                "content": output_str,
            })
        # Mirror into normalized history
        messages.append({"role": "user", "content": tool_result_blocks})

    return {
        "messages": messages,
        "tool_calls": tool_calls_made,
        "stop_reason": "max_iters",
    }


# ----------------------------------------------------------------------------
# Failover orchestrator
# ----------------------------------------------------------------------------

def _run_chat_with_failover(incoming_messages: list, tools: list) -> dict:
    """Run one chat turn through the failover chain.

    Iterates _AI_FAILOVER_CHAIN; skips providers whose circuit breaker is open
    or whose API key is missing. On a failover-eligible error records the failure
    and continues to the next provider. Returns the loop result dict augmented
    with 'served_by', 'switched', and 'attempts'.
    """
    chain = _AI_FAILOVER_CHAIN
    attempts: list = []
    messages_to_try = list(incoming_messages)

    for provider in chain:
        model, api_key = _provider_config(provider)
        if not api_key:
            logging.warning("chat_failover: skipping %s — no API key configured", provider)
            attempts.append({"provider": provider, "skipped": "no_api_key"})
            continue

        # Skipping on an open breaker only makes sense if there's another
        # provider left in the chain to fail over to. For a single-provider
        # chain (the common case — AI_PROVIDER_FAILOVER unset), skipping
        # would just manufacture a hard 60s outage window where pre-failover
        # behavior would have kept retrying directly. Still attempt it; the
        # breaker's failure count keeps accumulating for observability.
        if len(chain) > 1:
            with _breaker_lock:
                if _breaker_is_open(_provider_breaker, provider, time.time()):
                    logging.warning("chat_failover: skipping %s — circuit breaker open", provider)
                    attempts.append({"provider": provider, "skipped": "breaker_open"})
                    continue

        if provider == "anthropic":
            result = _anthropic_chat_loop(list(messages_to_try), tools, model, api_key)
        else:
            result = _openai_chat_loop(list(messages_to_try), tools, model, api_key)

        if result.get("stop_reason") == "error" and result.get("should_failover"):
            with _breaker_lock:
                _breaker_record_failure(_provider_breaker, provider, time.time())
            logging.warning(
                "chat_failover: %s failed (http_status=%s), trying next provider. error=%s",
                provider, result.get("http_status"), result.get("error"),
            )
            attempts.append({
                "provider": provider,
                "error": result.get("error"),
                "http_status": result.get("http_status"),
            })
            # Forward messages at the clean turn boundary to the next provider.
            messages_to_try = result.get("messages", messages_to_try)
            continue

        # Success or non-retryable error (e.g. 401) — return as-is.
        if result.get("stop_reason") != "error":
            with _breaker_lock:
                _breaker_record_success(_provider_breaker, provider)

        result["served_by"] = provider
        result["switched"] = bool(chain) and provider != chain[0]
        result["attempts"] = attempts
        return result

    # All providers exhausted.
    return {
        "messages": incoming_messages,
        "tool_calls": [],
        "stop_reason": "error",
        "error": "All providers in failover chain failed or are unavailable.",
        "should_failover": False,
        "served_by": None,
        "switched": False,
        "attempts": attempts,
    }


# ----------------------------------------------------------------------------
# Chat history persistence helpers
# ----------------------------------------------------------------------------

def _derive_chat_title(messages: list) -> str:
    """Return a short title from the first user text in messages."""
    for m in messages:
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, str):
                return chat_store.derive_title(content)
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        return chat_store.derive_title(block.get("text", ""))
    return "New conversation"


def _try_summarize(conv_id: str) -> None:
    """Background task: generate and persist a 1-2 sentence conversation summary."""
    try:
        msgs = chat_store.get_messages(CHAT_DB_PATH, conv_id)
        lines = []
        for m in msgs[-30:]:
            content = m["content"]
            text = ""
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        break
            if text.strip():
                lines.append(f"{m['role'].upper()}: {text.strip()[:200]}")
        if not lines:
            return
        system = (
            "Summarize this lighting control conversation in 1-2 sentences. "
            "Focus on what was accomplished (scenes created, colors set, issues resolved). "
            "Be concise."
        )
        conv_text = "\n".join(lines)
        if AI_PROVIDER == "anthropic" and AI_API_KEY:
            summary = call_anthropic(system, conv_text)
        elif AI_PROVIDER == "openai" and AI_API_KEY:
            summary = call_openai(system, conv_text)
        else:
            return
        chat_store.update_summary(CHAT_DB_PATH, conv_id, summary.strip())
    except Exception:
        pass


# ----------------------------------------------------------------------------
# /api/conversations routes
# ----------------------------------------------------------------------------

@app.route("/api/conversations", methods=["GET"])
def list_conversations_route():
    return jsonify(chat_store.list_conversations(CHAT_DB_PATH))


@app.route("/api/conversations", methods=["POST"])
def create_conversation_route():
    data = request.get_json(silent=True) or {}
    title = data.get("title", "")
    conv_id = chat_store.create_conversation(CHAT_DB_PATH, title=title)
    return jsonify({"id": conv_id}), 201


@app.route("/api/conversations/search", methods=["GET"])
def search_conversations_route():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    return jsonify(chat_store.search_conversations(CHAT_DB_PATH, q))


@app.route("/api/conversations/<conv_id>", methods=["GET"])
def get_conversation_route(conv_id):
    if not chat_store.conversation_exists(CHAT_DB_PATH, conv_id):
        return jsonify({"error": "not found"}), 404
    msgs = chat_store.get_messages(CHAT_DB_PATH, conv_id)
    return jsonify({"messages": msgs})


@app.route("/api/conversations/<conv_id>", methods=["DELETE"])
def delete_conversation_route(conv_id):
    chat_store.delete_conversation(CHAT_DB_PATH, conv_id)
    return "", 204


@app.route("/api/conversations/<conv_id>/fork", methods=["POST"])
def fork_conversation_route(conv_id):
    if not chat_store.conversation_exists(CHAT_DB_PATH, conv_id):
        return jsonify({"error": "not found"}), 404
    data = request.get_json(silent=True) or {}
    upto = data.get("upto_index")
    new_id = chat_store.fork_conversation(CHAT_DB_PATH, conv_id, upto_index=upto)
    return jsonify({"id": new_id}), 201


# ----------------------------------------------------------------------------
# /api/chat endpoint
# ----------------------------------------------------------------------------

@app.route("/api/chat", methods=["POST"])
def handle_chat():
    """Stateless agentic chat — client sends full message history, server
    processes one turn (which may include several internal tool dispatches),
    returns the updated history plus a trace of tool calls.

    Body:
        {
          "messages": [
            { "role": "user", "content": "Set the key lights to 3200K" }
          ]
        }

    Response:
        {
          "success": true,
          "messages": [...updated history...],
          "tool_calls": [{name, input, output}, ...],
          "stop_reason": "end_turn" | "max_iters" | "error",
          "provider": "anthropic",
          "model": "...",
        }
    """
    data = request.get_json(silent=True) or {}
    incoming_messages = data.get("messages") or []
    if not isinstance(incoming_messages, list) or not incoming_messages:
        return jsonify({"success": False, "error": "messages must be a non-empty array"}), 400

    if not _AI_FAILOVER_CHAIN:
        return jsonify({
            "success": False,
            "error": (
                f"Agentic chat requires AI_PROVIDER=anthropic or openai (got {AI_PROVIDER!r}). "
                "Ollama tool-calling is not supported yet."
            ),
        }), 400
    usable = [p for p in _AI_FAILOVER_CHAIN if _provider_config(p)[1]]
    if not usable:
        return jsonify({"success": False, "error": "No API key configured for any provider in the failover chain"}), 400

    # Resolve or create the conversation for persistence.
    conv_id = data.get("conversation_id") or None
    if conv_id and chat_store.conversation_exists(CHAT_DB_PATH, conv_id):
        existing_count = chat_store.message_count(CHAT_DB_PATH, conv_id)
    else:
        conv_id = chat_store.create_conversation(
            CHAT_DB_PATH, title=_derive_chat_title(incoming_messages)
        )
        existing_count = 0

    tools = _build_chat_tools()
    result = _run_chat_with_failover(list(incoming_messages), tools)
    served_by = result.get("served_by") or AI_PROVIDER
    served_model = _provider_config(served_by)[0] if served_by else AI_MODEL

    # Persist the new turns (everything that wasn't already in the DB).
    new_turns = result.get("messages", [])[existing_count:]
    if new_turns:
        chat_store.append_messages(CHAT_DB_PATH, conv_id, new_turns)

    # Auto-summarise asynchronously when we cross a multiple of CHAT_SUMMARIZE_EVERY.
    total = chat_store.message_count(CHAT_DB_PATH, conv_id)
    if total >= CHAT_SUMMARIZE_EVERY and total % CHAT_SUMMARIZE_EVERY == 0:
        threading.Thread(target=_try_summarize, args=(conv_id,), daemon=True).start()

    return jsonify({
        "success": result.get("stop_reason") not in ("error",),
        "messages": result.get("messages", []),
        "tool_calls": result.get("tool_calls", []),
        "stop_reason": result.get("stop_reason"),
        "provider": served_by,
        "model": served_model,
        "conversation_id": conv_id,
        "switched": result.get("switched", False),
        "failover_chain": _AI_FAILOVER_CHAIN,
        "attempts": result.get("attempts", []),
        **({"error": result["error"]} if result.get("error") else {}),
    })


# ----------------------------------------------------------------------------
# OSC backend — adapter binding OSC router verbs to the in-process helpers
# above. Kept in app.py (rather than osc_backend.py) because it closes over
# Flask-app internals; osc_backend.py itself stays a pure, dependency-light
# module the tests can import without touching app.py's startup side effects.
# ----------------------------------------------------------------------------

class _OscActions:
    def activate_scene(self, name):
        apply_existing_scene_live(name)

    def start_chase(self, name):
        _start_chase_by_ref(name)

    def set_channel(self, fixture_id, channel, value):
        fixtures = {str(f["id"]): f for f in get_workspace_fixtures()}
        fixture = fixtures.get(str(fixture_id))
        if fixture is None:
            log.warning("osc_set_channel_unknown_fixture", fixture_id=fixture_id)
            return
        set_channel_values([(_absolute_channel(fixture, channel), value)])

    def set_master(self, value):
        apply_brightness_live(value, target_groups=None)

    def blackout(self):
        _do_blackout(None)

    def cue_go(self, ref):
        cl = self._resolve_cue_list(ref)
        if cl and cl.get("cues"):
            _go_cue_list(cl)

    def cue_stop(self, ref):
        cl = self._resolve_cue_list(ref)
        if cl:
            _stop_cue_list(cl["id"])

    def cue_pause(self, ref):
        # Cue lists only support go/stop today — no pause primitive exists to
        # map onto (see parent #27). Log and no-op rather than guessing.
        log.info("osc_cue_pause_unsupported", ref=ref)

    def _resolve_cue_list(self, ref):
        if ref is not None:
            _, cl = _find_cue_list(ref)
            return cl
        data = _load_cue_lists()
        cue_lists = data["cue_lists"]
        if len(cue_lists) == 1:
            return cue_lists[0]
        log.warning("osc_cue_ref_missing_and_ambiguous", cue_list_count=len(cue_lists))
        return None


if __name__ == "__main__":
    # Check if lightsctl exists
    if not LIGHTSCTL.exists():
        log.error("lightsctl_not_found", path=str(LIGHTSCTL))
        sys.exit(1)

    # Start the dedicated QLC+ WebSocket loop in a background thread.
    # All QLC+ comms go through this one persistent connection.
    _start_qlc_loop()
    if not MOCK_DMX:
        try:
            _qlc_run(_ensure_qlc_ws(), timeout=5)
        except Exception as e:
            log.warning("initial_qlc_connect_failed", error=str(e))

    # Boot-time look restore + rolling last-look snapshot (see the
    # boot-restore block near set_channel_values for the rules).
    if BOOT_RESTORE_ENABLED and IS_LOCAL:
        threading.Thread(target=_boot_restore_last_look, daemon=True,
                         name="boot-restore").start()
        threading.Thread(target=_last_look_saver_loop, daemon=True,
                         name="last-look-saver").start()

    # Wire audio SocketIO subscriber so BPM/onset events stream to the browser.
    # The engine only starts capture when /api/audio/enable is called.
    _audio_engine.subscribe(_audio_socketio_subscriber)
    if _audio_engine.available:
        print("✓ Audio engine ready (aubio + sounddevice found)")
    else:
        print("⚠ Audio engine unavailable — aubio/sounddevice not installed")

    # Start the MIDI listener thread — device auto-discovery + reconnect on
    # hot-plug. No-op (never opens a port) when python-rtmidi isn't installed.
    _midi_listener.start()
    if _midi_listener.available:
        print("✓ MIDI engine ready (python-rtmidi found)")
    else:
        print("⚠ MIDI engine unavailable — python-rtmidi not installed")

    # OSC backend — inbound UDP listener + outbound /state/* feedback.
    # Fails soft: a busy port or missing python-osc logs a warning, never
    # blocks boot (the QLC+ single-writer path never depends on this).
    _osc_config = OscConfig.from_env()
    if _osc_config.enabled:
        try:
            start_listener(_osc_config, _OscActions())
            _osc_emitter = OscStateEmitter(build_udp_client(_osc_config))
            threading.Thread(
                target=drain_event_bus, args=(EVENT_BUS, _osc_emitter),
                daemon=True, name="osc-state-emitter",
            ).start()
        except Exception as e:
            log.warning("osc_backend_start_failed", error=str(e))

    # Run server with SocketIO (debug=False to avoid stat reloader doubling connections).
    #
    # NOTE on `allow_unsafe_werkzeug=True`:
    # We intentionally use Werkzeug's WSGI server for the single-user studio
    # LAN deploy. For a "real" production WSGI (gunicorn + eventlet/gevent
    # worker), see issue #47 — that migration needs Pi-side testing because
    # of the persistent QLC+ asyncio loop that lives in a thread.
    #
    # Silence Werkzeug's own "development server" warning (still visible on
    # service start otherwise). The flask-socketio "appears to be used in a
    # production deployment" line lands one level higher (a plain print() in
    # the library) and is harder to filter — left in place as the visible
    # marker that we're on the dev server.
    class _SilenceDevServerWarning(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return "development server" not in record.getMessage().lower()

    logging.getLogger("werkzeug").addFilter(_SilenceDevServerWarning())

    port = int(os.getenv("CONTROL_PORT", "5000"))
    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)
