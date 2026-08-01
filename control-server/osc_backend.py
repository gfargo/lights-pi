"""OSC backend — inbound listener, address routing, outbound state emission.

Routing (dispatch_osc) and outbound emission (OscStateEmitter) are pure: they
take plain data in and call injected collaborators, so they're unit-testable
with synthetic packets and a stub client, no real UDP socket required. Only
start_listener()/build_udp_client() touch python-osc's transport classes, and
those imports are deferred into the functions that need them so importing
this module for routing/emitter tests never requires a socket to bind.
"""
import os
import queue
import threading
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

    @classmethod
    def from_env(cls) -> "OscConfig":
        return cls(
            enabled=os.getenv("OSC_ENABLED", "true").strip().lower() not in ("0", "false", "no"),
            listen_host=os.getenv("OSC_LISTEN_HOST", "0.0.0.0"),
            listen_port=int(os.getenv("OSC_LISTEN_PORT", "8000")),
            out_host=os.getenv("OSC_OUT_HOST", "127.0.0.1"),
            out_port=int(os.getenv("OSC_OUT_PORT", "9000")),
        )


# ---------------------------------------------------------------------------
# Inbound routing — pure (no sockets)
# ---------------------------------------------------------------------------

def _err(address, message):
    return {"ok": False, "address": address, "error": message}


def _ok(address, **extra):
    return {"ok": True, "address": address, **extra}


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
    set_channel(fixture_id, channel, value), set_master(value), blackout(),
    cue_go(ref), cue_stop(ref), cue_pause(ref). Never raises — malformed or
    unknown addresses return an ``{"ok": False, ...}`` dict instead.
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
        actions.activate_scene(parts[1])
        return _ok(address, action="activate_scene", name=parts[1])

    if head == "chase" and len(parts) == 2:
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
        actions.blackout()
        return _ok(address, action="blackout")

    if head == "cue" and len(parts) == 2:
        verb = parts[1]
        cue_ref = args[0] if args else None
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
    """Translates EventBus envelopes into outbound OSC ``/state/*`` messages.

    ``client`` needs one method — ``send_message(address, value)`` — matching
    ``pythonosc.udp_client.SimpleUDPClient``. Tests pass a stub recorder.
    """

    def __init__(self, client):
        self._client = client

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
        elif event_type == "master_changed":
            value = data.get("value")
            if value is not None:
                self._client.send_message("/state/master", value)
        elif event_type == "chase_started":
            name = data.get("chase_name")
            if name is not None:
                self._client.send_message("/state/chase-active", name)
        elif event_type == "chase_stopped":
            self._client.send_message("/state/chase-active", "")


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


def start_listener(config: OscConfig, actions):
    """Build and start the OSC UDP server on a daemon thread.

    Returns the running server, or None if disabled or the port couldn't be
    bound (logged as a warning — a busy port must never crash boot).
    """
    if not config.enabled:
        log.info("osc_listener_disabled")
        return None

    from pythonosc.dispatcher import Dispatcher
    from pythonosc.osc_server import ThreadingOSCUDPServer

    disp = Dispatcher()
    disp.set_default_handler(lambda address, *args: _log_route(dispatch_osc(address, args, actions)))

    try:
        server = ThreadingOSCUDPServer((config.listen_host, config.listen_port), disp)
    except OSError as e:
        log.warning("osc_listen_bind_failed", host=config.listen_host, port=config.listen_port, error=str(e))
        return None

    threading.Thread(target=server.serve_forever, daemon=True, name="osc-listener").start()
    log.info("osc_listener_started", host=config.listen_host, port=config.listen_port)
    return server


def _log_route(result):
    if not result.get("ok"):
        log.warning("osc_route_failed", **result)
