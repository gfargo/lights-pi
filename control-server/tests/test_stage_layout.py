"""Tests for stage layout persistence: _load_stage_layout / _save_stage_layout
helpers and the GET/POST /api/stage_layout routes."""
import json

import app
from app import _load_stage_layout, _save_stage_layout


class TestLoadStageLayout:
    def test_missing_file_returns_default(self, monkeypatch, tmp_path):
        monkeypatch.setattr(app, "STAGE_LAYOUT_FILE", tmp_path / "stage_layout.json")
        assert _load_stage_layout() == {"room": {}, "positions": {}}

    def test_corrupt_json_returns_default(self, monkeypatch, tmp_path):
        layout_file = tmp_path / "stage_layout.json"
        layout_file.write_text("{not valid json")
        monkeypatch.setattr(app, "STAGE_LAYOUT_FILE", layout_file)
        assert _load_stage_layout() == {"room": {}, "positions": {}}

    def test_non_dict_json_returns_default(self, monkeypatch, tmp_path):
        layout_file = tmp_path / "stage_layout.json"
        layout_file.write_text("[1, 2, 3]")
        monkeypatch.setattr(app, "STAGE_LAYOUT_FILE", layout_file)
        assert _load_stage_layout() == {"room": {}, "positions": {}}

    def test_missing_keys_are_defaulted(self, monkeypatch, tmp_path):
        layout_file = tmp_path / "stage_layout.json"
        layout_file.write_text(json.dumps({"positions": {"0": {"x": 1, "y": 2}}}))
        monkeypatch.setattr(app, "STAGE_LAYOUT_FILE", layout_file)
        assert _load_stage_layout() == {"room": {}, "positions": {"0": {"x": 1, "y": 2}}}


class TestSaveStageLayoutRoundTrip:
    def test_round_trip_persists_across_fresh_read(self, monkeypatch, tmp_path):
        monkeypatch.setattr(app, "STAGE_LAYOUT_FILE", tmp_path / "stage_layout.json")
        layout = {
            "room": {"width": 20, "height": 12},
            "positions": {"0": {"x": 1.2, "y": 3.4}, "3": {"x": 5.0, "y": 2.0}},
        }
        _save_stage_layout(layout)

        # Simulate a process restart: read back through a fresh load call.
        assert _load_stage_layout() == layout

    def test_save_creates_parent_directory(self, monkeypatch, tmp_path):
        nested = tmp_path / "nested" / "stage_layout.json"
        monkeypatch.setattr(app, "STAGE_LAYOUT_FILE", nested)
        _save_stage_layout({"room": {}, "positions": {}})
        assert nested.exists()

    def test_missing_fixture_position_survives_round_trip(self, monkeypatch, tmp_path):
        """A position stored for a fixture ID no longer in the workspace is
        returned unchanged — the load path never cross-checks the workspace."""
        monkeypatch.setattr(app, "STAGE_LAYOUT_FILE", tmp_path / "stage_layout.json")
        layout = {"room": {}, "positions": {"999": {"x": 1.0, "y": 1.0}}}
        _save_stage_layout(layout)
        assert _load_stage_layout()["positions"]["999"] == {"x": 1.0, "y": 1.0}


class TestStageLayoutRoutes:
    def _client(self, monkeypatch, tmp_path):
        monkeypatch.setattr(app, "STAGE_LAYOUT_FILE", tmp_path / "stage_layout.json")
        app.app.config["TESTING"] = True
        return app.app.test_client()

    def test_get_empty_layout(self, monkeypatch, tmp_path):
        client = self._client(monkeypatch, tmp_path)
        resp = client.get("/api/stage_layout")
        assert resp.status_code == 200
        assert resp.get_json() == {"room": {}, "positions": {}, "background": None}

    def test_post_then_get_round_trip(self, monkeypatch, tmp_path):
        client = self._client(monkeypatch, tmp_path)
        body = {
            "room": {"width": 20, "height": 12},
            "positions": {"0": {"x": 1.2, "y": 3.4}},
        }
        post_resp = client.post("/api/stage_layout", json=body)
        assert post_resp.status_code == 200
        assert post_resp.get_json()["success"] is True

        get_resp = client.get("/api/stage_layout")
        assert get_resp.get_json() == {**body, "background": None}

    def test_post_drops_unparseable_positions(self, monkeypatch, tmp_path):
        client = self._client(monkeypatch, tmp_path)
        body = {
            "positions": {
                "0": {"x": 1, "y": 2},
                "1": {"x": "not-a-number", "y": 2},
                "2": "not-a-dict",
            }
        }
        resp = client.post("/api/stage_layout", json=body)
        data = resp.get_json()
        assert data["positions"] == {"0": {"x": 1.0, "y": 2.0}}

    def test_post_empty_body_saves_defaults(self, monkeypatch, tmp_path):
        client = self._client(monkeypatch, tmp_path)
        resp = client.post("/api/stage_layout", json={})
        assert resp.status_code == 200
        assert resp.get_json() == {"success": True, "room": {}, "positions": {}, "background": None}


class TestLiveFixtureColors:
    """_live_fixture_colors: live DMX values → one display colour per fixture."""

    RGB_INFO = [
        {"offset": 0, "role": "dimmer"},
        {"offset": 1, "role": "red"},
        {"offset": 2, "role": "green"},
        {"offset": 3, "role": "blue"},
    ]

    def _info(self, fixture):
        return fixture["_info"]

    def test_rgb_fixture_at_address(self):
        fx = [{"id": 0, "name": "Par", "universe": 0, "address": 7, "_info": self.RGB_INFO}]
        values = {"8": 255, "9": 255, "10": 0, "11": 0}
        out = app._live_fixture_colors(fx, values, self._info)
        assert out == [{"id": 0, "name": "Par", "rgb": [255, 0, 0], "level": 255}]

    def test_dimmer_scales_colour(self):
        fx = [{"id": 1, "name": "Par", "universe": 0, "address": 0, "_info": self.RGB_INFO}]
        out = app._live_fixture_colors(fx, {1: 0, 2: 255, 3: 255, 4: 255}, self._info)
        assert out[0]["rgb"] == [0, 0, 0]
        assert out[0]["level"] == 255

    def test_missing_values_read_as_zero(self):
        fx = [{"id": 2, "name": "Par", "universe": 0, "address": 0, "_info": self.RGB_INFO}]
        out = app._live_fixture_colors(fx, {}, self._info)
        assert out[0]["rgb"] == [0, 0, 0]
        assert out[0]["level"] == 0

    def test_no_colour_roles_gives_none(self):
        info = [{"offset": 0, "role": None}, {"offset": 1, "role": "strobe"}]
        fx = [{"id": 3, "name": "Fog", "universe": 0, "address": 0, "_info": info}]
        out = app._live_fixture_colors(fx, {"1": 200}, self._info)
        assert out[0]["rgb"] is None
        assert out[0]["level"] == 200

    def test_second_universe_offsets_by_512(self):
        info = [{"offset": 0, "role": "dimmer"}]
        fx = [{"id": 4, "name": "Dim", "universe": 1, "address": 0, "_info": info}]
        out = app._live_fixture_colors(fx, {"513": 128, "1": 255}, self._info)
        assert out[0]["level"] == 128


class TestStageColorsRoute:
    def test_returns_colour_per_fixture(self, monkeypatch):
        fixtures = [{"id": 0, "name": "Par", "universe": 0, "address": 0, "channels": 4,
                     "manufacturer": "", "model": "", "mode": ""}]
        monkeypatch.setattr(app, "get_workspace_fixtures", lambda: fixtures)
        monkeypatch.setattr(app, "get_current_channel_values", lambda *a, **k: {1: 255, 2: 0, 3: 255, 4: 0})
        monkeypatch.setattr(app, "_fixture_channels_info", lambda f: TestLiveFixtureColors.RGB_INFO)
        r = app.app.test_client().get("/api/stage/colors")
        assert r.status_code == 200
        assert r.get_json()["fixtures"][0]["rgb"] == [0, 255, 0]


class TestStageAim:
    def test_normalize_aim(self):
        assert app._normalize_aim(None) is None
        assert app._normalize_aim("north") is None
        assert app._normalize_aim(True) is None
        assert app._normalize_aim(float("nan")) is None
        assert app._normalize_aim(0) == 0
        assert app._normalize_aim(89.6) == 90
        assert app._normalize_aim(-90) == 270
        assert app._normalize_aim(720) == 0

    def test_post_keeps_aim(self, monkeypatch, tmp_path):
        monkeypatch.setattr(app, "STAGE_LAYOUT_FILE", tmp_path / "stage_layout.json")
        client = app.app.test_client()
        body = {"positions": {"0": {"x": 1, "y": 2, "rotation": -45}, "1": {"x": 3, "y": 4, "rotation": "x"}}}
        data = client.post("/api/stage_layout", json=body).get_json()
        assert data["positions"] == {"0": {"x": 1.0, "y": 2.0, "rotation": 315}, "1": {"x": 3.0, "y": 4.0}}


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 16


class TestStageBackground:
    def _client(self, monkeypatch, tmp_path):
        monkeypatch.setattr(app, "STAGE_LAYOUT_FILE", tmp_path / "stage_layout.json")
        return app.app.test_client()

    def test_image_kind(self):
        assert app._image_kind(PNG[:16]) == "png"
        assert app._image_kind(JPG[:16]) == "jpg"
        assert app._image_kind(WEBP[:16]) == "webp"
        assert app._image_kind(b"<svg xmlns=") is None
        assert app._image_kind(b"") is None

    def test_get_without_upload_is_404(self, monkeypatch, tmp_path):
        assert self._client(monkeypatch, tmp_path).get("/api/stage/background").status_code == 404

    def test_upload_get_replace_delete(self, monkeypatch, tmp_path):
        import io
        client = self._client(monkeypatch, tmp_path)
        resp = client.post("/api/stage/background", data={"image": (io.BytesIO(PNG), "plan.png")},
                           content_type="multipart/form-data")
        assert resp.status_code == 200 and resp.get_json()["background"]
        got = client.get("/api/stage/background")
        assert got.status_code == 200 and got.mimetype == "image/png" and got.data == PNG
        assert client.get("/api/stage_layout").get_json()["background"]

        # A new upload of another type replaces the old file.
        client.post("/api/stage/background", data={"image": (io.BytesIO(JPG), "plan.jpg")},
                    content_type="multipart/form-data")
        assert not (tmp_path / "stage_background.png").exists()
        assert client.get("/api/stage/background").mimetype == "image/jpeg"

        assert client.delete("/api/stage/background").get_json()["background"] is None
        assert client.get("/api/stage/background").status_code == 404

    def test_rejects_non_images(self, monkeypatch, tmp_path):
        import io
        client = self._client(monkeypatch, tmp_path)
        resp = client.post("/api/stage/background",
                           data={"image": (io.BytesIO(b"<svg onload=alert(1)>"), "plan.svg")},
                           content_type="multipart/form-data")
        assert resp.status_code == 415
        assert client.post("/api/stage/background", data={}).status_code == 400

    def test_rejects_oversize(self, monkeypatch, tmp_path):
        import io
        monkeypatch.setattr(app, "STAGE_BACKGROUND_MAX_BYTES", 64)
        client = self._client(monkeypatch, tmp_path)
        resp = client.post("/api/stage/background", data={"image": (io.BytesIO(PNG + b"\x00" * 64), "p.png")},
                           content_type="multipart/form-data")
        assert resp.status_code == 413


class TestStageTargets:
    def test_split_targets(self):
        assert app._split_targets(["front", 3, "back", True, None, 0]) == (["front", "back"], ["3", "0"])
        assert app._split_targets(None) == ([], [])

    def test_command_targets(self):
        assert app._command_targets({}) is None
        assert app._command_targets({"groups": [], "fixtures": []}) is None
        assert app._command_targets({"groups": ["front"], "fixtures": ["3", 4, "x"]}) == ["front", 3, 4]

    def test_group_fixture_ids_mixes_groups_and_fixtures(self, monkeypatch, tmp_path):
        groups = tmp_path / "groups.json"
        groups.write_text(json.dumps({"groups": {"front": {"fixtures": [0, 1]}}}))
        monkeypatch.setattr(app, "GROUPS_FILE", groups)
        assert app._group_fixture_ids(["front", 5]) == {"0", "1", "5"}
        assert app._group_fixture_ids([5]) == {"5"}
        assert app._group_fixture_ids(None) is None

    def test_fixture_ids_work_without_groups_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(app, "GROUPS_FILE", tmp_path / "missing.json")
        assert app._group_fixture_ids([2, 7]) == {"2", "7"}

    def test_filter_scene_xml_keeps_only_picked(self):
        xml = (
            '<Function xmlns="http://www.qlcplus.org/Workspace" Type="Scene" Name="s">'
            '<FixtureVal ID="0">0,255</FixtureVal><FixtureVal ID="1">0,128</FixtureVal>'
            '<FixtureVal ID="3">0,64</FixtureVal></Function>'
        )
        out = app._filter_scene_xml_fixtures(xml, {"1", "3"})
        root = app._scene_root_from_xml(out)
        ids = [fv.get("ID") for fv in app._find_children(root, "FixtureVal")]
        assert ids == ["1", "3"]


class TestUploadSizeLimit:
    def _client(self, monkeypatch, tmp_path):
        monkeypatch.setattr(app, "STAGE_LAYOUT_FILE", tmp_path / "stage_layout.json")
        return app.app.test_client()

    def test_accepts_photo_bigger_than_app_body_limit(self, monkeypatch, tmp_path):
        # The app-wide limit is 1 MB; a 3 MB floor plan must still go through.
        import io
        client = self._client(monkeypatch, tmp_path)
        big = PNG + b"\x00" * (3 * 1024 * 1024)
        assert len(big) > app.app.config["MAX_CONTENT_LENGTH"]
        resp = client.post("/api/stage/background", data={"image": (io.BytesIO(big), "plan.png")},
                           content_type="multipart/form-data")
        assert resp.status_code == 200, resp.get_data(as_text=True)[:200]
        assert (tmp_path / "stage_background.png").stat().st_size == len(big)

    def test_body_over_8_mb_is_refused_with_json(self, monkeypatch, tmp_path):
        import io
        client = self._client(monkeypatch, tmp_path)
        huge = PNG + b"\x00" * (9 * 1024 * 1024)
        resp = client.post("/api/stage/background", data={"image": (io.BytesIO(huge), "plan.png")},
                           content_type="multipart/form-data")
        assert resp.status_code == 413
        assert resp.get_json()["success"] is False
        assert not (tmp_path / "stage_background.png").exists()


def test_other_routes_keep_the_1_mb_limit():
    client = app.app.test_client()
    resp = client.post("/api/stage_layout", data=b"{" + b" " * (2 * 1024 * 1024) + b"}",
                       content_type="application/json")
    assert resp.status_code == 413
    assert "1 MB" in resp.get_json()["error"]
