"""OSC backend — inbound listener, address routing, outbound state emission.

Routing (dispatch_osc) and outbound emission (OscStateEmitter) are pure: they
take plain data in and call injected collaborators, so they're unit-testable
with synthetic packets and a stub client, no real UDP socket required. Only
start_listener()/build_udp_client() touch python-osc's transport classes, and
those imports are deferred into the functions that need them so importing
this module for routing/emitter tests never requires a socket to bind.
"""
import collections
import os
import queue
import re
import threading
import time
from dataclasses import dataclass

import structlog

log = structlog.get_logger("lights.osc")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class OscConfig:
    enabled: bool = True
    listen_host: str = "0.0.0.0"
    listen_port: int = 8000
    out_host: str = "127.0.0.1"
    out_port: int = 9000
    # True when OSC_OUT_HOST was set explicitly; feedback then defaults to
    # that fixed address instead of replying to whoever sent the last message.
    out_host_set: bool = False

    @classmethod
    def from_env(cls) -> "OscConfig":
        out_host = os.getenv("OSC_OUT_HOST", "").strip()
        return cls(
            enabled=os.getenv("OSC_ENABLED", "true").strip().lower() not in ("0", "false", "no"),
            listen_host=os.getenv("OSC_LISTEN_HOST", "0.0.0.0"),
            listen_port=int(os.getenv("OSC_LISTEN_PORT", "8000")),
            out_host=out_host or "127.0.0.1",
            out_port=int(os.getenv("OSC_OUT_PORT", "9000")),
            out_host_set=bool(out_host),
        )


def osc_slug(name) -> str:
    """Address-safe form of a scene or chase name: "Lights ON" -> "lights-on".

    OSC addresses can't carry spaces, so layouts address functions by ID or
    by this slug; the router matches either.
    """
    return re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")


# ---------------------------------------------------------------------------
# Inbound routing — pure (no sockets)
# ---------------------------------------------------------------------------

def _err(address, message):
    return {"ok": False, "address": address, "error": message}


def _ok(address, **extra):
    return {"ok": True, "address": address, **extra}


def _is_release(args):
    """True for a button release: one numeric argument equal to zero.

    TouchOSC buttons send 1 on press and 0 on release. Trigger addresses act
    on the press only, so a scene doesn't fire twice per tap. A message with
    no arguments (QLab's usual form) is a press.
    """
    if len(args) != 1 or isinstance(args[0], (str, bytes)):
        return False
    try:
        return float(args[0]) == 0.0
    except (TypeError, ValueError):
        return False


def _normalize_level(raw):
    """Accept a TouchOSC-style float 0.0-1.0 or a raw 0-255 value; return an int 0-255."""
    try:
        num = float(raw)
    except (TypeError, ValueError):
        return None
    if 0.0 <= num <= 1.0:
        num *= 255
    return max(0, min(255, round(num)))


def dispatch_osc(address, args, actions):
    """Route one inbound OSC message to the matching lighting action.

    ``actions`` exposes: activate_scene(name), start_chase(name),
    stop_chase(name), set_channel(fixture_id, channel, value),
    set_master(value), blackout(), cue_go(ref), cue_stop(ref), cue_pause(ref).
    Never raises — malformed or unknown addresses return an
    ``{"ok": False, ...}`` dict instead.

    Button releases (a single 0 argument) on trigger addresses are accepted
    and ignored, except on /chase/<name>, where 0 stops the chase so a
    TouchOSC toggle button starts it on and stops it off.
    """
    try:
        return _dispatch(str(address), tuple(args), actions)
    except Exception as e:
        log.warning("osc_dispatch_failed", address=address, error=str(e))
        return _err(address, str(e))


def _dispatch(address, args, actions):
    parts = [p for p in address.strip("/").split("/") if p != ""]
    if not parts:
        return _err(address, "empty address")

    head = parts[0]

    if head == "scene" and len(parts) == 2:
        if _is_release(args):
            return _ok(address, action="release")
        actions.activate_scene(parts[1])
        return _ok(address, action="activate_scene", name=parts[1])

    if head == "chase" and len(parts) == 2:
        if _is_release(args):
            actions.stop_chase(parts[1])
            return _ok(address, action="stop_chase", name=parts[1])
        actions.start_chase(parts[1])
        return _ok(address, action="start_chase", name=parts[1])

    if head == "fixture" and len(parts) == 3:
        if len(args) != 1:
            return _err(address, "expected exactly one value argument")
        try:
            fixture_id = int(parts[1])
            channel = int(parts[2])
        except ValueError:
            return _err(address, "fixture id and channel must be integers")
        value = _normalize_level(args[0])
        if value is None:
            return _err(address, "invalid channel value")
        actions.set_channel(fixture_id, channel, value)
        return _ok(address, action="set_channel", fixture_id=fixture_id, channel=channel, value=value)

    if head == "master" and len(parts) == 1:
        if len(args) != 1:
            return _err(address, "expected exactly one value argument")
        value = _normalize_level(args[0])
        if value is None:
            return _err(address, "invalid master value")
        actions.set_master(value)
        return _ok(address, action="set_master", value=value)

    if head == "blackout" and len(parts) == 1:
        if _is_release(args):
            return _ok(address, action="release")
        actions.blackout()
        return _ok(address, action="blackout")

    if head == "cue" and len(parts) == 2:
        verb = parts[1]
        if _is_release(args):
            return _ok(address, action="release")
        # A string argument names the cue list; a number is a button value.
        cue_ref = args[0] if args and isinstance(args[0], str) else None
        if verb == "go":
            actions.cue_go(cue_ref)
            return _ok(address, action="cue_go", cue=cue_ref)
        if verb == "stop":
            actions.cue_stop(cue_ref)
            return _ok(address, action="cue_stop", cue=cue_ref)
        if verb == "pause":
            actions.cue_pause(cue_ref)
            return _ok(address, action="cue_pause", cue=cue_ref)
        return _err(address, f"unknown cue verb: {verb}")

    return _err(address, f"unknown address: {address}")


# ---------------------------------------------------------------------------
# Outbound state emission — pure (takes an injected client)
# ---------------------------------------------------------------------------

class OscStateEmitter:
    """Translates EventBus envelopes into outbound OSC messages.

    Two kinds go out. ``/state/*`` messages carry names and raw values for
    scripts. Control feedback mirrors the input addresses, so a TouchOSC
    control lights up or moves without any scripting: the active scene's
    ``/scene/<id>`` and ``/scene/<slug>`` get 1.0 (and the previous one 0.0),
    chases likewise on ``/chase/...``, and ``/master`` gets 0.0–1.0.

    ``client`` needs one method — ``send_message(address, value)`` — matching
    ``pythonosc.udp_client.SimpleUDPClient``. Tests pass a stub recorder.
    """

    def __init__(self, client):
        self._client = client
        self._active_scene = []   # addresses lit for the current scene

    def _send_all(self, addresses, value):
        for address in addresses:
            self._client.send_message(address, value)

    @staticmethod
    def _addresses(kind, fid, name):
        out = []
        if fid not in (None, ""):
            out.append(f"/{kind}/{fid}")
        slug = osc_slug(name)
        if slug and slug != str(fid):
            out.append(f"/{kind}/{slug}")
        return out

    def on_event(self, event_type, data):
        try:
            self._handle(event_type, data or {})
        except Exception as e:
            log.warning("osc_emit_failed", event_type=event_type, error=str(e))

    def _handle(self, event_type, data):
        if event_type == "scene_activated":
            name = data.get("scene_name")
            if name is not None:
                self._client.send_message("/state/scene-active", name)
                lit = self._addresses("scene", data.get("scene_id"), name)
                self._send_all([a for a in self._active_scene if a not in lit], 0.0)
                self._send_all(lit, 1.0)
                self._active_scene = lit
        elif event_type == "master_changed":
            value = data.get("value")
            if value is not None:
                self._client.send_message("/state/master", value)
                try:
                    self._client.send_message("/master", round(max(0, min(255, float(value))) / 255, 4))
                except (TypeError, ValueError):
                    pass
        elif event_type == "chase_started":
            name = data.get("chase_name")
            if name is not None:
                self._client.send_message("/state/chase-active", name)
                self._send_all(self._addresses("chase", data.get("chase_id"), name), 1.0)
        elif event_type == "chase_stopped":
            self._client.send_message("/state/chase-active", "")
            self._send_all(self._addresses("chase", data.get("chase_id"), data.get("chase_name")), 0.0)


# ---------------------------------------------------------------------------
# Feedback destination + activity monitor — pure (socket factory injected)
# ---------------------------------------------------------------------------

FEEDBACK_MODES = ("sender", "fixed", "off")


class OscFeedbackClient:
    """``send_message`` that goes wherever feedback is currently pointed.

    Modes:
      * ``sender`` — reply to the IP that sent the most recent inbound
        message, on ``port``. Suits a phone or tablet whose address changes.
      * ``fixed`` — always ``host:port``.
      * ``off`` — send nothing.

    ``make_client(host, port)`` builds the underlying sender (python-osc's
    SimpleUDPClient in production, a stub in tests); one is cached per
    destination.
    """

    def __init__(self, make_client, mode="sender", host="", port=9000, monitor=None):
        self._make_client = make_client
        self._clients = {}
        self._lock = threading.Lock()
        self.mode = mode
        self.host = host
        self.port = port
        self.last_sender = None
        self._monitor = monitor

    def configure(self, mode, host, port):
        if mode not in FEEDBACK_MODES:
            raise ValueError(f"mode must be one of {FEEDBACK_MODES}")
        self.mode, self.host, self.port = mode, host, int(port)

    def note_sender(self, host):
        if host:
            self.last_sender = host

    def destination(self):
        if self.mode == "fixed" and self.host:
            return (self.host, self.port)
        if self.mode == "sender" and self.last_sender:
            return (self.last_sender, self.port)
        return None

    def send_message(self, address, value):
        dest = self.destination()
        if dest is None:
            return
        with self._lock:
            client = self._clients.get(dest)
            if client is None:
                client = self._clients[dest] = self._make_client(*dest)
        client.send_message(address, value)
        if self._monitor is not None:
            self._monitor.note_sent(dest)


class OscMonitor:
    """Recent inbound messages and counters, for the settings panel."""

    def __init__(self, size=25):
        self._recent = collections.deque(maxlen=size)
        self.received = 0
        self.sent = 0
        self.last_sent_to = None

    def note_received(self, source, address, args, result):
        self.received += 1
        self._recent.appendleft({
            "at": time.time(),
            "from": source,
            "address": str(address),
            "args": [a if isinstance(a, (int, float, str)) else repr(a) for a in args][:4],
            "ok": bool(result.get("ok")),
            "action": result.get("action"),
            "error": result.get("error"),
        })

    def note_sent(self, dest):
        self.sent += 1
        self.last_sent_to = f"{dest[0]}:{dest[1]}"

    def recent(self):
        return list(self._recent)


def drain_event_bus(event_bus, emitter, stop_event=None):
    """Pull envelopes from a fresh EventBus subscriber queue and hand each to
    *emitter*. Blocking — run this on a daemon thread."""
    q = event_bus.subscribe()
    try:
        while stop_event is None or not stop_event.is_set():
            try:
                envelope = q.get(timeout=1)
            except queue.Empty:
                continue
            emitter.on_event(envelope["type"], envelope["data"])
    finally:
        event_bus.unsubscribe(q)


# ---------------------------------------------------------------------------
# Socket bootstrap — the only place python-osc's transport classes are touched
# ---------------------------------------------------------------------------

def build_udp_client(config: OscConfig):
    from pythonosc.udp_client import SimpleUDPClient
    return SimpleUDPClient(config.out_host, config.out_port)


def start_listener(config: OscConfig, actions, on_message=None):
    """Build and start the OSC UDP server on a daemon thread.

    ``on_message(source_ip, address, args, result)``, when given, is called
    after every routed message (feedback uses it to learn who to reply to,
    the settings panel to show recent traffic).

    Returns the running server, or None if disabled or the port couldn't be
    bound (logged as a warning — a busy port must never crash boot).
    """
    if not config.enabled:
        log.info("osc_listener_disabled")
        return None

    from pythonosc.dispatcher import Dispatcher
    from pythonosc.osc_server import BlockingOSCUDPServer

    disp = Dispatcher()
    def _handle(client_address, address, *args):
        result = dispatch_osc(address, args, actions)
        _log_route(result)
        if on_message is not None:
            try:
                on_message(client_address[0] if client_address else None, address, args, result)
            except Exception as e:  # observers must never break routing
                log.warning("osc_on_message_failed", error=str(e))

    disp.set_default_handler(_handle, needs_reply_address=True)

    try:
        server = BlockingOSCUDPServer((config.listen_host, config.listen_port), disp)
    except OSError as e:
        log.warning("osc_listen_bind_failed", host=config.listen_host, port=config.listen_port, error=str(e))
        return None

    threading.Thread(target=_serve, args=(server,), daemon=True, name="osc-listener").start()
    log.info("osc_listener_started", host=config.listen_host, port=config.listen_port)
    return server


def _serve(server):
    """Receive loop that stays cooperative under gunicorn's eventlet worker.

    Not server.serve_forever(): socketserver picks its selector class when the
    module is first imported, which gunicorn does (via logging.config) before
    eventlet monkey-patches anything. Under the eventlet worker that loop then
    spins in a non-green poll() and never yields, so the hub — and every HTTP
    request behind it — starves. Verified on the Pi 2026-09-19: the worker
    listened on :5000 with 60+ connections queued and accepted none.

    recvfrom() on the (green, post-patch) socket yields properly. Handling the
    packet inline keeps dispatch on the hub, which the QLC+ bridge in app.py
    requires; under plain `python app.py` this is an ordinary OS thread and a
    plain blocking recvfrom, so behaviour there is unchanged.
    """
    sock = server.socket
    while True:
        try:
            data, client = sock.recvfrom(server.max_packet_size)
        except OSError as e:
            log.warning("osc_listener_recv_failed", error=str(e))
            return
        try:
            server.finish_request((data, sock), client)
        except Exception as e:  # a bad packet must never kill the listener
            log.warning("osc_listener_handle_failed", error=str(e))


def _log_route(result):
    if not result.get("ok"):
        log.warning("osc_route_failed", **result)
