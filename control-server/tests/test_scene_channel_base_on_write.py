"""Scenes are STORED in QLC+'s native 0-based channel form, whatever the writer used.

QLC+ reads FixtureVal channels 0-based. Several writers (the AI generator, MCP/API clients,
templates, and the live-state snapshot) used 1-based numbers, so when QLC+ played the scene every
value landed one channel too high and the last one fell off the end of the fixture.
"""
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import app as app_module

NS = "{http://www.qlcplus.org/Workspace}"
FIXTURES = [
    {"id": 0, "channels": 7, "address": 0, "universe": 0},
    {"id": 3, "channels": 3, "address": 7, "universe": 0},
]


def _scene(*vals):
    body = "".join(f'<FixtureVal ID="{i}">{t}</FixtureVal>' for i, t in vals)
    return ET.fromstring(f'<Function Type="Scene" Name="s">{body}</Function>')


def _texts(scene):
    return {fv.get("ID"): fv.text for fv in scene}


class TestNormalize:
    def test_one_based_dense_is_shifted_down(self):
        s = _scene((3, "1,255,2,0,3,0"))  # 3-ch fixture: channel 3 == count -> 1-based
        assert app_module._normalize_scene_fixture_vals(s, FIXTURES) == 1
        assert _texts(s) == {"3": "0,255,1,0,2,0"}

    def test_seven_channel_one_based(self):
        s = _scene((0, "1,255,2,255,3,0,4,0,5,80,6,0,7,0"))
        app_module._normalize_scene_fixture_vals(s, FIXTURES)
        assert _texts(s) == {"0": "0,255,1,255,2,0,3,0,4,80,5,0,6,0"}

    def test_zero_based_is_left_alone(self):
        s = _scene((0, "0,255,1,250,2,0,3,0,4,205,5,0,6,0"))
        assert app_module._normalize_scene_fixture_vals(s, FIXTURES) == 0
        assert _texts(s) == {"0": "0,255,1,250,2,0,3,0,4,205,5,0,6,0"}

    def test_ambiguous_sparse_is_left_alone(self):
        s = _scene((0, "2,255,3,255"))  # no 0, no 7: cannot tell, so do not guess
        assert app_module._normalize_scene_fixture_vals(s, FIXTURES) == 0
        assert _texts(s) == {"0": "2,255,3,255"}

    def test_unknown_fixture_and_bad_text_are_skipped(self):
        s = _scene((9, "1,255,2,0,3,0"), (0, "x,y"))
        assert app_module._normalize_scene_fixture_vals(s, FIXTURES) == 0

    def test_mixed_scene_only_changes_the_one_based_fixture(self):
        s = _scene((0, "0,255,1,255"), (3, "1,255,2,0,3,0"))
        assert app_module._normalize_scene_fixture_vals(s, FIXTURES) == 1
        assert _texts(s) == {"0": "0,255,1,255", "3": "0,255,1,0,2,0"}


class TestSnapshot:
    def test_snapshot_writes_zero_based_channels(self, monkeypatch):
        monkeypatch.setattr(app_module, "get_workspace_fixtures", lambda *a, **k: FIXTURES)
        # absolute channel (1-based) -> value: fixture 3 starts at address 7 => abs 8..10
        monkeypatch.setattr(app_module, "get_current_channel_values", lambda: {8: 200, 9: 100, 10: 50})
        xml = app_module._snapshot_current_state_as_xml("snap")
        fv = {e.get("ID"): e.text for e in ET.fromstring(xml.split("<!DOCTYPE Function>")[-1]).iter("FixtureVal")}
        assert fv["3"] == "0,200,1,100,2,50"
        assert fv["0"].startswith("0,0,1,0")


class TestInject:
    def test_inject_normalizes_before_storing(self, monkeypatch, tmp_path):
        ws = tmp_path / "w.qxw"
        shutil.copy(Path(__file__).parent / "fixtures" / "sample.qxw", ws)
        monkeypatch.setattr(app_module, "WORKSPACE_PATH", ws)
        # fixture 0 of the sample workspace has 7 channels
        xml = '<Function Type="Scene" Name="one-based"><FixtureVal ID="0">1,255,2,255,3,0,4,0,5,80,6,0,7,0</FixtureVal></Function>'
        assert app_module._inject_scene_into_workspace(xml, 9001) is True
        def local(e):
            return e.tag.split("}")[-1]
        scene = next(f for f in ET.parse(ws).getroot().iter() if local(f) == "Function" and f.get("ID") == "9001")
        fv = next(v for v in scene if local(v) == "FixtureVal")
        assert fv.text == "0,255,1,255,2,0,3,0,4,80,5,0,6,0"
