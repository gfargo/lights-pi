"""MIDI engine: python-rtmidi listener + pure CC/note dispatch logic.

Designed to import cleanly even when python-rtmidi is absent — all
hardware-gated code lives inside MidiListener and is guarded by the
`available` flag, mirroring audio_engine.py. The parsing/scaling/dispatch
helpers are pure and fully unit-testable with constructed fake MIDI
messages — no device required.
"""
import collections
import threading
import time
import uuid

# ---------------------------------------------------------------------------
# Pure helpers — no hardware, fully unit-testable
# ---------------------------------------------------------------------------

VALID_INPUT_TYPES = ("cc", "note")
# channel and master take a fader or knob (CC); scene, chase_toggle and
# blackout take a button or pad (note on).
VALID_ACTION_TYPES = ("channel", "master", "scene", "chase_toggle", "blackout")


def parse_midi_message(data) -> dict | None:
    """Parse a raw 1-3 byte MIDI message into a plain dict, or None if it's
    malformed / out of range / a message type we don't act on (pitch bend,
    program change, sysex, clock, etc).

    Returns: {"type": "cc"|"note_on"|"note_off", "channel": 0-15, "number": 0-127, "value": 0-127}

    A Note On with velocity 0 is normalized to "note_off" per the MIDI spec
    convention (many controllers send it that way instead of a real Note Off).
    """
    if not data:
        return None
    try:
        status = int(data[0])
        data1 = int(data[1]) if len(data) > 1 else 0
        data2 = int(data[2]) if len(data) > 2 else 0
    except (TypeError, ValueError):
        return None

    if not (0 <= status <= 255) or not (0 <= data1 <= 127) or not (0 <= data2 <= 127):
        return None

    kind_nibble = status & 0xF0
    channel = status & 0x0F

    if kind_nibble == 0xB0:
        kind = "cc"
    elif kind_nibble == 0x90:
        kind = "note_on" if data2 > 0 else "note_off"
    elif kind_nibble == 0x80:
        kind = "note_off"
    else:
        return None

    return {"type": kind, "channel": channel, "number": data1, "value": data2}


def scale_value(value, out_min: int = 0, out_max: int = 255, curve: str = "linear",
                 in_min: int = 0, in_max: int = 127) -> int:
    """Scale a MIDI 0-127 value into an output range.

    `curve` is reserved for future non-linear response curves; only "linear"
    is implemented today and unknown curve names fall back to it.
    """
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = in_min
    value = max(in_min, min(in_max, value))
    span_in = in_max - in_min
    if span_in <= 0:
        return int(out_min)
    ratio = (value - in_min) / span_in
    scaled = out_min + ratio * (out_max - out_min)
    return int(round(max(out_min, min(out_max, scaled))))


def _coerce_int(value, lo=None, hi=None):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    if lo is not None and n < lo:
        return None
    if hi is not None and n > hi:
        return None
    return n


def build_mapping(data: dict, mapping_id: str | None = None):
    """Validate + normalize a mapping CRUD payload.

    Returns (mapping_dict, None) on success or (None, error_message) on
    validation failure. Does not touch disk or the workspace — pure.
    """
    data = data if isinstance(data, dict) else {}
    raw_input = data.get("input")
    raw_input = raw_input if isinstance(raw_input, dict) else {}
    input_type = raw_input.get("type")
    if input_type not in VALID_INPUT_TYPES:
        return None, f"input.type must be one of {list(VALID_INPUT_TYPES)}"

    number = _coerce_int(raw_input.get("number"), 0, 127)
    if number is None:
        return None, "input.number must be an integer 0-127"

    channel = None
    if raw_input.get("channel") is not None:
        channel = _coerce_int(raw_input.get("channel"), 0, 15)
        if channel is None:
            return None, "input.channel must be an integer 0-15 (or omitted to match any channel)"

    raw_action = data.get("action")
    raw_action = raw_action if isinstance(raw_action, dict) else {}
    action_type = raw_action.get("type")
    if action_type not in VALID_ACTION_TYPES:
        return None, f"action.type must be one of {list(VALID_ACTION_TYPES)}"

    action = {"type": action_type}
    if action_type == "channel":
        fixture_id = _coerce_int(raw_action.get("fixture_id"), 0, None)
        if fixture_id is None:
            return None, "action.fixture_id is required (non-negative integer) for a channel mapping"
        offset = _coerce_int(raw_action.get("channel_offset", 0), 0, None)
        if offset is None:
            return None, "action.channel_offset must be a non-negative integer"
        out_min = _coerce_int(raw_action.get("out_min", 0), 0, 255)
        if out_min is None:
            return None, "action.out_min must be an integer 0-255"
        out_max = _coerce_int(raw_action.get("out_max", 255), 0, 255)
        if out_max is None:
            return None, "action.out_max must be an integer 0-255"
        if out_min > out_max:
            return None, "action.out_min must be <= action.out_max"
        action.update({
            "fixture_id": fixture_id,
            "channel_offset": offset,
            "out_min": out_min,
            "out_max": out_max,
            "curve": str(raw_action.get("curve") or "linear"),
        })
    elif action_type == "scene":
        scene_id = str(raw_action.get("scene_id") or "").strip()
        if not scene_id:
            return None, "action.scene_id is required for a scene mapping"
        action["scene_id"] = scene_id
    elif action_type == "chase_toggle":
        chase_id = str(raw_action.get("chase_id") or "").strip()
        if not chase_id:
            return None, "action.chase_id is required for a chase_toggle mapping"
        action["chase_id"] = chase_id
    # "master" and "blackout" take no parameters.

    if action_type in ("channel", "master") and input_type != "cc":
        return None, f"a {action_type} mapping needs a fader or knob (input.type \"cc\")"
    if action_type in ("scene", "chase_toggle", "blackout") and input_type != "note":
        return None, f"a {action_type} mapping needs a button or pad (input.type \"note\")"

    mapping = {
        "id": mapping_id or uuid.uuid4().hex[:12],
        "name": str(data.get("name") or "").strip(),
        "input": {"type": input_type, "channel": channel, "number": number},
        "action": action,
    }
    return mapping, None


def _input_matches(mapping_input: dict, msg: dict) -> bool:
    mtype = mapping_input.get("type")
    if mtype == "cc":
        if msg["type"] != "cc":
            return False
    elif mtype == "note":
        if msg["type"] not in ("note_on", "note_off"):
            return False
    else:
        return False

    if mapping_input.get("number") != msg["number"]:
        return False

    mapped_channel = mapping_input.get("channel")
    if mapped_channel is not None and mapped_channel != msg["channel"]:
        return False

    return True


def dispatch_midi_message(msg: dict | None, mappings: list, actions: dict, chase_state: dict | None = None) -> dict:
    """Route one parsed MIDI message through the configured mappings.

    msg: output of parse_midi_message(), or None (ignored).
    mappings: list of mapping dicts as produced by build_mapping().
    actions: dict of callables the dispatch invokes —
        set_channel_values(updates: list[(abs_channel:int, value:int)]) -> bool
        resolve_channel(fixture_id:int, channel_offset:int) -> int | None
        set_master(level:int 0-255) -> Any
        blackout() -> Any
        activate_scene(scene_id) -> Any
        start_chase(chase_id) -> Any
        stop_chase(chase_id) -> Any
    chase_state: mutable dict of mapping_id -> bool (running), owned by the
        caller so toggle state survives across dispatch calls. A fresh dict
        is used (and discarded) if omitted.

    Returns {"matched": bool, "mapping_id": str|None, "action": str|None}.
    Never raises — a malformed message or a mapping referencing a missing
    fixture/scene/chase is reported as unmatched rather than crashing the
    listener thread.
    """
    if not msg:
        return {"matched": False, "mapping_id": None, "action": None}
    if chase_state is None:
        chase_state = {}

    for mapping in mappings:
        if not _input_matches(mapping.get("input") or {}, msg):
            continue

        mapping_id = mapping.get("id")
        action = mapping.get("action") or {}
        action_type = action.get("type")

        if action_type == "channel" and msg["type"] == "cc":
            abs_channel = actions["resolve_channel"](action.get("fixture_id"), action.get("channel_offset", 0))
            if abs_channel is None:
                return {"matched": False, "mapping_id": mapping_id, "action": action_type}
            scaled = scale_value(
                msg["value"],
                out_min=action.get("out_min", 0),
                out_max=action.get("out_max", 255),
                curve=action.get("curve", "linear"),
            )
            actions["set_channel_values"]([(abs_channel, scaled)])
            return {"matched": True, "mapping_id": mapping_id, "action": action_type, "value": scaled}

        if action_type == "master" and msg["type"] == "cc":
            level = scale_value(msg["value"])
            actions["set_master"](level)
            return {"matched": True, "mapping_id": mapping_id, "action": action_type, "value": level}

        if action_type == "blackout" and msg["type"] == "note_on":
            actions["blackout"]()
            return {"matched": True, "mapping_id": mapping_id, "action": action_type}

        if action_type == "scene" and msg["type"] == "note_on":
            actions["activate_scene"](action.get("scene_id"))
            return {"matched": True, "mapping_id": mapping_id, "action": action_type}

        if action_type == "chase_toggle" and msg["type"] == "note_on":
            running = bool(chase_state.get(mapping_id, False))
            if running:
                actions["stop_chase"](action.get("chase_id"))
            else:
                actions["start_chase"](action.get("chase_id"))
            chase_state[mapping_id] = not running
            return {"matched": True, "mapping_id": mapping_id, "action": action_type}

        # Input matched but this (action type, message type) combo isn't
        # actionable (e.g. a note_off on a scene mapping) — ignore quietly.
        return {"matched": False, "mapping_id": mapping_id, "action": action_type}

    return {"matched": False, "mapping_id": None, "action": None}


def control_key(msg: dict) -> str:
    """Identity of the physical control behind a message: "cc:<channel>:<number>"
    for a fader or knob, "note:<channel>:<number>" for a button or pad (its
    note on and note off share a key)."""
    kind = "cc" if msg.get("type") == "cc" else "note"
    return f"{kind}:{msg.get('channel')}:{msg.get('number')}"


def is_system_port(name: str) -> bool:
    """Ports every Linux box has that aren't controllers: ALSA's "Midi
    Through" loopback and QLC+'s own sequencer client."""
    lowered = str(name).lower()
    return "midi through" in lowered or "__qlc__" in lowered or lowered.startswith("qlcplus")


class MidiMonitor:
    """What the controllers have sent lately, for the web UI.

    Keeps the last *recent* messages in order (each with a sequence number,
    so a poller asks only for what's new) and the latest message per
    physical control (for live value bars). Pure bookkeeping: callers decide
    which thread records into it.
    """

    def __init__(self, recent: int = 50, controls: int = 64):
        self._lock = threading.Lock()
        self._seq = 0
        self._recent = collections.deque(maxlen=recent)
        self._controls: collections.OrderedDict[str, dict] = collections.OrderedDict()
        self._max_controls = controls

    def record(self, port: str, msg: dict, now: float | None = None) -> dict:
        key = control_key(msg)
        with self._lock:
            self._seq += 1
            entry = {
                "seq": self._seq,
                "ts": time.time() if now is None else now,
                "port": port,
                "type": msg["type"],
                "channel": msg["channel"],
                "number": msg["number"],
                "value": msg["value"],
                "key": key,
            }
            self._recent.append(entry)
            self._controls[f"{port}|{key}"] = entry
            self._controls.move_to_end(f"{port}|{key}")
            while len(self._controls) > self._max_controls:
                self._controls.popitem(last=False)
        return entry

    def snapshot(self, after: int = 0) -> dict:
        """Messages newer than *after*, oldest first, and every control's
        latest message, most recently moved first."""
        with self._lock:
            return {
                "seq": self._seq,
                "messages": [dict(m) for m in self._recent if m["seq"] > after],
                "controls": [dict(c) for c in reversed(self._controls.values())],
            }


# ---------------------------------------------------------------------------
# MidiListener — hardware-gated background thread
# ---------------------------------------------------------------------------

def _release(midi_obj) -> None:
    """Free an rtmidi object's ALSA sequencer client right now.

    Dropping the last Python reference is not enough: python-rtmidi only
    frees the underlying client in `delete()`. Without it every probe
    leaks one `/dev/snd/seq` handle, and after 64 of them (the kernel's
    per-system limit for user clients) every later MidiIn() fails with
    "error creating ALSA sequencer client object" until the process
    restarts. Never raises.
    """
    for method in ("close_port", "delete"):
        try:
            getattr(midi_obj, method)()
        except Exception:
            pass


class MidiListener:
    """Owns rtmidi input ports: discovery, hot-plug reconnect, and callback
    wiring. Every incoming message is handed to `dispatch_fn(port_name,
    raw_message)` — the caller (app.py) owns mapping storage and the actual
    lighting-action callables, keeping this class free of Flask/app state.

    Usage::

        listener = MidiListener(dispatch_fn=my_handler)
        if listener.available:
            listener.start()
            ...
            listener.stop()
    """

    def __init__(self, dispatch_fn, poll_interval: float = 2.0):
        self._dispatch_fn = dispatch_fn
        self._poll_interval = poll_interval
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._ports: dict = {}  # port name -> open rtmidi.MidiIn instance
        self._last_sync_error: str | None = None

        self.available: bool = self._check_deps()

    @staticmethod
    def _check_deps() -> bool:
        try:
            import rtmidi  # noqa: F401
            return True
        except (ImportError, OSError):
            return False

    def list_device_names(self) -> list:
        """Return currently-visible MIDI input port names. Never raises —
        returns [] when rtmidi is unavailable or no devices are connected,
        so a headless Pi with nothing plugged in never crashes this call."""
        if not self.available:
            return []
        try:
            import rtmidi
            probe = rtmidi.MidiIn()
            try:
                return list(probe.get_ports())
            finally:
                _release(probe)
        except Exception as exc:
            print(f"[midi-listener] device query failed: {exc}")
            return []

    def connected_ports(self) -> list:
        """Names of the ports the listener has open right now. Cheap: no
        rtmidi probe (each probe costs an ALSA client), so it's safe to poll."""
        return sorted(self._ports)

    def start(self) -> bool:
        if not self.available:
            return False
        with self._lock:
            if self._running:
                return True
            self._running = True
            self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="midi-listener")
            self._thread.start()
        return True

    def stop(self) -> None:
        with self._lock:
            self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        for midi_in in self._ports.values():
            _release(midi_in)
        self._ports.clear()

    def _poll_loop(self) -> None:
        import rtmidi
        while self._running:
            try:
                self._sync_ports(rtmidi)
                self._note_sync_result(None)
            except Exception as exc:
                self._note_sync_result(str(exc))
            time.sleep(self._poll_interval)

    def _note_sync_result(self, error: str | None) -> None:
        """Log a port-sync failure once, not on every poll. The loop runs
        every couple of seconds, so a persistent failure would otherwise
        fill the journal and push everything else out of it."""
        if error == self._last_sync_error:
            return
        if error:
            print(f"[midi-listener] port sync error: {error} (repeats suppressed until this changes)")
        else:
            print("[midi-listener] port sync recovered")
        self._last_sync_error = error

    def _sync_ports(self, rtmidi) -> None:
        probe = rtmidi.MidiIn()
        try:
            current_names = list(probe.get_ports())
        finally:
            _release(probe)

        for idx, name in enumerate(current_names):
            if name in self._ports:
                continue
            midi_in = None
            try:
                midi_in = rtmidi.MidiIn()
                midi_in.open_port(idx)
                midi_in.ignore_types(sysex=True, timing=True, active_sense=True)
                midi_in.set_callback(self._make_callback(name))
                self._ports[name] = midi_in
                print(f"[midi-listener] connected: {name}")
            except Exception as exc:
                if midi_in is not None:
                    _release(midi_in)
                print(f"[midi-listener] failed to open {name}: {exc}")

        for name in list(self._ports.keys()):
            if name not in current_names:
                _release(self._ports[name])
                del self._ports[name]
                print(f"[midi-listener] disconnected: {name}")

    def _make_callback(self, port_name: str):
        def _callback(event, _data=None):
            message, _deltatime = event
            try:
                self._dispatch_fn(port_name, list(message))
            except Exception as exc:
                print(f"[midi-listener] dispatch error: {exc}")
        return _callback
