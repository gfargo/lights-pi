"""Tests for midi_engine.py — MIDI message parsing, value scaling, mapping
validation, and dispatch. All pure: dispatch is exercised with constructed
fake MIDI messages and stub action callables, no rtmidi / hardware / QLC+
WebSocket involved (per CLAUDE.md: pure-helper tests preferred over mocking
the WebSocket)."""
import pytest
from midi_engine import (
    MidiListener,
    build_mapping,
    dispatch_midi_message,
    parse_midi_message,
    scale_value,
)

# ---------------------------------------------------------------------------
# parse_midi_message
# ---------------------------------------------------------------------------


class TestParseMidiMessage:
    def test_control_change(self):
        # CC 21 = 64 on MIDI channel 0 → status 0xB0
        assert parse_midi_message([0xB0, 21, 64]) == {
            "type": "cc", "channel": 0, "number": 21, "value": 64,
        }

    def test_control_change_channel_5(self):
        assert parse_midi_message([0xB5, 1, 127]) == {
            "type": "cc", "channel": 5, "number": 1, "value": 127,
        }

    def test_note_on(self):
        assert parse_midi_message([0x90, 60, 100]) == {
            "type": "note_on", "channel": 0, "number": 60, "value": 100,
        }

    def test_note_on_velocity_zero_is_note_off(self):
        """Per MIDI convention, Note On with velocity 0 means Note Off."""
        assert parse_midi_message([0x90, 60, 0]) == {
            "type": "note_off", "channel": 0, "number": 60, "value": 0,
        }

    def test_note_off(self):
        assert parse_midi_message([0x80, 60, 64]) == {
            "type": "note_off", "channel": 0, "number": 60, "value": 64,
        }

    @pytest.mark.parametrize("data", [
        [],
        None,
        [0xE0, 64, 64],   # pitch bend — unsupported, not malformed
        [0xC0, 5],        # program change — unsupported
        [0xF0, 1, 2],     # sysex — unsupported
    ])
    def test_unsupported_or_empty_returns_none(self, data):
        assert parse_midi_message(data) is None

    @pytest.mark.parametrize("data", [
        [0xB0, 200, 64],    # data1 out of 0-127 range
        [0xB0, 21, 999],    # data2 out of 0-127 range
        [0xB0, -1, 64],     # negative data1
        [0xB0, "x", 64],    # non-numeric
    ])
    def test_malformed_returns_none(self, data):
        assert parse_midi_message(data) is None


# ---------------------------------------------------------------------------
# scale_value
# ---------------------------------------------------------------------------


class TestScaleValue:
    def test_full_range_linear(self):
        assert scale_value(0) == 0
        assert scale_value(127) == 255

    def test_midpoint(self):
        assert scale_value(64, out_min=0, out_max=255) == pytest.approx(128, abs=1)

    def test_custom_output_range(self):
        assert scale_value(0, out_min=10, out_max=20) == 10
        assert scale_value(127, out_min=10, out_max=20) == 20

    def test_clamps_out_of_range_input(self):
        assert scale_value(999) == 255
        assert scale_value(-5) == 0

    def test_non_numeric_input_falls_back_to_in_min(self):
        assert scale_value("garbage") == 0

    def test_unknown_curve_falls_back_to_linear(self):
        assert scale_value(127, curve="exponential") == 255


# ---------------------------------------------------------------------------
# build_mapping
# ---------------------------------------------------------------------------


class TestBuildMapping:
    def test_valid_channel_mapping(self):
        mapping, error = build_mapping({
            "name": "Fixture 0 master",
            "input": {"type": "cc", "number": 21},
            "action": {"type": "channel", "fixture_id": 0, "channel_offset": 0},
        })
        assert error is None
        assert mapping["name"] == "Fixture 0 master"
        assert mapping["input"] == {"type": "cc", "channel": None, "number": 21}
        assert mapping["action"] == {
            "type": "channel", "fixture_id": 0, "channel_offset": 0,
            "out_min": 0, "out_max": 255, "curve": "linear",
        }
        assert mapping["id"]

    def test_stable_id_when_provided(self):
        mapping, error = build_mapping({
            "input": {"type": "cc", "number": 1},
            "action": {"type": "channel", "fixture_id": 0},
        }, mapping_id="fixed-id")
        assert error is None
        assert mapping["id"] == "fixed-id"

    def test_valid_scene_mapping(self):
        mapping, error = build_mapping({
            "input": {"type": "note", "number": 60},
            "action": {"type": "scene", "scene_id": "3"},
        })
        assert error is None
        assert mapping["action"] == {"type": "scene", "scene_id": "3"}

    def test_valid_chase_toggle_mapping(self):
        mapping, error = build_mapping({
            "input": {"type": "note", "number": 61, "channel": 2},
            "action": {"type": "chase_toggle", "chase_id": "5"},
        })
        assert error is None
        assert mapping["input"]["channel"] == 2
        assert mapping["action"] == {"type": "chase_toggle", "chase_id": "5"}

    def test_missing_input_type(self):
        _, error = build_mapping({"input": {"number": 1}, "action": {"type": "scene", "scene_id": "1"}})
        assert "input.type" in error

    def test_non_dict_input_is_rejected_not_crashed(self):
        _, error = build_mapping({"input": "cc", "action": {"type": "scene", "scene_id": "1"}})
        assert "input.type" in error

    def test_non_dict_action_is_rejected_not_crashed(self):
        _, error = build_mapping({"input": {"type": "cc", "number": 1}, "action": "channel"})
        assert "action.type" in error

    def test_non_dict_payload_is_rejected_not_crashed(self):
        _, error = build_mapping("not a dict")
        assert error is not None

    def test_number_out_of_range(self):
        _, error = build_mapping({
            "input": {"type": "cc", "number": 200},
            "action": {"type": "channel", "fixture_id": 0},
        })
        assert "input.number" in error

    def test_channel_out_of_range(self):
        _, error = build_mapping({
            "input": {"type": "cc", "number": 1, "channel": 16},
            "action": {"type": "channel", "fixture_id": 0},
        })
        assert "input.channel" in error

    def test_missing_action_type(self):
        _, error = build_mapping({"input": {"type": "cc", "number": 1}, "action": {}})
        assert "action.type" in error

    def test_channel_mapping_requires_fixture_id(self):
        _, error = build_mapping({
            "input": {"type": "cc", "number": 1},
            "action": {"type": "channel"},
        })
        assert "fixture_id" in error

    def test_channel_mapping_rejects_inverted_output_range(self):
        _, error = build_mapping({
            "input": {"type": "cc", "number": 1},
            "action": {"type": "channel", "fixture_id": 0, "out_min": 200, "out_max": 50},
        })
        assert "out_min" in error

    def test_scene_mapping_requires_scene_id(self):
        _, error = build_mapping({
            "input": {"type": "note", "number": 1},
            "action": {"type": "scene"},
        })
        assert "scene_id" in error

    def test_chase_toggle_requires_chase_id(self):
        _, error = build_mapping({
            "input": {"type": "note", "number": 1},
            "action": {"type": "chase_toggle"},
        })
        assert "chase_id" in error


# ---------------------------------------------------------------------------
# dispatch_midi_message
# ---------------------------------------------------------------------------


class _RecordingActions(dict):
    """Stub actions dict that records every call for assertions."""

    def __init__(self, resolve_channel_result=1):
        self.calls = []
        super().__init__({
            "set_channel_values": lambda updates: self.calls.append(("set_channel_values", updates)) or True,
            "resolve_channel": lambda fid, off: (
                self.calls.append(("resolve_channel", fid, off)) or resolve_channel_result
            ),
            "activate_scene": lambda scene_id: self.calls.append(("activate_scene", scene_id)),
            "start_chase": lambda chase_id: self.calls.append(("start_chase", chase_id)),
            "stop_chase": lambda chase_id: self.calls.append(("stop_chase", chase_id)),
        })


class TestDispatchMidiMessage:
    def test_cc_maps_to_channel_value_with_curve(self):
        """Acceptance example: CC 21 mapped to fixture 0 master → set_channel_values
        called with the right scaled value."""
        actions = _RecordingActions(resolve_channel_result=1)
        mappings = [{
            "id": "m1",
            "input": {"type": "cc", "channel": None, "number": 21},
            "action": {"type": "channel", "fixture_id": 0, "channel_offset": 0,
                       "out_min": 0, "out_max": 255, "curve": "linear"},
        }]
        msg = parse_midi_message([0xB0, 21, 127])  # CC 21 = full value

        result = dispatch_midi_message(msg, mappings, actions)

        assert result == {"matched": True, "mapping_id": "m1", "action": "channel", "value": 255}
        assert ("resolve_channel", 0, 0) in actions.calls
        assert ("set_channel_values", [(1, 255)]) in actions.calls

    def test_cc_applies_custom_output_range(self):
        actions = _RecordingActions(resolve_channel_result=7)
        mappings = [{
            "id": "m1",
            "input": {"type": "cc", "channel": None, "number": 21},
            "action": {"type": "channel", "fixture_id": 0, "channel_offset": 0,
                       "out_min": 0, "out_max": 100, "curve": "linear"},
        }]
        msg = parse_midi_message([0xB0, 21, 0])

        result = dispatch_midi_message(msg, mappings, actions)

        assert result["value"] == 0
        assert ("set_channel_values", [(7, 0)]) in actions.calls

    def test_note_on_activates_scene(self):
        actions = _RecordingActions()
        mappings = [{
            "id": "m2",
            "input": {"type": "note", "channel": None, "number": 60},
            "action": {"type": "scene", "scene_id": "sunset"},
        }]
        msg = parse_midi_message([0x90, 60, 100])

        result = dispatch_midi_message(msg, mappings, actions)

        assert result == {"matched": True, "mapping_id": "m2", "action": "scene"}
        assert ("activate_scene", "sunset") in actions.calls

    def test_note_off_does_not_activate_scene(self):
        actions = _RecordingActions()
        mappings = [{
            "id": "m2",
            "input": {"type": "note", "channel": None, "number": 60},
            "action": {"type": "scene", "scene_id": "sunset"},
        }]
        msg = parse_midi_message([0x80, 60, 0])  # explicit note off

        result = dispatch_midi_message(msg, mappings, actions)

        assert result["matched"] is False
        assert not actions.calls

    def test_note_on_toggles_chase_start_then_stop(self):
        actions = _RecordingActions()
        mappings = [{
            "id": "m3",
            "input": {"type": "note", "channel": None, "number": 44},
            "action": {"type": "chase_toggle", "chase_id": "party"},
        }]
        state = {}
        msg = parse_midi_message([0x90, 44, 127])

        first = dispatch_midi_message(msg, mappings, actions, chase_state=state)
        second = dispatch_midi_message(msg, mappings, actions, chase_state=state)

        assert first == {"matched": True, "mapping_id": "m3", "action": "chase_toggle"}
        assert second == {"matched": True, "mapping_id": "m3", "action": "chase_toggle"}
        assert ("start_chase", "party") in actions.calls
        assert ("stop_chase", "party") in actions.calls
        # Started before it was stopped.
        assert actions.calls.index(("start_chase", "party")) < actions.calls.index(("stop_chase", "party"))

    def test_toggle_state_is_per_mapping(self):
        actions = _RecordingActions()
        mappings = [{
            "id": "m3",
            "input": {"type": "note", "channel": None, "number": 44},
            "action": {"type": "chase_toggle", "chase_id": "party"},
        }]
        state = {"m3": True}  # already running
        msg = parse_midi_message([0x90, 44, 127])

        result = dispatch_midi_message(msg, mappings, actions, chase_state=state)

        assert result["matched"] is True
        assert ("stop_chase", "party") in actions.calls
        assert not any(c[0] == "start_chase" for c in actions.calls)
        assert state["m3"] is False

    def test_no_matching_mapping_is_ignored(self):
        actions = _RecordingActions()
        msg = parse_midi_message([0xB0, 99, 64])  # CC 99 has no mapping

        result = dispatch_midi_message(msg, [], actions)

        assert result == {"matched": False, "mapping_id": None, "action": None}
        assert not actions.calls

    def test_mismatched_midi_channel_is_ignored(self):
        actions = _RecordingActions()
        mappings = [{
            "id": "m1",
            "input": {"type": "cc", "channel": 3, "number": 21},
            "action": {"type": "channel", "fixture_id": 0, "channel_offset": 0,
                       "out_min": 0, "out_max": 255, "curve": "linear"},
        }]
        msg = parse_midi_message([0xB0, 21, 64])  # MIDI channel 0, mapping wants 3

        result = dispatch_midi_message(msg, mappings, actions)

        assert result["matched"] is False
        assert not actions.calls

    def test_unresolvable_fixture_is_ignored_not_crashed(self):
        """A mapping pointing at a fixture that no longer exists in the
        workspace must not raise — the listener thread must survive it."""
        actions = _RecordingActions(resolve_channel_result=None)
        mappings = [{
            "id": "m1",
            "input": {"type": "cc", "channel": None, "number": 21},
            "action": {"type": "channel", "fixture_id": 999, "channel_offset": 0,
                       "out_min": 0, "out_max": 255, "curve": "linear"},
        }]
        msg = parse_midi_message([0xB0, 21, 64])

        result = dispatch_midi_message(msg, mappings, actions)

        assert result["matched"] is False
        assert not any(c[0] == "set_channel_values" for c in actions.calls)

    def test_none_message_is_ignored(self):
        """Malformed raw MIDI bytes parse to None upstream; dispatch must
        handle that gracefully rather than crash the listener thread."""
        actions = _RecordingActions()
        result = dispatch_midi_message(None, [{"id": "m1"}], actions)
        assert result == {"matched": False, "mapping_id": None, "action": None}
        assert not actions.calls

    def test_malformed_raw_message_never_reaches_a_mapping(self):
        actions = _RecordingActions()
        mappings = [{
            "id": "m1",
            "input": {"type": "cc", "channel": None, "number": 21},
            "action": {"type": "channel", "fixture_id": 0, "channel_offset": 0,
                       "out_min": 0, "out_max": 255, "curve": "linear"},
        }]
        malformed = parse_midi_message([0xB0, 21, 999])  # out-of-range value byte

        result = dispatch_midi_message(malformed, mappings, actions)

        assert result == {"matched": False, "mapping_id": None, "action": None}
        assert not actions.calls


# ---------------------------------------------------------------------------
# MidiListener — availability gate only (no hardware in CI)
# ---------------------------------------------------------------------------


class TestMidiListenerNoHardware:
    def test_list_device_names_empty_without_rtmidi(self, monkeypatch):
        listener = MidiListener(dispatch_fn=lambda *a: None)
        monkeypatch.setattr(listener, "available", False)
        assert listener.list_device_names() == []

    def test_start_is_noop_without_rtmidi(self, monkeypatch):
        listener = MidiListener(dispatch_fn=lambda *a: None)
        monkeypatch.setattr(listener, "available", False)
        assert listener.start() is False

    def test_availability_reflects_import_success(self):
        # python-rtmidi isn't installed in this dev/CI environment (optional
        # dependency, same story as aubio for the audio engine) — pin the
        # expectation so a future install doesn't silently change behavior
        # without anyone noticing this test needs updating too.
        listener = MidiListener(dispatch_fn=lambda *a: None)
        try:
            import rtmidi  # noqa: F401
            expected = True
        except ImportError:
            expected = False
        assert listener.available is expected
