"""Tests for the strobe rate → DMX value mapping (_strobe_dmx_value)."""
import pytest

from app import _strobe_dmx_value


class TestStrobeDmxValue:
    """Maps rate input → DMX channel value 0 (rest) or 10..255 (slow → fast)."""

    @pytest.mark.parametrize("rate,expected", [
        # Explicit off forms
        ("off", 0),
        ("OFF", 0),
        ("Off", 0),
        ("stop", 0),
        ("rest", 0),
        ("none", 0),
        ("0", 0),
        ("0hz", 0),
        (0, 0),
        (0.0, 0),
        # None / null
        (None, 0),
    ])
    def test_off_forms(self, rate, expected):
        assert _strobe_dmx_value(rate) == expected

    @pytest.mark.parametrize("rate,expected", [
        # 0.5 Hz → DMX 16 (10 + (0.5/20)*245)
        (0.5,  16),
        (1,    22),
        (5,    71),
        (10,  132),
        (15,  194),
        (20,  255),
    ])
    def test_numeric_rates(self, rate, expected):
        assert _strobe_dmx_value(rate) == expected

    @pytest.mark.parametrize("rate", [25, 30, 100, 1000])
    def test_above_max_clamps_to_255(self, rate):
        """Above 20 Hz clamps to DMX 255 (typically fastest reliable strobe)."""
        assert _strobe_dmx_value(rate) == 255

    @pytest.mark.parametrize("rate,expected", [
        ("5Hz", 71),
        ("12", 157),
        ("12Hz", 157),
        ("0.5 Hz", 16),
        # Mixed case
        ("8HZ", 108),
    ])
    def test_string_rate_with_optional_hz(self, rate, expected):
        assert _strobe_dmx_value(rate) == expected

    @pytest.mark.parametrize("rate", ["garbage", "", "fast", "very-slow"])
    def test_unparseable_returns_zero(self, rate):
        """If we can't parse it, default to off (safe behavior)."""
        assert _strobe_dmx_value(rate) == 0

    def test_negative_returns_zero(self):
        """Negative rate is nonsense — off."""
        assert _strobe_dmx_value(-5) == 0
