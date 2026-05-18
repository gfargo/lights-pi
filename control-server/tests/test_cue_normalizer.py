"""Tests for cue normalization (_normalize_cue) — the cue list builder
input format → canonical internal cue shape."""

from app import _normalize_cue


class TestNormalizeCue:
    """Cue: {at | at_ms, scene | chase | action, parameters?, groups?}."""

    def test_scene_cue_with_at(self):
        result = _normalize_cue({"at": "0:32", "scene": "Chorus"})
        assert result == {
            "at_ms": 32_000,
            "action": "activate_scene",
            "parameters": {"scene": "Chorus"},
            "groups": None,
        }

    def test_scene_cue_with_at_ms(self):
        result = _normalize_cue({"at_ms": 32500, "scene": "Chorus"})
        assert result["at_ms"] == 32500
        assert result["action"] == "activate_scene"

    def test_chase_cue(self):
        result = _normalize_cue({"at": "1:00", "chase": "Sunset"})
        assert result == {
            "at_ms": 60_000,
            "action": "start_chase",
            "parameters": {"chase": "Sunset"},
            "groups": None,
        }

    def test_action_cue_with_parameters(self):
        result = _normalize_cue({
            "at": "0:22",
            "action": "strobe",
            "parameters": {"rate": 8},
        })
        assert result == {
            "at_ms": 22_000,
            "action": "strobe",
            "parameters": {"rate": 8},
            "groups": None,
        }

    def test_action_cue_without_parameters(self):
        """Some actions (blackout) don't need parameters."""
        result = _normalize_cue({"at": "0:30", "action": "blackout"})
        assert result == {
            "at_ms": 30_000,
            "action": "blackout",
            "parameters": {},
            "groups": None,
        }

    def test_cue_with_groups(self):
        result = _normalize_cue({
            "at": "0:08",
            "scene": "Spotlight",
            "groups": ["key-lights"],
        })
        assert result["groups"] == ["key-lights"]

    def test_missing_timestamp_errors(self):
        result = _normalize_cue({"scene": "X"})
        assert "error" in result

    def test_invalid_timestamp_errors(self):
        result = _normalize_cue({"at": "not-a-time", "scene": "X"})
        assert "error" in result

    def test_no_action_field_errors(self):
        """Cue must have one of scene / chase / action."""
        result = _normalize_cue({"at": "0:32"})
        assert "error" in result

    def test_non_dict_errors(self):
        for bad in [None, "string", 42, []]:
            result = _normalize_cue(bad)
            assert "error" in result, f"expected error for {bad!r}"

    def test_scene_takes_precedence_over_chase_over_action(self):
        """If multiple are provided, the order is: scene > chase > action.
        Matches the implementation; this test documents that."""
        result = _normalize_cue({
            "at": "0:00",
            "scene": "S",
            "chase": "C",
            "action": "blackout",
        })
        assert result["action"] == "activate_scene"
        assert result["parameters"]["scene"] == "S"

    def test_zero_timestamp_accepted(self):
        """at: 0 is a valid cue ('fire at GO')."""
        result = _normalize_cue({"at_ms": 0, "scene": "Start"})
        assert result["at_ms"] == 0

    def test_human_readable_fractional_seconds(self):
        result = _normalize_cue({"at": "0:15.500", "scene": "X"})
        assert result["at_ms"] == 15_500
