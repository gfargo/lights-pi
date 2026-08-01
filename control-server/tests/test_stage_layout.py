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
        assert resp.get_json() == {"room": {}, "positions": {}}

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
        assert get_resp.get_json() == body

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
        assert resp.get_json() == {"success": True, "room": {}, "positions": {}}
