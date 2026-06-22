"""Tests for audio-reactive mode pure helpers."""
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import (
    _audio_bpm_from_intervals,
    _audio_compute_rms,
    _audio_mode_valid,
    _audio_normalize_sensitivity,
)


class TestAudioNormalizeSensitivity:
    @pytest.mark.parametrize("value,expected", [
        (0.0, 0.0),
        (1.0, 1.0),
        (0.5, 0.5),
        ("50%", 0.5),
        ("100%", 1.0),
        ("0%", 0.0),
        ("75%", 0.75),
        (1.5, 1.0),   # clamp high
        (-0.1, 0.0),  # clamp low
        (None, 0.5),  # fallback default
        ("bad", 0.5),
        ("bad%", 0.5),
    ])
    def test_normalize(self, value, expected):
        assert _audio_normalize_sensitivity(value) == pytest.approx(expected, abs=1e-9)

    def test_integer_input(self):
        assert _audio_normalize_sensitivity(1) == pytest.approx(1.0)

    def test_zero_string(self):
        assert _audio_normalize_sensitivity("0") == pytest.approx(0.0)


class TestAudioModeValid:
    @pytest.mark.parametrize("mode", [
        "beat_pulse", "amplitude_color", "bpm_sync_chase", "spectrum_split",
    ])
    def test_valid_modes(self, mode):
        assert _audio_mode_valid(mode) is True

    @pytest.mark.parametrize("mode", [
        "unknown", "", "BEAT_PULSE", "beat-pulse", None,
    ])
    def test_invalid_modes(self, mode):
        assert _audio_mode_valid(mode) is False


class TestAudioComputeRms:
    def test_silence(self):
        assert _audio_compute_rms([0.0] * 100) == pytest.approx(0.0)

    def test_empty(self):
        assert _audio_compute_rms([]) == pytest.approx(0.0)

    def test_unit_sine(self):
        # RMS of a pure sine wave with amplitude A is A / sqrt(2)
        N = 1000
        samples = [math.sin(2 * math.pi * i / N) for i in range(N)]
        assert _audio_compute_rms(samples) == pytest.approx(1.0 / math.sqrt(2), rel=1e-3)

    def test_dc_offset(self):
        # Constant signal of value k → RMS = |k|
        assert _audio_compute_rms([0.5] * 50) == pytest.approx(0.5)
        assert _audio_compute_rms([-0.3] * 50) == pytest.approx(0.3)

    def test_single_sample(self):
        assert _audio_compute_rms([0.8]) == pytest.approx(0.8)

    def test_non_negative(self):
        samples = [math.sin(i * 0.1) * 0.9 for i in range(200)]
        assert _audio_compute_rms(samples) >= 0.0


class TestAudioBpmFromIntervals:
    def test_returns_none_for_zero_intervals(self):
        assert _audio_bpm_from_intervals([]) is None

    def test_returns_none_for_one_interval(self):
        assert _audio_bpm_from_intervals([0.5]) is None

    def test_120_bpm(self):
        # 120 BPM = 0.5 s per beat
        intervals = [0.5] * 8
        assert _audio_bpm_from_intervals(intervals) == pytest.approx(120.0)

    def test_60_bpm(self):
        intervals = [1.0] * 6
        assert _audio_bpm_from_intervals(intervals) == pytest.approx(60.0)

    def test_median_rejects_outlier(self):
        # 7 normal intervals at 0.5 s (120 BPM) + 1 outlier
        intervals = [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 5.0]
        bpm = _audio_bpm_from_intervals(intervals)
        assert bpm == pytest.approx(120.0)

    def test_uses_recent_max_beats(self):
        # Only last 8 intervals matter; old 60-BPM intervals are ignored
        old = [1.0] * 20
        recent = [0.5] * 8
        bpm = _audio_bpm_from_intervals(old + recent, max_beats=8)
        assert bpm == pytest.approx(120.0)

    def test_zero_interval_guard(self):
        assert _audio_bpm_from_intervals([0.0, 0.0]) is None

    def test_rounding(self):
        intervals = [0.6] * 4   # 100 BPM exactly
        assert _audio_bpm_from_intervals(intervals) == pytest.approx(100.0)
        assert isinstance(_audio_bpm_from_intervals(intervals), float)
