"""Tests for scene-swatch pure helpers: _fixture_values_to_rgb and swatch SVG."""
import pytest
from app import _fixture_values_to_rgb, _scene_swatch_svg, _neutral_swatch_svg


def ch(role, value, offset=0, name=""):
    """Build a channel dict as returned by _scene_value_breakdown."""
    return {"offset": offset, "name": name or role or f"Ch {offset + 1}", "role": role, "value": value}


class TestFixtureValuesToRgb:
    """_fixture_values_to_rgb — maps channel breakdown to display RGB."""

    def test_pure_red(self):
        assert _fixture_values_to_rgb([ch("red", 255), ch("green", 0), ch("blue", 0)]) == (255, 0, 0)

    def test_pure_green(self):
        assert _fixture_values_to_rgb([ch("red", 0), ch("green", 255), ch("blue", 0)]) == (0, 255, 0)

    def test_pure_blue(self):
        assert _fixture_values_to_rgb([ch("red", 0), ch("green", 0), ch("blue", 255)]) == (0, 0, 255)

    def test_white_channel_brightens_all(self):
        r, g, b = _fixture_values_to_rgb([ch("red", 0), ch("green", 0), ch("blue", 0), ch("white", 200)])
        assert r == g == b == 200

    def test_dimmer_scales_rgb(self):
        r, g, b = _fixture_values_to_rgb([ch("red", 255), ch("green", 255), ch("blue", 255), ch("dimmer", 128)])
        # dimmer=128/255 ≈ 0.502
        assert r == pytest.approx(128, abs=1)
        assert g == pytest.approx(128, abs=1)
        assert b == pytest.approx(128, abs=1)

    def test_dimmer_zero_produces_black(self):
        assert _fixture_values_to_rgb([ch("red", 255), ch("green", 255), ch("blue", 255), ch("dimmer", 0)]) == (0, 0, 0)

    def test_amber_adds_orange_to_red(self):
        r, g, b = _fixture_values_to_rgb([ch("red", 0), ch("green", 0), ch("blue", 0), ch("amber", 255)])
        assert r == 255
        assert g > 0   # amber has a green component
        assert b == 0

    def test_wwa_warm_only(self):
        """Full warm channel → incandescent (2700K) color, scaled by level."""
        r, g, b = _fixture_values_to_rgb([ch("warm", 255), ch("cool", 0)])
        # At 2700K: cct_to_rgb → (255, 167, 87); scale = 1.0
        assert r == 255
        assert g > 0
        assert b >= 0

    def test_wwa_cool_only(self):
        """Full cool channel → daylight (6500K) color."""
        r, g, b = _fixture_values_to_rgb([ch("warm", 0), ch("cool", 255)])
        # At 6500K: cct_to_rgb → (255, 254, 250)
        assert r > 200
        assert g > 200
        assert b > 200

    def test_wwa_zero_levels_returns_none(self):
        assert _fixture_values_to_rgb([ch("warm", 0), ch("cool", 0)]) is None

    def test_dimmer_only_returns_neutral_white(self):
        r, g, b = _fixture_values_to_rgb([ch("dimmer", 255)])
        assert r == g == b == 255

    def test_dimmer_only_half_level(self):
        r, g, b = _fixture_values_to_rgb([ch("dimmer", 128)])
        assert r == g == b == 128

    def test_no_color_roles_returns_none(self):
        """Channels with no recognisable role should yield None."""
        assert _fixture_values_to_rgb([ch(None, 100), ch(None, 200)]) is None

    def test_empty_channels_returns_none(self):
        assert _fixture_values_to_rgb([]) is None

    def test_rgb_clamps_at_255(self):
        """White + full red must not overflow."""
        r, g, b = _fixture_values_to_rgb([ch("red", 255), ch("green", 0), ch("blue", 0), ch("white", 255)])
        assert r == 255

    def test_duplicate_roles_takes_max(self):
        """If a fixture has two red channels, the highest value wins."""
        r, g, b = _fixture_values_to_rgb([
            ch("red", 100, offset=0),
            ch("red", 200, offset=1),
            ch("green", 0),
            ch("blue", 0),
        ])
        assert r == 200

    def test_wwa_amber_channel_brightens(self):
        """Amber on a WWA fixture should tint the result warmer/brighter."""
        r_no_amber, g_no_amber, _ = _fixture_values_to_rgb([ch("warm", 128), ch("cool", 0), ch("amber", 0)])
        r_amber, g_amber, _ = _fixture_values_to_rgb([ch("warm", 128), ch("cool", 0), ch("amber", 100)])
        assert r_amber >= r_no_amber


class TestSwatchSvg:
    """_neutral_swatch_svg and basic _scene_swatch_svg output shapes."""

    def test_neutral_swatch_is_data_uri(self):
        uri = _neutral_swatch_svg()
        assert uri.startswith("data:image/svg+xml;charset=utf-8,")

    def test_neutral_swatch_contains_rect(self):
        uri = _neutral_swatch_svg()
        assert "%3Crect" in uri or "rect" in uri

    def test_neutral_swatch_no_raw_angle_brackets(self):
        uri = _neutral_swatch_svg()
        # Raw < > must be encoded in the data URI
        assert "<" not in uri
        assert ">" not in uri
