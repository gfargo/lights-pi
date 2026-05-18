"""Tests for the palette value normalizer (_normalize_palette_value).

Palette accepts a wide range of input shapes per group. The normalizer
turns each into a uniform routing dict that the apply_palette_live
dispatcher can act on.
"""
import pytest
from app import _normalize_palette_value


class TestNormalizePaletteValue:
    """Coerce a palette value into {kelvin|color, intensity} or {error}."""

    @pytest.mark.parametrize("v,expected_color", [
        ("warm", "warm"),
        ("cool", "cool"),
        ("magenta", "magenta"),
        ("red", "red"),
    ])
    def test_color_preset_string(self, v, expected_color):
        result = _normalize_palette_value(v)
        assert result == {"color": expected_color, "intensity": None}

    @pytest.mark.parametrize("v,expected_k", [
        (1800, 1800.0),
        (3200, 3200.0),
        (5600, 5600.0),
        (3200.5, 3200.5),
    ])
    def test_numeric_kelvin(self, v, expected_k):
        result = _normalize_palette_value(v)
        assert result == {"kelvin": expected_k, "intensity": None}

    def test_numeric_string_as_kelvin(self):
        """Numeric strings in the Kelvin range (1000-40000) → Kelvin."""
        assert _normalize_palette_value("3200") == {"kelvin": 3200.0, "intensity": None}

    def test_kelvin_with_k_suffix(self):
        assert _normalize_palette_value("5600K") == {"kelvin": 5600.0, "intensity": None}
        assert _normalize_palette_value("5600k") == {"kelvin": 5600.0, "intensity": None}

    def test_numeric_string_outside_kelvin_range_treated_as_color(self):
        """100 (below 1000K) isn't a plausible Kelvin → treat as color name."""
        result = _normalize_palette_value("100")
        # Falls through to color preset path
        assert result == {"color": "100", "intensity": None}

    def test_explicit_color_dict(self):
        v = {"color": "warm", "intensity": "70%"}
        assert _normalize_palette_value(v) == {"color": "warm", "intensity": "70%"}

    def test_explicit_kelvin_dict(self):
        v = {"kelvin": 3200, "intensity": "50%"}
        assert _normalize_palette_value(v) == {"kelvin": 3200.0, "intensity": "50%"}

    def test_short_key_k(self):
        v = {"k": 3200}
        assert _normalize_palette_value(v) == {"kelvin": 3200.0, "intensity": None}

    def test_dict_without_color_or_kelvin_errors(self):
        result = _normalize_palette_value({"intensity": "70%"})
        assert "error" in result

    def test_dict_with_bad_kelvin_errors(self):
        result = _normalize_palette_value({"kelvin": "not-a-number"})
        assert "error" in result

    def test_bool_rejected(self):
        """Bool is a Python int — explicitly reject to avoid surprise."""
        assert "error" in _normalize_palette_value(True)
        assert "error" in _normalize_palette_value(False)

    def test_empty_string_errors(self):
        assert "error" in _normalize_palette_value("")
        assert "error" in _normalize_palette_value("   ")

    def test_unsupported_type_errors(self):
        for v in [[], None, ()]:
            result = _normalize_palette_value(v)
            assert "error" in result, f"expected error for {v!r}"
