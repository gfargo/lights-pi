"""Tests for the cue-list time parser (_parse_time_ms / _format_time_ms).

These accept the wide range of inputs that LLM agents + humans actually use:
integer ms, numeric strings, "ms" / "s" suffixes, MM:SS, HH:MM:SS, fractional
seconds. Edge cases (bool, empty string, None, garbage) should all return
None cleanly rather than raising.
"""
import pytest

from app import _parse_time_ms, _format_time_ms


class TestParseTimeMs:
    """_parse_time_ms — coerce various input forms into milliseconds."""

    @pytest.mark.parametrize("inp,expected", [
        # Integer / float → ms directly
        (0, 0),
        (1500, 1500),
        (32500, 32500),
        (32500.0, 32500),
        (32500.7, 32500),  # truncates after int()
    ])
    def test_numeric_inputs(self, inp, expected):
        assert _parse_time_ms(inp) == expected

    @pytest.mark.parametrize("inp,expected", [
        # Plain numeric strings
        ("32", 32),
        ("32500", 32500),
        ("0", 0),
        # Suffix forms
        ("32s", 32000),
        ("0.5s", 500),
        ("32500ms", 32500),
        ("32500 ms", 32500),
        ("32 s", 32000),
    ])
    def test_string_suffix_forms(self, inp, expected):
        assert _parse_time_ms(inp) == expected

    @pytest.mark.parametrize("inp,expected", [
        # MM:SS
        ("0:32", 32_000),
        ("1:00", 60_000),
        ("1:30", 90_000),
        ("12:00", 720_000),
        # MM:SS.mmm
        ("0:32.500", 32_500),
        ("0:32.5", 32_500),
        ("1:45.500", 105_500),
        # HH:MM:SS
        ("1:00:00", 3_600_000),
        ("1:23:45", 5_025_000),
        # HH:MM:SS.mmm
        ("1:23:45.250", 5_025_250),
    ])
    def test_colon_forms(self, inp, expected):
        assert _parse_time_ms(inp) == expected

    @pytest.mark.parametrize("inp", [
        None,
        True,           # Bool — explicitly rejected to avoid int(True) == 1
        False,
        "",
        "garbage",
        "abc:def",
        "1:2:3:4",      # 4 colons — not a valid form
        "5:bad",
    ])
    def test_rejects_invalid(self, inp):
        assert _parse_time_ms(inp) is None

    def test_negative_clamps_to_zero(self):
        """Negative numbers should clamp to 0 (a cue at -5s is nonsense)."""
        assert _parse_time_ms(-1000) == 0
        assert _parse_time_ms(-0.5) == 0

    def test_case_insensitive_suffix(self):
        """Suffixes are case-insensitive."""
        assert _parse_time_ms("32S") == 32_000
        assert _parse_time_ms("32MS") == 32
        assert _parse_time_ms("32Ms") == 32

    def test_whitespace_tolerant(self):
        """Leading/trailing whitespace shouldn't break parsing."""
        assert _parse_time_ms("  32  ") == 32
        assert _parse_time_ms("  0:32  ") == 32_000


class TestFormatTimeMs:
    """_format_time_ms — render ms as M:SS.mmm or H:MM:SS.mmm."""

    @pytest.mark.parametrize("ms,expected", [
        (0,         "0:00.000"),
        (500,       "0:00.500"),
        (1500,      "0:01.500"),
        (32_500,    "0:32.500"),
        (60_000,    "1:00.000"),
        (90_000,    "1:30.000"),
        (105_500,   "1:45.500"),
        (720_000,   "12:00.000"),
    ])
    def test_under_one_hour(self, ms, expected):
        assert _format_time_ms(ms) == expected

    @pytest.mark.parametrize("ms,expected", [
        (3_600_000,   "1:00:00.000"),
        (5_025_000,   "1:23:45.000"),
        (5_025_250,   "1:23:45.250"),
    ])
    def test_one_hour_plus(self, ms, expected):
        assert _format_time_ms(ms) == expected

    def test_none_returns_dash(self):
        """None means "no duration" — render as em-dash for display."""
        assert _format_time_ms(None) == "—"

    def test_negative_clamps_to_zero(self):
        assert _format_time_ms(-1000) == "0:00.000"

    def test_roundtrip(self):
        """parse → format → parse should be lossless for canonical inputs."""
        for original in ["0:32.500", "1:00:00.000", "1:23:45.250"]:
            ms = _parse_time_ms(original)
            assert ms is not None
            formatted = _format_time_ms(ms)
            assert _parse_time_ms(formatted) == ms
