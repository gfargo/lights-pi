"""Tests for the shared 0-based/1-based FixtureVal offset heuristic.

Covers _decode_fixture_val_pairs directly, plus integration checks pinning
that scene_to_channel_values (playback) and _scene_value_breakdown (swatch)
decode the same sparse scene identically.
"""
import xml.etree.ElementTree as ET

from app import (
    _decode_fixture_val_pairs,
    _fixture_values_to_rgb,
    _scene_value_breakdown,
    scene_to_channel_values,
)


class TestDecodeFixtureValPairs:
    def test_sparse_zero_based_omitting_channel_zero(self):
        """Green+blue on a 0-based scene that never touches channel 0."""
        assert _decode_fixture_val_pairs([(1, 255), (2, 255)], 3) == [(1, 255), (2, 255)]

    def test_dense_zero_based(self):
        assert _decode_fixture_val_pairs([(0, 240), (1, 255), (2, 220)], 3) == [
            (0, 240),
            (1, 255),
            (2, 220),
        ]

    def test_dense_one_based_rgb(self):
        assert _decode_fixture_val_pairs([(1, 200), (2, 200), (3, 200)], 3) == [
            (0, 200),
            (1, 200),
            (2, 200),
        ]

    def test_dimmer_only_one_based(self):
        assert _decode_fixture_val_pairs([(1, 255)], 1) == [(0, 255)]

    def test_empty_pairs(self):
        assert _decode_fixture_val_pairs([], 3) == []

    def test_missing_channel_count_defaults_to_zero_based(self):
        assert _decode_fixture_val_pairs([(1, 255), (2, 255)], 0) == [(1, 255), (2, 255)]


RGB_FIXTURE = {
    "id": 0,
    "name": "Test RGB Par",
    "universe": 0,
    "address": 0,
    "channels": 3,
    "manufacturer": "",
    "model": "",
    "mode": "",
}


def _sparse_scene_root():
    return ET.fromstring('<Function ID="0" Type="Scene"><FixtureVal ID="0">1,255,2,255</FixtureVal></Function>')


class TestSparseSceneDecodesAsCyan:
    """The issue's acceptance case: '1,255,2,255' on R=0/G=1/B=2 -> cyan, not yellow."""

    def test_playback_lights_green_and_blue(self, monkeypatch):
        monkeypatch.setattr("app.get_workspace_fixtures", lambda: [RGB_FIXTURE])
        updates = scene_to_channel_values(_sparse_scene_root())
        # absolute_channel = universe*512 + address + offset + 1
        assert sorted(updates) == [(2, 255), (3, 255)]

    def test_swatch_breakdown_offsets_are_green_and_blue(self, monkeypatch):
        monkeypatch.setattr("app.get_workspace_fixtures", lambda: [RGB_FIXTURE])
        breakdown = _scene_value_breakdown(_sparse_scene_root())
        assert len(breakdown) == 1
        channels = breakdown[0]["channels"]
        assert [c["offset"] for c in channels] == [1, 2]

    def test_swatch_renders_cyan_not_yellow(self, monkeypatch):
        monkeypatch.setattr("app.get_workspace_fixtures", lambda: [RGB_FIXTURE])
        channels = _scene_value_breakdown(_sparse_scene_root())[0]["channels"]
        rgb = _fixture_values_to_rgb(channels)
        assert rgb is not None
        r, g, b = rgb
        assert r == 0
        assert g > 0
        assert b > 0
