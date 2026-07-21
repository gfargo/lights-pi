"""MIDI engine: python-rtmidi listener + pure CC/note dispatch logic.

Designed to import cleanly even when python-rtmidi is absent — all
hardware-gated code lives inside MidiListener and is guarded by the
`available` flag, mirroring audio_engine.py. The parsing/scaling/dispatch
helpers are pure and fully unit-testable with constructed fake MIDI
messages — no device required.
"""
import threading
import time
import uuid

# ---------------------------------------------------------------------------
# Pure helpers — no hardware, fully unit-testable
# ---------------------------------------------------------------------------

VALID_INPUT_TYPES = ("cc", "note")
VALID_ACTION_TYPES = ("channel", "scene", "chase_toggle")


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


# ---------------------------------------------------------------------------
# MidiListener — hardware-gated background thread
# ---------------------------------------------------------------------------

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
            names = list(probe.get_ports())
            del probe
            return names
        except Exception as exc:
            print(f"[midi-listener] device query failed: {exc}")
            return []

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
            try:
                midi_in.close_port()
            except Exception:
                pass
        self._ports.clear()

    def _poll_loop(self) -> None:
        import rtmidi
        while self._running:
            try:
                self._sync_ports(rtmidi)
            except Exception as exc:
                print(f"[midi-listener] port sync error: {exc}")
            time.sleep(self._poll_interval)

    def _sync_ports(self, rtmidi) -> None:
        probe = rtmidi.MidiIn()
        current_names = list(probe.get_ports())
        del probe

        for idx, name in enumerate(current_names):
            if name in self._ports:
                continue
            try:
                midi_in = rtmidi.MidiIn()
                midi_in.open_port(idx)
                midi_in.ignore_types(sysex=True, timing=True, active_sense=True)
                midi_in.set_callback(self._make_callback(name))
                self._ports[name] = midi_in
                print(f"[midi-listener] connected: {name}")
            except Exception as exc:
                print(f"[midi-listener] failed to open {name}: {exc}")

        for name in list(self._ports.keys()):
            if name not in current_names:
                try:
                    self._ports[name].close_port()
                except Exception:
                    pass
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
