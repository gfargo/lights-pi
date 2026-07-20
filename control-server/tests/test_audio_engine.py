"""Tests for audio_engine pure helpers (no hardware required)."""
import pytest
from audio_engine import bpm_to_interval_ms, clamp_bpm, noise_gate_passes, smooth_bpm


class TestBpmToIntervalMs:
    @pytest.mark.parametrize("bpm,expected_ms", [
        (60,   1000.0),
        (120,  500.0),
        (180,  333.333),
        (240,  250.0),
        (90,   666.667),
    ])
    def test_common_bpm_values(self, bpm, expected_ms):
        result = bpm_to_interval_ms(bpm)
        assert abs(result - expected_ms) < 0.5, f"bpm={bpm}: expected ~{expected_ms}, got {result}"

    def test_zero_bpm_raises(self):
        with pytest.raises(ValueError, match="BPM must be positive"):
            bpm_to_interval_ms(0)

    def test_negative_bpm_raises(self):
        with pytest.raises(ValueError, match="BPM must be positive"):
            bpm_to_interval_ms(-10)

    def test_returns_float(self):
        assert isinstance(bpm_to_interval_ms(120), float)

    def test_roundtrip(self):
        """60000 / interval_ms should give back the original BPM."""
        for bpm in [60, 90, 120, 140, 180, 200]:
            interval = bpm_to_interval_ms(bpm)
            assert abs(60_000.0 / interval - bpm) < 0.001


class TestClampBpm:
    @pytest.mark.parametrize("bpm,lo,hi,expected", [
        (120, 60, 200, 120),    # in range
        (50,  60, 200, 60),     # below lo
        (250, 60, 200, 200),    # above hi
        (60,  60, 200, 60),     # at lo boundary
        (200, 60, 200, 200),    # at hi boundary
    ])
    def test_clamp_cases(self, bpm, lo, hi, expected):
        assert clamp_bpm(bpm, lo, hi) == expected

    def test_default_range(self):
        """Default lo=60, hi=200."""
        assert clamp_bpm(30)  == 60.0
        assert clamp_bpm(120) == 120.0
        assert clamp_bpm(240) == 200.0

    def test_float_input(self):
        assert clamp_bpm(119.7, 60, 200) == 119.7

    def test_returns_float(self):
        assert isinstance(clamp_bpm(120), float)


class TestNoiseGatePasses:
    @pytest.mark.parametrize("rms,threshold,expected", [
        (0.05, 0.02, True),   # above threshold
        (0.01, 0.02, False),  # below threshold
        (0.02, 0.02, True),   # exactly at threshold
        (0.0,  0.02, False),  # silence
        (0.5,  0.0,  True),   # zero threshold always passes
        (0.0,  0.0,  True),   # zero rms with zero threshold
    ])
    def test_gate_cases(self, rms, threshold, expected):
        assert noise_gate_passes(rms, threshold) == expected

    def test_negative_threshold_blocks(self):
        """Negative threshold should never pass (invalid config)."""
        assert not noise_gate_passes(0.5, -0.01)

    def test_returns_bool(self):
        assert isinstance(noise_gate_passes(0.1, 0.05), bool)


class TestSmoothBpm:
    def test_empty_history_returns_zero(self):
        assert smooth_bpm([]) == 0.0

    def test_single_value(self):
        assert smooth_bpm([120.0]) == 120.0

    def test_median_of_odd_count(self):
        assert smooth_bpm([100, 110, 120]) == 110.0

    def test_median_of_even_count(self):
        assert smooth_bpm([100, 110, 120, 130]) == 115.0

    def test_window_limits_history(self):
        """With window=3, only the last 3 values are considered."""
        history = [60, 60, 60, 60, 60, 120, 120, 120]  # 8 items
        result = smooth_bpm(history, window=3)
        # Last 3 items are [120, 120, 120] → median = 120
        assert result == 120.0

    def test_window_larger_than_history(self):
        """Window larger than history uses all available values."""
        history = [100, 110]
        result = smooth_bpm(history, window=10)
        assert result == 105.0

    def test_outlier_rejection(self):
        """Median resists a single outlier better than mean would."""
        history = [119, 120, 121, 122, 200]  # 200 is an outlier
        result = smooth_bpm(history, window=5)
        assert result == 121.0  # median, not mean (156.4)

    def test_returns_float(self):
        assert isinstance(smooth_bpm([120, 121, 119]), float)
