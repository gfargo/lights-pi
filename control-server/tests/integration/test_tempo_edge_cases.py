"""Route-level tests for tempo/tap-chase edge cases (issue #64):

  - POST /api/chases/<id>/tempo must reject non-finite BPM (NaN/Infinity)
    with 400 instead of letting it through to a 500 in _bpm_to_step_ms.
  - POST /api/chases/<id>/start on a tap-source chase with zero playable
    steps must report failure (4xx) instead of a false "tap runner started".
"""
import asyncio
import xml.etree.ElementTree as ET

import pytest


@pytest.fixture
def tempo_app(monkeypatch, tmp_path, test_workspace):
    """Flask app wired to a private, writable copy of the test workspace —
    mirrors `concurrent_app` in test_workspace_concurrency.py so these tests
    can create/mutate chases without touching the git-tracked fixture."""
    import shutil

    import app as app_module

    ws_copy = tmp_path / "workspace.qxw"
    shutil.copy(test_workspace, ws_copy)
    monkeypatch.setattr(app_module, "WORKSPACE_PATH", ws_copy)

    groups_file = tmp_path / "fixture_groups.json"
    cue_lists_file = tmp_path / "cue_lists.json"
    groups_file.write_text("{}")
    cue_lists_file.write_text('{"cue_lists": []}')
    monkeypatch.setattr(app_module, "GROUPS_FILE", groups_file)
    monkeypatch.setattr(app_module, "CUE_LISTS_FILE", cue_lists_file)

    async def _mock_send_commands(commands):
        pass

    def _mock_qlc_run(coro, timeout=10):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    monkeypatch.setattr(app_module, "_qlc_send_commands", _mock_send_commands)
    monkeypatch.setattr(app_module, "_qlc_run", _mock_qlc_run)

    app_module.app.config["TESTING"] = True
    app_module._tap_runners.clear()
    yield app_module.app, ws_copy, app_module
    app_module._tap_runners.clear()


def _scene_xml(name: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE Function>\n'
        f'<Function Type="Scene" Name="{name}" Path="AI Generated">\n'
        '  <Speed FadeIn="0" FadeOut="0" Duration="0"/>\n'
        '  <FixtureVal ID="1">1,255</FixtureVal>\n'
        '</Function>'
    )


@pytest.fixture
def tap_chase(tempo_app):
    """Creates a tap-source chase with one valid step, returns (client, chase_id, app_module, ws_path)."""
    flask_app, ws_path, app_module = tempo_app
    client = flask_app.test_client()

    r = client.post("/api/scenes/save", json={"name": "Seed", "scene_xml": _scene_xml("Seed")})
    assert r.get_json()["success"] is True

    r = client.post("/api/chases", json={
        "name": "EdgeCaseTapChase",
        "steps": ["Seed"],
        "tempo_source": "tap",
    })
    body = r.get_json()
    assert body["success"] is True, body
    chase_id = body["chase"]["id"]
    return client, chase_id, app_module, ws_path


class TestTempoNonFiniteBpm:
    @pytest.mark.parametrize("bpm_value", [
        float("nan"),
        float("inf"),
        float("-inf"),
    ])
    def test_python_float_non_finite_rejected(self, tap_chase, bpm_value):
        client, chase_id, app_module, _ = tap_chase
        r = client.post(f"/api/chases/{chase_id}/tempo", json={"bpm": bpm_value})
        assert r.status_code == 400
        assert r.get_json()["success"] is False

    @pytest.mark.parametrize("bpm_value", ["nan", "inf", "-inf", "infinity"])
    def test_string_non_finite_rejected(self, tap_chase, bpm_value):
        client, chase_id, app_module, _ = tap_chase
        r = client.post(f"/api/chases/{chase_id}/tempo", json={"bpm": bpm_value})
        assert r.status_code == 400
        assert r.get_json()["success"] is False

    def test_valid_bpm_still_accepted(self, tap_chase):
        client, chase_id, app_module, _ = tap_chase
        r = client.post(f"/api/chases/{chase_id}/tempo", json={"bpm": 120})
        assert r.status_code == 200
        assert r.get_json()["success"] is True

    @pytest.mark.parametrize("bpm_value", [39, 241, 0, -10])
    def test_out_of_range_still_rejected(self, tap_chase, bpm_value):
        client, chase_id, app_module, _ = tap_chase
        r = client.post(f"/api/chases/{chase_id}/tempo", json={"bpm": bpm_value})
        assert r.status_code == 400
        assert r.get_json()["success"] is False


class TestStartTapChaseNoPlayableSteps:
    def test_start_reports_failure_and_registers_no_runner(self, tap_chase):
        client, chase_id, app_module, ws_path = tap_chase

        # Corrupt the chase's only step so it fails to resolve to a scene id,
        # mirroring the issue's repro (non-numeric <Step> Values).
        tree = ET.parse(ws_path)
        root = tree.getroot()
        chase_elem = None
        for func in root.iter():
            if func.tag.endswith("Function") and func.get("ID") == str(chase_id):
                chase_elem = func
                break
        assert chase_elem is not None, "chase element not found in workspace"
        for step in list(chase_elem):
            if step.tag.endswith("Step"):
                step.set("Values", "notanumber")
                step.text = "notanumber"
        tree.write(ws_path, encoding="UTF-8", xml_declaration=True)

        r = client.post(f"/api/chases/{chase_id}/start")
        assert r.status_code >= 400 and r.status_code < 500
        body = r.get_json()
        assert body["success"] is False
        assert str(chase_id) not in app_module._tap_runners

    def test_start_with_valid_step_still_succeeds(self, tap_chase):
        client, chase_id, app_module, _ = tap_chase
        r = client.post(f"/api/chases/{chase_id}/start")
        assert r.status_code == 200
        body = r.get_json()
        assert body["success"] is True
        assert body["response"] == "tap runner started"
        app_module._stop_tap_runner(str(chase_id))
