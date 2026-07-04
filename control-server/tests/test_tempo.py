"""Tests for tap-tempo helpers: BPM math, interval averaging, tempo_source normalization,
chase step extraction, and live-retime state updates."""
import xml.etree.ElementTree as ET

import app
import pytest
from app import (
    _bpm_to_step_ms,
    _chase_step_scene_ids,
    _normalize_tempo_source,
    _tap_intervals_to_bpm,
)


class TestBpmToStepMs:
    @pytest.mark.parametrize("bpm,expected", [
        (120, 500),   # canonical: 120 BPM → 500ms
        (90,  667),   # round(60000/90) = round(666.7) = 667
        (60,  1000),
        (240, 250),
        (40,  1500),
        (100, 600),
        (80,  750),
    ])
    def test_known_bpm(self, bpm, expected):
        assert _bpm_to_step_ms(bpm) == expected

    def test_returns_int(self):
        result = _bpm_to_step_ms(120)
        assert isinstance(result, int)

    def test_float_bpm(self):
        # 90.0 BPM should work same as 90
        assert _bpm_to_step_ms(90.0) == 667


class TestTapIntervalsToBpm:
    def test_120_bpm_from_four_taps(self):
        # 500ms intervals → 120 BPM
        bpm = _tap_intervals_to_bpm([500, 500, 500, 500])
        assert bpm is not None
        assert abs(bpm - 120.0) < 0.01

    def test_90_bpm_from_four_taps(self):
        # ~667ms intervals → ~89.96 BPM (60000/667)
        bpm = _tap_intervals_to_bpm([667, 667, 667, 667])
        assert bpm is not None
        # round-trip: bpm_to_step_ms(_tap_intervals_to_bpm([667,…])) should ≈ 667
        assert abs(bpm - 60000 / 667) < 0.01

    def test_uses_only_last_4_intervals(self):
        # First interval at 2000ms (30 BPM, out of range), last 4 at 500ms (120 BPM)
        bpm = _tap_intervals_to_bpm([2000, 500, 500, 500, 500])
        assert bpm is not None
        assert abs(bpm - 120.0) < 0.01

    def test_rolling_average_of_two(self):
        # Average of [400, 600] = 500ms → 120 BPM
        bpm = _tap_intervals_to_bpm([400, 600])
        assert bpm is not None
        assert abs(bpm - 120.0) < 0.01

    def test_empty_returns_none(self):
        assert _tap_intervals_to_bpm([]) is None

    def test_rejects_below_40_bpm(self):
        # 2000ms → 30 BPM → rejected
        assert _tap_intervals_to_bpm([2000]) is None

    def test_rejects_above_240_bpm(self):
        # 100ms → 600 BPM → rejected
        assert _tap_intervals_to_bpm([100]) is None

    def test_boundary_exactly_40_bpm_accepted(self):
        # 1500ms → exactly 40 BPM
        bpm = _tap_intervals_to_bpm([1500])
        assert bpm is not None
        assert abs(bpm - 40.0) < 0.01

    def test_boundary_exactly_240_bpm_accepted(self):
        # 250ms → exactly 240 BPM
        bpm = _tap_intervals_to_bpm([250])
        assert bpm is not None
        assert abs(bpm - 240.0) < 0.01

    def test_returns_float(self):
        result = _tap_intervals_to_bpm([500])
        assert isinstance(result, float)


class TestNormalizeTempoSource:
    @pytest.mark.parametrize("inp,expected", [
        ("fixed",  "fixed"),
        ("tap",    "tap"),
        ("audio",  "audio"),
        ("Fixed",  "fixed"),
        ("TAP",    "tap"),
        ("AUDIO",  "audio"),
        (" tap ",  "tap"),
        ("Tap",    "tap"),
    ])
    def test_valid_inputs(self, inp, expected):
        assert _normalize_tempo_source(inp) == expected

    @pytest.mark.parametrize("inp", [None, "", "unknown", "beat", "manual"])
    def test_invalid_defaults_to_fixed(self, inp):
        assert _normalize_tempo_source(inp) == "fixed"

    @pytest.mark.parametrize("inp", [True, False, 0, 1, 42])
    def test_non_string_defaults_to_fixed(self, inp):
        assert _normalize_tempo_source(inp) == "fixed"

    def test_custom_default(self):
        assert _normalize_tempo_source(None, default="tap") == "tap"
        assert _normalize_tempo_source("garbage", default="tap") == "tap"


def _make_chase_element(steps):
    """Build a minimal <Function Type="Chaser"> element with the given step list.

    steps: list of (number, scene_id) tuples.
    """
    root = ET.fromstring('<Function Type="Chaser" ID="10" Name="Test"/>')
    for num, sid in steps:
        step = ET.SubElement(root, "Step")
        step.set("Number", str(num))
        step.set("FadeIn", "0")
        step.set("Hold", "500")
        step.set("FadeOut", "0")
        step.set("Values", str(sid))
    return root


class TestChaseStepSceneIds:
    def test_ordered_by_step_number(self):
        # Steps deliberately out of insertion order — must come back sorted by Number
        elem = _make_chase_element([(2, 30), (0, 10), (1, 20)])
        assert _chase_step_scene_ids(elem) == [10, 20, 30]

    def test_single_step(self):
        elem = _make_chase_element([(0, 42)])
        assert _chase_step_scene_ids(elem) == [42]

    def test_empty_chase(self):
        elem = ET.fromstring('<Function Type="Chaser" ID="1" Name="Empty"/>')
        assert _chase_step_scene_ids(elem) == []

    def test_non_numeric_values_ignored(self):
        root = ET.fromstring('<Function Type="Chaser" ID="1" Name="X"/>')
        good = ET.SubElement(root, "Step")
        good.set("Number", "0")
        good.set("Values", "5")
        bad = ET.SubElement(root, "Step")
        bad.set("Number", "1")
        bad.set("Values", "notanumber")
        assert _chase_step_scene_ids(root) == [5]

    def test_text_content_fallback(self):
        # Older QLC+ versions stored the scene ID as step text, not Values attr
        root = ET.fromstring('<Function Type="Chaser" ID="1" Name="X"/>')
        step = ET.SubElement(root, "Step")
        step.set("Number", "0")
        step.text = "99"
        assert _chase_step_scene_ids(root) == [99]


class TestUpdateTapRunnerBpm:
    # Access _tap_runners / _update_tap_runner_bpm via the `app` module (not
    # bound names imported at collection time): tests in test_mock_dmx.py use
    # importlib.reload(app) to exercise MOCK_DMX, which rebinds app's module
    # globals to fresh objects. A name imported earlier via `from app import
    # _tap_runners` would keep pointing at the pre-reload dict, going out of
    # sync with the reloaded `_update_tap_runner_bpm`'s view of the state.
    def setup_method(self):
        app._tap_runners.clear()

    def teardown_method(self):
        app._tap_runners.clear()

    def test_returns_false_when_no_runner(self):
        assert app._update_tap_runner_bpm("42", 500.0) is False

    def test_returns_true_and_updates_when_runner_exists(self):
        app._tap_runners["7"] = {"step_ms": 500.0, "running": True}
        result = app._update_tap_runner_bpm("7", 667.0)
        assert result is True
        assert app._tap_runners["7"]["step_ms"] == 667.0

    def test_bpm_change_reflected_live(self):
        # Simulate what set_chase_tempo does: write new BPM, update live runner
        app._tap_runners["5"] = {"step_ms": 500.0, "running": True}
        new_step_ms = _bpm_to_step_ms(90)  # 667 ms
        app._update_tap_runner_bpm("5", new_step_ms)
        assert app._tap_runners["5"]["step_ms"] == 667

    def test_coerces_to_float(self):
        app._tap_runners["3"] = {"step_ms": 500.0, "running": True}
        app._update_tap_runner_bpm("3", 250)  # int input
        assert isinstance(app._tap_runners["3"]["step_ms"], float)

    def test_string_chase_id_matches(self):
        app._tap_runners["9"] = {"step_ms": 500.0, "running": True}
        assert app._update_tap_runner_bpm("9", 400.0) is True

    def test_no_runner_for_different_id(self):
        app._tap_runners["1"] = {"step_ms": 500.0, "running": True}
        assert app._update_tap_runner_bpm("2", 400.0) is False
        assert app._tap_runners["1"]["step_ms"] == 500.0
