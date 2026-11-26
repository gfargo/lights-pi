"""Routes the cue list timeline editor (#29) relies on, end to end.

Storage lands in the ``patched_app`` tmp directory (CUE_AUDIO_DIR and
CUE_LISTS_FILE are sandboxed there).
"""


def _ok(response, status=200):
    assert response.status_code == status, response.get_data(as_text=True)[:400]
    return response.get_json()


def _make(client, name="Timeline", cues=None):
    cues = cues or [{"at_ms": 0, "action": "blackout"}, {"at_ms": 4000, "action": "blackout"}]
    return _ok(client.post("/api/cue_lists", json={"name": name, "cues": cues}))["cue_list"]["id"]


WAVE = {"audio_file": "show.mp3", "resolution_ms": 100, "audio_duration_ms": 300,
        "peaks": [[0.2, 0.1], [0.9, 0.5], [0.4, 0.2]]}


class TestWaveformStore:
    def test_put_get_delete_round_trip(self, flask_client):
        client, _ = flask_client
        import app as app_module

        cl_id = _make(client)
        empty = _ok(client.get(f"/api/cue_lists/{cl_id}/waveform"))
        assert empty["peaks"] == []

        stored = _ok(client.put(f"/api/cue_lists/{cl_id}/waveform", json=WAVE))
        assert stored == {"success": True, "audio_file": "show.mp3", "audio_duration_ms": 300, "buckets": 3}
        assert app_module._waveform_sidecar_path(cl_id).exists()

        got = _ok(client.get(f"/api/cue_lists/{cl_id}/waveform"))
        assert got["audio_file"] == "show.mp3"
        assert got["resolution_ms"] == 100
        assert got["peaks"][1] == {"peak": 0.9, "rms": 0.5}
        assert _ok(client.get(f"/api/cue_lists/{cl_id}"))["cue_list"]["audio_file"] == "show.mp3"

        _ok(client.delete(f"/api/cue_lists/{cl_id}/waveform"))
        assert not app_module._waveform_sidecar_path(cl_id).exists()
        assert _ok(client.get(f"/api/cue_lists/{cl_id}/waveform"))["peaks"] == []
        assert _ok(client.get(f"/api/cue_lists/{cl_id}"))["cue_list"]["audio_file"] is None

    def test_bad_waveform_is_rejected_and_nothing_stored(self, flask_client):
        client, _ = flask_client
        import app as app_module

        cl_id = _make(client)
        body = _ok(client.put(f"/api/cue_lists/{cl_id}/waveform", json={**WAVE, "peaks": "loud"}), status=400)
        assert "peaks" in body["error"]
        assert not app_module._waveform_sidecar_path(cl_id).exists()

    def test_deleting_the_cue_list_removes_its_waveform(self, flask_client):
        client, _ = flask_client
        import app as app_module

        cl_id = _make(client)
        _ok(client.put(f"/api/cue_lists/{cl_id}/waveform", json=WAVE))
        _ok(client.delete(f"/api/cue_lists/{cl_id}"))
        assert not app_module._waveform_sidecar_path(cl_id).exists()

    def test_unknown_cue_list(self, flask_client):
        client, _ = flask_client
        assert client.put("/api/cue_lists/999/waveform", json=WAVE).status_code == 404
        assert client.delete("/api/cue_lists/999/waveform").status_code == 404


class TestTimelineEdits:
    def test_retimed_cues_persist(self, flask_client):
        """Acceptance: drag a cue, save, reload; the new time is kept."""
        client, _ = flask_client
        cues = [{"at_ms": i * 1000, "action": "blackout"} for i in range(24)]
        cl_id = _make(client, cues=cues)
        listed = _ok(client.get(f"/api/cue_lists/{cl_id}"))["cue_list"]["cues"]
        assert len(listed) == 24

        moved = [dict(c) for c in listed]
        moved[3]["at_ms"] = 3250  # what the editor sends after a drag
        _ok(client.patch(f"/api/cue_lists/{cl_id}", json={"cues": [
            {"at_ms": c["at_ms"], "action": c["action"], "parameters": c["parameters"]} for c in moved
        ]}))
        reloaded = _ok(client.get(f"/api/cue_lists/{cl_id}"))["cue_list"]["cues"]
        assert [c["at_ms"] for c in reloaded][:5] == [0, 1000, 2000, 3250, 4000]

    def test_preview_uses_unsaved_cues_when_sent(self, flask_client):
        client, recorded = flask_client
        bright = {"at_ms": 0, "action": "adjust_brightness", "parameters": {"value": "50%"}}
        cl_id = _make(client, cues=[bright])

        recorded.clear()
        saved = _ok(client.post(f"/api/cue_lists/{cl_id}/preview", json={"at_ms": 0}))
        assert saved["applied"]["action"] == "adjust_brightness"
        assert recorded, "preview sent nothing to QLC+"

        # In the editor the first cue was moved to 2 s and not yet saved:
        # at 1 s nothing should apply.
        unsaved = _ok(client.post(f"/api/cue_lists/{cl_id}/preview", json={
            "at_ms": 1000, "cues": [{**bright, "at_ms": 2000}],
        }))
        assert unsaved["applied"] is None

        # The stored list is untouched by a preview.
        stored = _ok(client.get(f"/api/cue_lists/{cl_id}"))["cue_list"]["cues"]
        assert stored[0]["at_ms"] == 0

    def test_preview_skips_half_finished_unsaved_cues(self, flask_client):
        # A cue still being edited (no time yet, a chase not picked) must not
        # stop scrubbing elsewhere in the list.
        client, recorded = flask_client
        bright = {"at_ms": 0, "action": "adjust_brightness", "parameters": {"value": "50%"}}
        cl_id = _make(client, cues=[bright])
        recorded.clear()
        body = _ok(client.post(f"/api/cue_lists/{cl_id}/preview", json={
            "at_ms": 500,
            "cues": [bright, {"action": "blackout"}, {"at_ms": 9000, "chase": ""}],
        }))
        assert body["applied"]["action"] == "adjust_brightness"
        assert recorded

    def test_preview_reports_a_broken_reference_only_when_it_fires(self, flask_client):
        client, _ = flask_client
        bright = {"at_ms": 0, "action": "adjust_brightness", "parameters": {"value": "50%"}}
        cl_id = _make(client, cues=[bright])
        cues = [bright, {"at_ms": 2000, "scene": "No Such Scene"}]
        _ok(client.post(f"/api/cue_lists/{cl_id}/preview", json={"at_ms": 1000, "cues": cues}))
        body = _ok(client.post(f"/api/cue_lists/{cl_id}/preview", json={"at_ms": 2500, "cues": cues}),
                   status=400)
        assert "No Such Scene" in body["error"]

    def test_preview_rejects_cues_that_are_not_a_list(self, flask_client):
        client, _ = flask_client
        cl_id = _make(client)
        _ok(client.post(f"/api/cue_lists/{cl_id}/preview", json={"at_ms": 0, "cues": "all"}), status=400)


class TestBadNumbers:
    """Flask's JSON parser accepts NaN and Infinity; they must not reach int()."""

    def _raw(self, client, method, url, text):
        return client.open(url, method=method, data=text, content_type="application/json")

    def test_non_finite_times_and_resolutions_are_400(self, flask_client):
        client, _ = flask_client
        cl_id = _make(client)
        assert self._raw(client, "POST", f"/api/cue_lists/{cl_id}/preview", '{"at_ms": NaN}').status_code == 400
        assert self._raw(client, "POST", f"/api/cue_lists/{cl_id}/preview", '{"at_ms": Infinity}').status_code == 400
        resp = self._raw(client, "PUT", f"/api/cue_lists/{cl_id}/waveform",
                         '{"audio_file": "a.mp3", "resolution_ms": Infinity, "peaks": [[0.1, 0.1]]}')
        assert resp.status_code == 400
        resp = self._raw(client, "PUT", f"/api/cue_lists/{cl_id}/waveform",
                         '{"audio_file": "a.mp3", "resolution_ms": 50, "audio_duration_ms": NaN, "peaks": [[0.1, 0.1]]}')
        assert resp.status_code == 200
        assert resp.get_json()["audio_duration_ms"] == 50

    def test_parameters_must_be_an_object(self, flask_client):
        client, _ = flask_client
        resp = client.post("/api/cue_lists", json={
            "name": "Bad params", "cues": [{"at_ms": 0, "action": "activate_scene", "parameters": "Warm"}],
        })
        assert resp.status_code == 400


def test_changing_audio_file_drops_the_old_waveform(flask_client):
    client, _ = flask_client
    import app as app_module

    cl_id = _make(client)
    _ok(client.put(f"/api/cue_lists/{cl_id}/waveform", json=WAVE))
    _ok(client.patch(f"/api/cue_lists/{cl_id}", json={"audio_file": "show.mp3"}))
    assert app_module._waveform_sidecar_path(cl_id).exists(), "same file: keep the waveform"

    _ok(client.patch(f"/api/cue_lists/{cl_id}", json={"audio_file": "encore.wav"}))
    assert not app_module._waveform_sidecar_path(cl_id).exists()
    assert _ok(client.get(f"/api/cue_lists/{cl_id}/waveform"))["peaks"] == []
