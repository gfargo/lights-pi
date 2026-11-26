"""MIDI tab support: master and blackout actions, the message monitor, the
hand-off from rtmidi's thread to the hub, and fader coalescing."""
import importlib
from pathlib import Path

import midi_engine as me
import pytest

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample.qxw"


def _cc(number, value, channel=0):
    return {"type": "cc", "channel": channel, "number": number, "value": value}


def _note(number, velocity=100, channel=0):
    return {"type": "note_on" if velocity else "note_off", "channel": channel, "number": number, "value": velocity}


# ---------------------------------------------------------------------------
# Engine: new actions and input/action pairing
# ---------------------------------------------------------------------------

class TestNewActions:
    def _actions(self, calls):
        return {
            "set_channel_values": lambda u: calls.append(("set", u)),
            "resolve_channel": lambda f, o: 1,
            "set_master": lambda level: calls.append(("master", level)),
            "blackout": lambda: calls.append(("blackout",)),
            "activate_scene": lambda s: calls.append(("scene", s)),
            "start_chase": lambda c: calls.append(("start", c)),
            "stop_chase": lambda c: calls.append(("stop", c)),
        }

    def test_master_scales_a_fader_to_0_255(self):
        mapping, err = me.build_mapping({"input": {"type": "cc", "number": 7}, "action": {"type": "master"}})
        assert err is None and mapping["action"] == {"type": "master"}
        calls = []
        result = me.dispatch_midi_message(_cc(7, 127), [mapping], self._actions(calls))
        assert result["matched"] and result["value"] == 255
        me.dispatch_midi_message(_cc(7, 0), [mapping], self._actions(calls))
        assert calls == [("master", 255), ("master", 0)]

    def test_blackout_fires_on_press_not_release(self):
        mapping, err = me.build_mapping({"input": {"type": "note", "number": 40}, "action": {"type": "blackout"}})
        assert err is None
        calls = []
        me.dispatch_midi_message(_note(40), [mapping], self._actions(calls))
        me.dispatch_midi_message(_note(40, 0), [mapping], self._actions(calls))
        assert calls == [("blackout",)]

    @pytest.mark.parametrize("input_type,action", [
        ("note", {"type": "master"}),
        ("note", {"type": "channel", "fixture_id": 0}),
        ("cc", {"type": "blackout"}),
        ("cc", {"type": "scene", "scene_id": "1"}),
        ("cc", {"type": "chase_toggle", "chase_id": "2"}),
    ])
    def test_action_must_suit_the_control(self, input_type, action):
        mapping, err = me.build_mapping({"input": {"type": input_type, "number": 1}, "action": action})
        assert mapping is None
        assert ("fader" if input_type == "note" else "button") in err


class TestHelpers:
    def test_control_key_joins_note_on_and_off(self):
        assert me.control_key(_cc(21, 5, channel=2)) == "cc:2:21"
        assert me.control_key(_note(36)) == me.control_key(_note(36, 0)) == "note:0:36"

    @pytest.mark.parametrize("name,system", [
        ("Midi Through:Midi Through Port-0 14:0", True),
        ("qlcplus:__QLC__ 128:0", True),
        ("nanoKONTROL2:nanoKONTROL2 MIDI 1 20:0", False),
        ("MIDI Mix", False),
    ])
    def test_system_ports(self, name, system):
        assert me.is_system_port(name) is system


class TestMonitor:
    def test_recent_messages_after_a_sequence_number(self):
        mon = me.MidiMonitor(recent=3)
        for i in range(5):
            mon.record("Pad", _cc(1, i), now=100 + i)
        snap = mon.snapshot()
        assert snap["seq"] == 5
        assert [m["value"] for m in snap["messages"]] == [2, 3, 4]   # oldest dropped
        assert [m["value"] for m in mon.snapshot(after=4)["messages"]] == [4]
        assert mon.snapshot(after=5)["messages"] == []

    def test_latest_value_per_control_most_recent_first(self):
        mon = me.MidiMonitor()
        mon.record("Pad", _cc(1, 10))
        mon.record("Pad", _cc(2, 20))
        mon.record("Pad", _cc(1, 99))
        mon.record("Other", _cc(1, 50))   # same CC, different device: its own control
        controls = mon.snapshot()["controls"]
        assert [(c["port"], c["number"], c["value"]) for c in controls] == [
            ("Other", 1, 50), ("Pad", 1, 99), ("Pad", 2, 20),
        ]

    def test_control_list_is_bounded(self):
        mon = me.MidiMonitor(controls=2)
        for n in range(4):
            mon.record("Pad", _cc(n, 1))
        assert [c["number"] for c in mon.snapshot()["controls"]] == [3, 2]


# ---------------------------------------------------------------------------
# App: hub hand-off, coalescing, monitor route
# ---------------------------------------------------------------------------

@pytest.fixture
def app_module(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MOCK_DMX", "1")
    monkeypatch.setenv("QLC_WORKSPACE", str(_FIXTURE))
    import app as _app_module

    importlib.reload(_app_module)
    _app_module.app.config["TESTING"] = True
    yield _app_module
    for var in ("HOME", "MOCK_DMX", "QLC_WORKSPACE"):
        monkeypatch.delenv(var, raising=False)
    importlib.reload(_app_module)


@pytest.fixture
def hub(app_module, monkeypatch):
    """Capture _call_on_hub hand-offs instead of running them, the way a
    native rtmidi thread would see it under eventlet."""
    queued = []
    monkeypatch.setattr(app_module, "_call_on_hub", lambda fn, *a, **k: queued.append((fn, a, k)))
    return queued


def _run(queued):
    while queued:
        fn, a, k = queued.pop(0)
        fn(*a, **k)


class TestHubHandOff:
    def test_callback_only_queues_work(self, app_module, hub, monkeypatch):
        handled = []
        monkeypatch.setattr(app_module, "_handle_midi_message", lambda port, msg: handled.append(msg))
        app_module._on_midi_message("Pad", [0x90, 36, 100])
        assert handled == [] and len(hub) == 1
        _run(hub)
        assert handled == [_note(36)]

    def test_a_moving_fader_is_coalesced_to_its_latest_value(self, app_module, hub, monkeypatch):
        handled = []
        monkeypatch.setattr(app_module, "_handle_midi_message", lambda port, msg: handled.append(msg["value"]))
        for value in range(0, 128, 8):
            app_module._on_midi_message("Pad", [0xB0, 21, value])
        assert len(hub) == 1                     # one queued hub call, not sixteen
        _run(hub)
        assert handled == [120]                  # only the final position is applied

        app_module._on_midi_message("Pad", [0xB0, 21, 64])
        assert len(hub) == 1                     # after the hub took it, the next move queues again
        _run(hub)
        assert handled == [120, 64]

    def test_separate_faders_are_not_merged(self, app_module, hub, monkeypatch):
        handled = []
        monkeypatch.setattr(app_module, "_handle_midi_message", lambda port, msg: handled.append(msg["number"]))
        app_module._on_midi_message("Pad", [0xB0, 21, 1])
        app_module._on_midi_message("Pad", [0xB0, 22, 1])
        _run(hub)
        assert sorted(handled) == [21, 22]

    def test_garbage_is_ignored(self, app_module, hub):
        app_module._on_midi_message("Pad", [0xF8])        # clock
        app_module._on_midi_message("Pad", [])
        assert hub == []


class TestMonitorRoute:
    def test_reports_devices_messages_and_controls(self, app_module, monkeypatch):
        monkeypatch.setattr(app_module._midi_listener, "connected_ports",
                            lambda: ["Midi Through:Midi Through Port-0 14:0", "nanoKONTROL2"])
        app_module._handle_midi_message("nanoKONTROL2", _cc(21, 64))
        app_module._handle_midi_message("nanoKONTROL2", _note(36))
        with app_module.app.test_client() as client:
            body = client.get("/api/midi/monitor").get_json()
            later = client.get(f"/api/midi/monitor?after={body['seq']}").get_json()
            bad = client.get("/api/midi/monitor?after=nope")
        assert body["devices"] == [
            {"name": "Midi Through:Midi Through Port-0 14:0", "system": True},
            {"name": "nanoKONTROL2", "system": False},
        ]
        assert [m["key"] for m in body["messages"]] == ["cc:0:21", "note:0:36"]
        assert body["controls"][0]["key"] == "note:0:36"
        assert later["messages"] == [] and later["seq"] == body["seq"]
        assert bad.status_code == 200

    def test_master_and_blackout_mappings_drive_the_rig(self, app_module, monkeypatch):
        calls = []
        monkeypatch.setattr(app_module, "apply_brightness_live", lambda v, target_groups=None: calls.append(("master", v)))
        monkeypatch.setattr(app_module, "_do_blackout", lambda groups=None: calls.append(("blackout", groups)))
        with app_module.app.test_client() as client:
            assert client.post("/api/midi/mappings", json={
                "input": {"type": "cc", "number": 7}, "action": {"type": "master"}}).status_code == 200
            assert client.post("/api/midi/mappings", json={
                "input": {"type": "note", "number": 40}, "action": {"type": "blackout"}}).status_code == 200
            assert client.post("/api/midi/mappings", json={
                "input": {"type": "note", "number": 41}, "action": {"type": "master"}}).status_code == 400
        app_module._handle_midi_message("Pad", _cc(7, 127))
        app_module._handle_midi_message("Pad", _note(40))
        assert calls == [("master", 255), ("blackout", None)]
