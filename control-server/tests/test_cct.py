"""Tests for the Kelvin → RGB approximation and the WWA mix function.

Locks in the Tanner Helland algorithm output at canonical anchor points so
regressions to the math show up immediately. Tests aren't checking absolute
correctness vs reality (the algorithm is itself an approximation) but
"the values we shipped to users in v2.5 don't shift unintentionally."
"""
import pytest
from app import _cct_to_rgb, _wwa_mix


class TestCctToRgb:
    """_cct_to_rgb — Tanner Helland's algorithm."""

    @pytest.mark.parametrize("kelvin,expected_rgb", [
        # Canonical anchors — locked from v2.5 sanity check
        (1800,  (255, 126, 0)),       # candle / firelight
        (2700,  (255, 167, 87)),      # incandescent
        (3200,  (255, 184, 123)),     # tungsten
        (4000,  (255, 206, 166)),     # cool-white fluorescent
        (5000,  (255, 228, 206)),     # near-neutral
        (5600,  (255, 239, 225)),     # daylight
        (6500,  (255, 254, 250)),     # pure white
        (8000,  (221, 230, 255)),     # cool
        (10000, (202, 218, 255)),     # overcast
    ])
    def test_canonical_anchors(self, kelvin, expected_rgb):
        assert _cct_to_rgb(kelvin) == expected_rgb

    def test_red_saturates_low_kelvin(self):
        """Below 6600K all three values for red should saturate at 255."""
        for k in [1800, 2000, 3200, 5000, 6500]:
            r, _, _ = _cct_to_rgb(k)
            assert r == 255, f"red not saturated at {k}K"

    def test_blue_saturates_high_kelvin(self):
        """At/above 6600K, blue saturates at 255."""
        for k in [6600, 7000, 8000, 10000]:
            _, _, b = _cct_to_rgb(k)
            assert b == 255, f"blue not saturated at {k}K"

    def test_blue_zero_very_low_kelvin(self):
        """Below 1900K, blue clamps to 0 (no cool component)."""
        _, _, b = _cct_to_rgb(1500)
        assert b == 0

    def test_warm_to_cool_monotonic(self):
        """As Kelvin increases through the range, blue should monotonically
        increase. (Subject to floor/ceiling clamps at the extremes.)"""
        last_blue = -1
        for k in range(2000, 9000, 500):
            _, _, b = _cct_to_rgb(k)
            assert b >= last_blue, f"blue regressed at {k}K"
            last_blue = b

    def test_handles_out_of_range_input(self):
        """Inputs outside the algorithm's domain shouldn't crash —
        _cct_to_rgb internally clamps to [1000, 40000]."""
        assert _cct_to_rgb(0) == _cct_to_rgb(1000)
        assert _cct_to_rgb(100000) == _cct_to_rgb(40000)


class TestWwaMix:
    """_wwa_mix — proportional warm/cool/amber blend for WWA fixtures."""

    def test_pure_warm_at_anchor(self):
        """2700K is the warm anchor — full warm, no cool, no amber."""
        mix = _wwa_mix(2700)
        assert mix["warm"] == 1.0
        assert mix["cool"] == 0.0
        assert mix["amber"] == 0.0

    def test_pure_cool_at_anchor(self):
        """6500K is the cool anchor — full cool, no warm, no amber."""
        mix = _wwa_mix(6500)
        assert mix["warm"] == 0.0
        assert mix["cool"] == 1.0
        assert mix["amber"] == 0.0

    def test_midpoint_balanced(self):
        """Halfway between 2700 and 6500 ≈ 4600K — roughly 50/50 warm/cool."""
        mix = _wwa_mix(4600)
        assert abs(mix["warm"] - 0.5) < 0.01
        assert abs(mix["cool"] - 0.5) < 0.01
        assert mix["amber"] == 0.0

    def test_below_warm_anchor_blends_amber(self):
        """Below 2700K, warm should taper toward amber."""
        mix = _wwa_mix(1800)
        assert mix["amber"] == 1.0    # at the amber floor → fully amber
        # Warm is reduced by the amber blend
        assert mix["warm"] == 0.0

    def test_amber_blend_proportional(self):
        """Between 2700K (no amber) and 1800K (full amber), amber should
        scale linearly with how far below 2700K we are."""
        mix_at_2250 = _wwa_mix(2250)  # halfway from 1800 → 2700
        assert abs(mix_at_2250["amber"] - 0.5) < 0.01

    def test_above_cool_anchor_clamps(self):
        """Above 6500K, cool should stay at 1.0, warm at 0.0."""
        mix = _wwa_mix(8000)
        assert mix["warm"] == 0.0
        assert mix["cool"] == 1.0

    def test_below_amber_floor_clamps(self):
        """Below 1800K (amber floor), amber stays at 1.0 — no negative blend."""
        mix = _wwa_mix(1000)
        assert mix["amber"] == 1.0
