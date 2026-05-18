"""Miscellaneous small helper tests — direction / run-order normalization,
fixture ID coercion, level parser."""
import pytest
from app import (
    _normalize_direction,
    _normalize_fixture_ids,
    _normalize_run_order,
    _parse_level,
)


class TestNormalizeDirection:
    @pytest.mark.parametrize("inp,expected", [
        ("Forward", "Forward"),
        ("forward", "Forward"),
        ("FORWARD", "Forward"),
        (" forward ", "Forward"),
        ("Backward", "Backward"),
        ("backward", "Backward"),
    ])
    def test_canonical_forms(self, inp, expected):
        assert _normalize_direction(inp) == expected

    def test_none_returns_default(self):
        assert _normalize_direction(None) == "Forward"
        assert _normalize_direction("") == "Forward"

    def test_unknown_returns_default(self):
        assert _normalize_direction("garbage") == "Forward"

    def test_custom_default(self):
        assert _normalize_direction(None, default="Backward") == "Backward"


class TestNormalizeRunOrder:
    @pytest.mark.parametrize("inp,expected", [
        ("Loop", "Loop"),
        ("loop", "Loop"),
        ("LOOP", "Loop"),
        ("SingleShot", "SingleShot"),
        ("single-shot", "SingleShot"),
        ("single_shot", "SingleShot"),
        ("PingPong", "PingPong"),
        ("ping-pong", "PingPong"),
        ("ping_pong", "PingPong"),
        ("Random", "Random"),
        ("random", "Random"),
    ])
    def test_canonical_forms(self, inp, expected):
        assert _normalize_run_order(inp) == expected

    def test_none_defaults_to_loop(self):
        assert _normalize_run_order(None) == "Loop"

    def test_unknown_defaults_to_loop(self):
        assert _normalize_run_order("garbage") == "Loop"


class TestNormalizeFixtureIds:
    def test_basic_list(self):
        assert _normalize_fixture_ids([0, 1, 2]) == [0, 1, 2]

    def test_string_numerics_coerced(self):
        assert _normalize_fixture_ids(["0", "1", "2"]) == [0, 1, 2]

    def test_dedupes(self):
        assert _normalize_fixture_ids([0, 1, 0, 2, 1]) == [0, 1, 2]

    def test_drops_unparseable(self):
        assert _normalize_fixture_ids([0, "bad", 1, None, 2]) == [0, 1, 2]

    def test_empty_inputs(self):
        assert _normalize_fixture_ids([]) == []
        assert _normalize_fixture_ids(None) == []

    def test_preserves_order(self):
        assert _normalize_fixture_ids([5, 2, 7, 1]) == [5, 2, 7, 1]


class TestParseLevel:
    """_parse_level — accept 0-255 / "75%" / "+30" / "-20" forms."""

    @pytest.mark.parametrize("inp,expected", [
        ("0", 0),
        ("128", 128),
        ("255", 255),
        ("300", 255),    # clamp to 255
        ("-10", 0),      # clamp to 0
        (200, 200),
        (300, 255),
    ])
    def test_absolute_values(self, inp, expected):
        assert _parse_level(inp) == expected

    @pytest.mark.parametrize("pct,expected", [
        ("0%", 0),
        ("50%", 128),     # rounded
        ("75%", 191),
        ("100%", 255),
        ("110%", 255),    # clamp at 100%
    ])
    def test_percentage_forms(self, pct, expected):
        assert _parse_level(pct) == expected

    def test_relative_with_current_value(self):
        """+30 relative to a current of 100 → 130."""
        assert _parse_level("+30", current=100) == 130
        assert _parse_level("-20", current=100) == 80

    def test_relative_clamps(self):
        """Relative addition clamps to 0..255."""
        assert _parse_level("+300", current=100) == 255
        assert _parse_level("-300", current=100) == 0

    def test_relative_without_current_treated_as_absolute(self):
        """When no current value is passed, '+30' falls through to absolute
        parsing — returns 30. This matches the implementation's behavior;
        the relative path only fires when current is not None."""
        assert _parse_level("+30", current=None, default=100) == 30
        assert _parse_level("-20", current=None, default=100) == 0  # clamped

    def test_none_returns_default(self):
        assert _parse_level(None, default=200) == 200

    def test_empty_string_returns_default(self):
        assert _parse_level("", default=200) == 200
        assert _parse_level("   ", default=200) == 200

    def test_garbage_returns_default(self):
        assert _parse_level("garbage", default=128) == 128
