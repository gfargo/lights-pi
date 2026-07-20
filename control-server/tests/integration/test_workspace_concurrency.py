"""Concurrency test for workspace .qxw writers (issue #63).

Hammers POST /api/chases/<id>/tempo from multiple threads while scene saves
run concurrently, and asserts the final workspace file is never corrupted and
never loses an update. Guards against regressing the _WORKSPACE_LOCK /
_atomic_write_tree serialization added to fix the race.
"""
import asyncio
import os
import shutil
import stat
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

_NS = {"qlc": "http://www.qlcplus.org/Workspace"}


@pytest.fixture
def concurrent_app(monkeypatch, tmp_path, test_workspace):
    """Flask app wired to a *private, writable* copy of the test workspace.

    Unlike `patched_app` (shared session-scoped fixture file), this test
    mutates the workspace file for real, so it must not touch the fixture
    shared by other tests.
    """
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
    return app_module.app, ws_copy


def _scene_xml(name: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE Function>\n'
        f'<Function Type="Scene" Name="{name}" Path="AI Generated">\n'
        '  <Speed FadeIn="0" FadeOut="0" Duration="0"/>\n'
        '  <FixtureVal ID="1">1,255</FixtureVal>\n'
        '</Function>'
    )


class TestWorkspaceConcurrency:
    def test_concurrent_tempo_and_scene_saves(self, concurrent_app):
        flask_app, ws_path = concurrent_app
        client = flask_app.test_client()

        # Seed a scene to build chases from.
        r = client.post("/api/scenes/save", json={"name": "Seed", "scene_xml": _scene_xml("Seed")})
        assert r.get_json()["success"] is True

        # Six tap-source chases, all referencing the seed scene. Each chase
        # gets exactly one dedicated hammering thread below — that keeps the
        # final BPM per chase deterministic (only one writer touches a given
        # chase's Duration/Hold) while still exercising real cross-chase and
        # cross-endpoint concurrency against the shared _WORKSPACE_LOCK.
        chase_ids = []
        for i in range(6):
            chase_name = f"TapChase{i}"
            r = client.post("/api/chases", json={
                "name": chase_name,
                "steps": ["Seed"],
                "tempo_source": "tap",
            })
            body = r.get_json()
            assert body["success"] is True, body
            chase_ids.append(body["chase"]["id"])

        N_TAPS_PER_THREAD = 8
        N_SCENE_SAVES = 15

        scene_names = [f"ConcurrentScene{i}" for i in range(N_SCENE_SAVES)]
        expected_bpm_by_chase = {}

        def hammer_tempo(chase_id, seed):
            app_client = flask_app.test_client()
            bpm = None
            for i in range(N_TAPS_PER_THREAD):
                bpm = 60 + ((seed + i) % 180)  # stays within 40-240
                resp = app_client.post(
                    f"/api/chases/{chase_id}/tempo", json={"bpm": bpm}
                )
                assert resp.status_code == 200, resp.get_json()
            return chase_id, bpm

        def save_scene(name):
            app_client = flask_app.test_client()
            resp = app_client.post(
                "/api/scenes/save", json={"name": name, "scene_xml": _scene_xml(name)}
            )
            assert resp.status_code == 200, resp.get_json()
            assert resp.get_json()["success"] is True

        with ThreadPoolExecutor(max_workers=len(chase_ids) + 4) as pool:
            futures = []
            for seed, chase_id in enumerate(chase_ids):
                futures.append(pool.submit(hammer_tempo, chase_id, seed))
            for name in scene_names:
                futures.append(pool.submit(save_scene, name))

            for fut in as_completed(futures):
                result = fut.result()
                if isinstance(result, tuple):
                    chase_id, last_bpm = result
                    # Exactly one thread writes each chase's tempo, so the
                    # last BPM *that thread* sent is the only value that can
                    # legally survive the lock's serialization.
                    expected_bpm_by_chase[chase_id] = last_bpm

        # The workspace file must parse cleanly — no torn/truncated writes.
        tree = ET.parse(ws_path)
        root = tree.getroot()

        # Injected Function subtrees are appended without the qlc: namespace
        # prefix (see _inject_scene_into_workspace / _inject_chase_into_workspace),
        # so — matching the app's own delete/rename lookups — search both forms.
        engine = root.find("qlc:Engine", _NS)
        assert engine is not None
        all_functions = list(engine.findall("qlc:Function", _NS)) + list(engine.findall("Function"))

        # Every scene save must be present — no lost updates.
        scene_elements = [f for f in all_functions if f.get("Type") == "Scene"]
        found_names = {f.get("Name") for f in scene_elements}
        assert "Seed" in found_names
        for name in scene_names:
            assert name in found_names, f"lost scene save: {name}"

        # Every chase must still exist with the *exact* last BPM its dedicated
        # thread sent — not just "some valid value" — proving no tempo write
        # was silently lost to the race.
        import app as app_module

        chase_elements = {
            f.get("ID"): f for f in all_functions if f.get("Type") == "Chaser"
        }
        for chase_id in chase_ids:
            chase_el = chase_elements.get(str(chase_id))
            assert chase_el is not None, f"lost chase: {chase_id}"
            speed = chase_el.find("qlc:Speed", _NS)
            if speed is None:
                speed = chase_el.find("Speed")
            assert speed is not None
            duration = int(speed.get("Duration"))
            expected_duration = app_module._bpm_to_step_ms(expected_bpm_by_chase[chase_id])
            assert duration == expected_duration, (
                f"chase {chase_id}: expected Duration {expected_duration} "
                f"(from last BPM {expected_bpm_by_chase[chase_id]}), got {duration}"
            )
            steps = list(chase_el.findall("qlc:Step", _NS)) + list(chase_el.findall("Step"))
            assert steps
            for step in steps:
                assert int(step.get("Hold")) == duration

        # No leftover atomic-write temp files.
        leftover = list(ws_path.parent.glob(".qlc-ws-*"))
        assert leftover == [], f"leftover temp files: {leftover}"

    def test_atomic_write_tree_leaves_no_temp_files(self, concurrent_app):
        import app as app_module

        flask_app, ws_path = concurrent_app
        tree = ET.parse(ws_path)
        with app_module._WORKSPACE_LOCK:
            app_module._atomic_write_tree(tree)

        # File still parses and no stray temp file remains.
        ET.parse(ws_path)
        leftover = list(ws_path.parent.glob(".qlc-ws-*"))
        assert leftover == [], f"leftover temp files: {leftover}"

    def test_atomic_write_tree_preserves_file_mode(self, concurrent_app):
        """mkstemp defaults to 0600 — _atomic_write_tree must carry over the
        original workspace file's mode instead of silently tightening it."""
        import app as app_module

        flask_app, ws_path = concurrent_app
        os.chmod(ws_path, 0o644)

        tree = ET.parse(ws_path)
        with app_module._WORKSPACE_LOCK:
            app_module._atomic_write_tree(tree)

        assert stat.S_IMODE(os.stat(ws_path).st_mode) == 0o644

    def test_concurrent_tempo_same_chase_no_torn_write(self, concurrent_app):
        """N threads racing tempo POSTs against the SAME chase must never
        corrupt the file — the final Duration must be one of the values
        actually sent, and every Step/Hold must agree with it (no torn write
        mixing bytes from two different mutations)."""
        import app as app_module

        flask_app, ws_path = concurrent_app
        client = flask_app.test_client()

        r = client.post("/api/scenes/save", json={"name": "Seed", "scene_xml": _scene_xml("Seed")})
        assert r.get_json()["success"] is True

        r = client.post("/api/chases", json={
            "name": "SharedTapChase",
            "steps": ["Seed"],
            "tempo_source": "tap",
        })
        body = r.get_json()
        assert body["success"] is True, body
        chase_id = body["chase"]["id"]

        N_THREADS = 8
        bpms = [60 + (i * 23) % 180 for i in range(N_THREADS)]  # stays within 40-240

        def hit_tempo(bpm):
            app_client = flask_app.test_client()
            resp = app_client.post(f"/api/chases/{chase_id}/tempo", json={"bpm": bpm})
            assert resp.status_code == 200, resp.get_json()

        with ThreadPoolExecutor(max_workers=N_THREADS) as pool:
            futures = [pool.submit(hit_tempo, bpm) for bpm in bpms]
            for fut in as_completed(futures):
                fut.result()

        sent_durations = {app_module._bpm_to_step_ms(bpm) for bpm in bpms}

        # Must parse cleanly — no torn/truncated write.
        tree = ET.parse(ws_path)
        root = tree.getroot()
        engine = root.find("qlc:Engine", _NS)
        assert engine is not None
        all_functions = list(engine.findall("qlc:Function", _NS)) + list(engine.findall("Function"))
        chase_elements = {f.get("ID"): f for f in all_functions if f.get("Type") == "Chaser"}

        chase_el = chase_elements.get(str(chase_id))
        assert chase_el is not None, f"lost chase: {chase_id}"
        speed = chase_el.find("qlc:Speed", _NS)
        if speed is None:
            speed = chase_el.find("Speed")
        assert speed is not None
        duration = int(speed.get("Duration"))
        assert duration in sent_durations, (
            f"chase {chase_id}: Duration {duration} is not among the sent values {sent_durations}"
        )

        steps = list(chase_el.findall("qlc:Step", _NS)) + list(chase_el.findall("Step"))
        assert steps
        for step in steps:
            assert int(step.get("Hold")) == duration, (
                "Step/Hold disagrees with Speed/Duration — torn write"
            )
