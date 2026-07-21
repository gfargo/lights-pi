"""Regression test for #875 — a cold /api/scenes must parse the workspace
XML exactly once, instead of ~2N+1 times (once per scene via
_find_scene_element, plus once per swatch via get_workspace_fixtures)."""
import importlib
import os
from pathlib import Path

import pytest

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample.qxw"


@pytest.fixture
def app_module():
    """Flask app reloaded with MOCK_DMX=1 and the sample workspace, so the
    module-level app.WORKSPACE_PATH points at a fixture with real scenes."""
    os.environ["MOCK_DMX"] = "1"
    os.environ["QLC_WORKSPACE"] = str(_FIXTURE)

    import app as _app_module

    importlib.reload(_app_module)
    _app_module.app.config["TESTING"] = True
    yield _app_module

    os.environ.pop("MOCK_DMX", None)
    os.environ.pop("QLC_WORKSPACE", None)
    importlib.reload(_app_module)


def test_cold_scenes_request_parses_workspace_once(app_module, monkeypatch):
    # Force a cold path: clear the swatch cache so nothing is served from it.
    app_module._scene_swatch_cache.clear()
    app_module._scene_swatch_cache_mtime = 0.0

    scenes = app_module.get_workspace_scenes()
    assert len(scenes) >= 2, "fixture must have multiple scenes to exercise reuse"

    counter = {"n": 0}
    orig_workspace_root = app_module._workspace_root

    def counting_workspace_root():
        counter["n"] += 1
        return orig_workspace_root()

    monkeypatch.setattr(app_module, "_workspace_root", counting_workspace_root)

    with app_module.app.test_client() as client:
        resp = client.get("/api/scenes")

    assert resp.status_code == 200
    assert counter["n"] == 1

    payload = resp.get_json()
    assert len(payload["scenes"]) == len(scenes)
    for scene in payload["scenes"]:
        assert "swatch" in scene
