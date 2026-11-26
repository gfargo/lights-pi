"""Create, read, change and delete through the real routes.

The smoke suite proves routes don't crash on bad input; these tests prove the
happy path does what it says. Each one walks an object through its lifecycle
using only HTTP calls, and checks the result the way a client would see it:
on the next GET, in the workspace file, or in the frames sent to QLC+.

All writes land in the tmp_path data directory from ``patched_app``.
"""
import pytest

SCENE_XML = (
    '<Function ID="0" Type="Scene" Name="placeholder" Path="Tests">'
    '<Speed FadeIn="0" FadeOut="0" Duration="0"/>'
    '<FixtureVal ID="1">0,200</FixtureVal>'
    '<FixtureVal ID="2">0,255,1,64,2,0</FixtureVal>'
    "</Function>"
)


def _ok(response, status=200):
    assert response.status_code == status, response.get_data(as_text=True)[:400]
    return response.get_json()


# ---------------------------------------------------------------------------
# Scenes
# ---------------------------------------------------------------------------

class TestSceneLifecycle:
    def _save(self, client, name="Test Warm"):
        body = _ok(client.post("/api/scenes/save", json={
            "name": name, "path": "Tests", "scene_xml": SCENE_XML,
        }))
        assert body["success"] is True
        return body["scene"]["id"]

    def _scene_names(self, client):
        body = _ok(client.get("/api/scenes"))
        scenes = body.get("scenes", body)
        return {str(s["id"]): s["name"] for s in scenes}

    def test_save_writes_scene_to_workspace(self, flask_client, patched_app):
        client, _ = flask_client
        import app as app_module

        scene_id = self._save(client)
        assert self._scene_names(client)[str(scene_id)] == "Test Warm"
        assert 'Name="Test Warm"' in app_module.WORKSPACE_PATH.read_text()

    def test_describe_returns_fixture_values(self, flask_client):
        client, _ = flask_client
        scene_id = self._save(client)
        body = _ok(client.get(f"/api/scenes/{scene_id}"))
        text = str(body)
        assert "Test Warm" in text
        assert "200" in text

    def test_rename_duplicate_delete(self, flask_client):
        client, _ = flask_client
        scene_id = self._save(client)

        _ok(client.patch(f"/api/scenes/{scene_id}", json={"name": "Test Cool"}))
        assert self._scene_names(client)[str(scene_id)] == "Test Cool"

        dup = _ok(client.post(f"/api/scenes/{scene_id}/duplicate", json={"name": "Test Copy"}))
        assert dup.get("success") is True
        names = self._scene_names(client)
        assert "Test Copy" in names.values()

        _ok(client.delete(f"/api/scenes/{scene_id}"))
        names = self._scene_names(client)
        assert str(scene_id) not in names
        assert "Test Copy" in names.values()

    def test_snapshot_captures_live_values(self, flask_client, monkeypatch):
        client, _ = flask_client
        import app as app_module

        # The mock QLC+ link has no values to read back; supply some.
        monkeypatch.setattr(
            app_module, "get_current_channel_values",
            lambda *a, **k: {1: 180, 2: 10, 3: 20, 4: 30},
        )
        body = _ok(client.post("/api/scenes/snapshot", json={"name": "Snap", "path": "Tests"}))
        assert body.get("success") is True
        assert "Snap" in self._scene_names(client).values()


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------

class TestGroupLifecycle:
    def _groups(self, client):
        body = _ok(client.get("/api/groups"))
        return body.get("groups", body)

    def test_create_change_delete(self, flask_client):
        client, _ = flask_client
        _ok(client.post("/api/groups", json={"name": "front", "fixtures": [1]}))
        assert "front" in str(self._groups(client))

        _ok(client.post("/api/groups/front/fixtures", json={"fixtures": [2]}))
        assert "2" in str(self._groups(client))

        _ok(client.open("/api/groups/front/fixtures", method="DELETE", json={"fixtures": [1]}))

        _ok(client.delete("/api/groups/front"))
        assert "front" not in str(self._groups(client))

    def test_brightness_on_group_only_touches_its_fixtures(self, flask_client):
        client, recorded = flask_client
        _ok(client.post("/api/groups", json={"name": "rgb", "fixtures": [2]}))
        recorded.clear()
        _ok(client.post("/api/action", json={
            "action": "adjust_brightness", "value": "100%", "groups": ["rgb"],
        }))
        channels = {int(cmd.split("|")[1]) for cmd in recorded if cmd.startswith("CH|")}
        assert channels, "no frames sent"
        # Fixture 1 (the dimmer) is on absolute channel 1; it must be left alone.
        assert 1 not in channels


# ---------------------------------------------------------------------------
# Cue lists
# ---------------------------------------------------------------------------

class TestCueListLifecycle:
    def test_create_update_describe_delete(self, flask_client):
        client, _ = flask_client
        created = _ok(client.post("/api/cue_lists", json={
            "name": "Show", "cues": [{"at_ms": 0, "action": "blackout"}],
        }))
        assert created.get("success") is True

        _ok(client.patch("/api/cue_lists/Show", json={
            "cues": [{"at_ms": 0, "action": "blackout"}, {"at_ms": 500, "action": "blackout"}],
        }))
        described = _ok(client.get("/api/cue_lists/Show"))
        assert len(described.get("cue_list", described).get("cues", [])) == 2

        _ok(client.delete("/api/cue_lists/Show"))
        assert client.get("/api/cue_lists/Show").status_code == 404


# ---------------------------------------------------------------------------
# Data the UI stores
# ---------------------------------------------------------------------------

class TestStoredSettings:
    def test_stage_layout_round_trip(self, flask_client):
        client, _ = flask_client
        layout = {"width_m": 6, "depth_m": 4, "positions": {"1": {"x": 1.5, "y": 2.0}}}
        _ok(client.post("/api/stage_layout", json=layout))
        body = _ok(client.get("/api/stage_layout"))
        assert body["positions"]["1"]["x"] == pytest.approx(1.5)

    def test_conversation_create_fork_delete(self, flask_client):
        client, _ = flask_client
        conv = _ok(client.post("/api/conversations", json={"title": "Lights"}), status=201)
        conv_id = conv.get("id") or conv.get("conversation_id")
        assert conv_id

        listed = _ok(client.get("/api/conversations"))
        assert conv_id in str(listed)

        fork = client.post(f"/api/conversations/{conv_id}/fork", json={})
        assert fork.status_code < 500

        assert client.delete(f"/api/conversations/{conv_id}").status_code == 204
        assert client.get(f"/api/conversations/{conv_id}").status_code == 404

    def test_osc_feedback_setting_persists(self, flask_client):
        client, _ = flask_client
        import app as app_module

        _ok(client.post("/api/osc/settings", json={"mode": "fixed", "host": "10.0.0.9", "port": 9001}))
        assert app_module.OSC_SETTINGS_FILE.exists()
        assert "10.0.0.9" in app_module.OSC_SETTINGS_FILE.read_text()
