"""Integration tests for /api/midi/* — device listing, mapping CRUD,
persistence across a simulated process restart, and dispatch → state.

Follows the app_module reload pattern from test_scenes_parse_count.py:
HOME is redirected to a tmp_path so MIDI_MAPPINGS_FILE
(~/.qlcplus/midi_mappings.json) is isolated per test, and reloading the
module simulates a real restart for the persistence test.
"""
import importlib
from pathlib import Path

import pytest

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample.qxw"


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MOCK_DMX", "1")
    monkeypatch.setenv("QLC_WORKSPACE", str(_FIXTURE))

    import app as _app_module

    importlib.reload(_app_module)
    _app_module.app.config["TESTING"] = True
    yield _app_module

    for var in ("HOME", "MOCK_DMX", "QLC_WORKSPACE"):
        monkeypatch.delenv(var, raising=False)
    importlib.reload(_app_module)


CHANNEL_MAPPING_BODY = {
    "name": "Fixture 0 master",
    "input": {"type": "cc", "number": 21},
    "action": {"type": "channel", "fixture_id": 0, "channel_offset": 0},
}


class TestDevicesRoute:
    def test_returns_empty_list_without_crashing(self, app_module):
        """Acceptance: GET /api/midi/devices returns [] gracefully when no
        MIDI hardware is present — must not crash the server headless."""
        with app_module.app.test_client() as client:
            resp = client.get("/api/midi/devices")
        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload["devices"] == []
        assert isinstance(payload["available"], bool)


class TestMappingsCrud:
    def test_list_empty_initially(self, app_module):
        with app_module.app.test_client() as client:
            resp = client.get("/api/midi/mappings")
        assert resp.status_code == 200
        assert resp.get_json() == {"mappings": []}

    def test_create_then_list(self, app_module):
        with app_module.app.test_client() as client:
            create = client.post("/api/midi/mappings", json=CHANNEL_MAPPING_BODY)
            assert create.status_code == 200
            created = create.get_json()
            assert created["success"] is True
            mapping_id = created["mapping"]["id"]

            listed = client.get("/api/midi/mappings").get_json()
        assert len(listed["mappings"]) == 1
        assert listed["mappings"][0]["id"] == mapping_id
        assert listed["mappings"][0]["action"]["fixture_id"] == 0

    def test_create_rejects_invalid_body(self, app_module):
        with app_module.app.test_client() as client:
            resp = client.post("/api/midi/mappings", json={"input": {"type": "cc"}, "action": {}})
        assert resp.status_code == 400
        assert resp.get_json()["success"] is False

    def test_update_mapping(self, app_module):
        with app_module.app.test_client() as client:
            created = client.post("/api/midi/mappings", json=CHANNEL_MAPPING_BODY).get_json()
            mapping_id = created["mapping"]["id"]

            updated = client.patch(
                f"/api/midi/mappings/{mapping_id}",
                json={"action": {"type": "channel", "fixture_id": 0, "channel_offset": 2}},
            )
            assert updated.status_code == 200
            assert updated.get_json()["mapping"]["action"]["channel_offset"] == 2

            listed = client.get("/api/midi/mappings").get_json()
        assert listed["mappings"][0]["action"]["channel_offset"] == 2
        # id is stable across an update
        assert listed["mappings"][0]["id"] == mapping_id

    def test_update_missing_mapping_404(self, app_module):
        with app_module.app.test_client() as client:
            resp = client.patch("/api/midi/mappings/does-not-exist", json=CHANNEL_MAPPING_BODY)
        assert resp.status_code == 404

    def test_delete_mapping(self, app_module):
        with app_module.app.test_client() as client:
            created = client.post("/api/midi/mappings", json=CHANNEL_MAPPING_BODY).get_json()
            mapping_id = created["mapping"]["id"]

            deleted = client.delete(f"/api/midi/mappings/{mapping_id}")
            assert deleted.status_code == 200
            assert deleted.get_json()["success"] is True

            listed = client.get("/api/midi/mappings").get_json()
        assert listed["mappings"] == []

    def test_delete_missing_mapping_404(self, app_module):
        with app_module.app.test_client() as client:
            resp = client.delete("/api/midi/mappings/does-not-exist")
        assert resp.status_code == 404
        assert resp.get_json()["success"] is False

    def test_delete_is_idempotent_404_not_500(self, app_module):
        with app_module.app.test_client() as client:
            created = client.post("/api/midi/mappings", json=CHANNEL_MAPPING_BODY).get_json()
            mapping_id = created["mapping"]["id"]
            client.delete(f"/api/midi/mappings/{mapping_id}")
            second = client.delete(f"/api/midi/mappings/{mapping_id}")
        assert second.status_code == 404


class TestMappingPersistence:
    def test_mappings_survive_a_process_restart(self, app_module, monkeypatch, tmp_path):
        """Acceptance: mappings persist across a process restart."""
        with app_module.app.test_client() as client:
            created = client.post("/api/midi/mappings", json=CHANNEL_MAPPING_BODY).get_json()
        mapping_id = created["mapping"]["id"]

        assert app_module.MIDI_MAPPINGS_FILE.exists()

        # Simulate a restart: reload the module fresh against the same HOME/
        # QLC_WORKSPACE env (still set by the app_module fixture at this point).
        import app as _reloaded
        importlib.reload(_reloaded)
        _reloaded.app.config["TESTING"] = True

        with _reloaded.app.test_client() as client:
            listed = client.get("/api/midi/mappings").get_json()

        assert len(listed["mappings"]) == 1
        assert listed["mappings"][0]["id"] == mapping_id
        assert listed["mappings"][0]["action"]["fixture_id"] == 0


class TestMidiState:
    def test_state_empty_initially(self, app_module):
        with app_module.app.test_client() as client:
            resp = client.get("/api/midi/state")
        assert resp.status_code == 200
        assert resp.get_json() == {"state": {}}

    def test_state_reflects_last_dispatched_value(self, app_module, monkeypatch):
        recorded = []
        monkeypatch.setattr(
            app_module, "set_channel_values",
            lambda updates: recorded.append(updates) or True,
        )

        with app_module.app.test_client() as client:
            created = client.post("/api/midi/mappings", json=CHANNEL_MAPPING_BODY).get_json()
            mapping_id = created["mapping"]["id"]

            # Fixture 0 (universe 0, address 0) → offset 0 → absolute channel 1.
            app_module._on_midi_message("Test Controller", [0xB0, 21, 64])

            state = client.get("/api/midi/state").get_json()

        assert mapping_id in state["state"]
        assert state["state"][mapping_id] == 64
        assert recorded, "set_channel_values should have been invoked by dispatch"
        assert recorded[0][0][0] == 1  # absolute channel for fixture 0 offset 0

    def test_unmapped_message_does_not_update_state(self, app_module):
        with app_module.app.test_client() as client:
            app_module._on_midi_message("Test Controller", [0xB0, 99, 64])  # no mapping for CC 99
            state = client.get("/api/midi/state").get_json()
        assert state == {"state": {}}

    def test_malformed_message_does_not_crash_listener(self, app_module):
        # Should not raise — _on_midi_message swallows parse failures.
        app_module._on_midi_message("Test Controller", [0xB0, 999, 64])
        with app_module.app.test_client() as client:
            resp = client.get("/api/midi/state")
        assert resp.status_code == 200
