"""Tests for audio-reactive mode pure helpers."""
import math

import pytest

from app import (
    _audio_amplitude_updates,
    _audio_bpm_from_intervals,
    _audio_compute_rms,
    _audio_mode_valid,
    _audio_normalize_sensitivity,
    _audio_pulse_updates,
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


# ---------------------------------------------------------------------------
# _audio_pulse_updates
# ---------------------------------------------------------------------------

def _rgb3_fixture(universe=0, address=0):
    """Minimal 3-channel RGB fixture with no manufacturer/model (uses heuristic)."""
    return {"universe": universe, "address": address, "channels": 3,
            "manufacturer": "", "model": "", "mode": "", "name": "test"}


class TestAudioPulseUpdates:
    def test_empty_fixtures(self):
        assert _audio_pulse_updates([]) == []

    def test_default_intensity_brightness_channels(self):
        fixture = _rgb3_fixture()
        # 3-ch heuristic → brightness=[0,1,2]; absolute channels start at address+1
        updates = _audio_pulse_updates([fixture], intensity=200)
        assert updates == [(1, 200), (2, 200), (3, 200)]

    def test_custom_intensity(self):
        # absolute_channel = universe*512 + address + offset + 1
        # universe=0, address=10, offsets=[0,1,2] → channels 11,12,13
        fixture = _rgb3_fixture(universe=0, address=10)
        updates = _audio_pulse_updates([fixture], intensity=128)
        assert updates == [(11, 128), (12, 128), (13, 128)]

    def test_intensity_clamped_high(self):
        fixture = _rgb3_fixture()
        updates = _audio_pulse_updates([fixture], intensity=999)
        assert all(val == 255 for _, val in updates)

    def test_intensity_clamped_low(self):
        fixture = _rgb3_fixture()
        updates = _audio_pulse_updates([fixture], intensity=-5)
        assert all(val == 0 for _, val in updates)

    def test_multiple_fixtures(self):
        f1 = _rgb3_fixture(universe=0, address=0)
        f2 = _rgb3_fixture(universe=0, address=10)
        updates = _audio_pulse_updates([f1, f2], intensity=100)
        assert len(updates) == 6  # 3 channels each
        assert (1, 100) in updates
        assert (12, 100) in updates


# ---------------------------------------------------------------------------
# _audio_amplitude_updates
# ---------------------------------------------------------------------------

class TestAudioAmplitudeUpdates:
    def test_empty_fixtures(self):
        assert _audio_amplitude_updates([], 0.5) == []

    def test_midpoint_rgb(self):
        # 3-ch heuristic exposes red=0, blue=2; warm/cool not present
        fixture = _rgb3_fixture()
        updates = _audio_amplitude_updates([fixture], 0.5)
        # red_val = int(0.5 * 200) = 100; blue_val = int(0.5 * 200) = 100
        assert (1, 100) in updates  # red ch
        assert (3, 100) in updates  # blue ch
        assert len(updates) == 2

    def test_silence_emphasizes_blue(self):
        fixture = _rgb3_fixture()
        updates = _audio_amplitude_updates([fixture], 0.0)
        update_map = dict(updates)
        assert update_map[1] == 0    # red = 0
        assert update_map[3] == 200  # blue = 200

    def test_peak_emphasizes_red(self):
        fixture = _rgb3_fixture()
        updates = _audio_amplitude_updates([fixture], 1.0)
        update_map = dict(updates)
        assert update_map[1] == 200  # red = 200
        assert update_map[3] == 0    # blue = 0

    def test_values_non_negative(self):
        fixture = _rgb3_fixture()
        for normalized in (0.0, 0.25, 0.5, 0.75, 1.0):
            updates = _audio_amplitude_updates([fixture], normalized)
            assert all(val >= 0 for _, val in updates)
