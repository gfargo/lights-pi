"""Tests for the cue-list preview-at-time and waveform peaks endpoints
(OSS-1145):

    POST /api/cue_lists/<id>/preview   — apply the cue active at a given
                                          instant without touching playback
    GET  /api/cue_lists/<id>/waveform  — fixed-resolution amplitude peaks
                                          for a cue list's associated audio
"""
import importlib
import math
import os
import struct
import time
import wave as wave_module
from unittest.mock import MagicMock

import pytest
from app import _audio_file_path, _cue_active_at, _wav_peaks

# ---------------------------------------------------------------------------
# Pure helper tests
# ---------------------------------------------------------------------------

def _cues():
    return [
        {"at_ms": 500, "action": "blackout", "parameters": {}, "groups": None},
        {"at_ms": 1500, "action": "strobe", "parameters": {"rate": 8}, "groups": None},
        {"at_ms": 2500, "action": "fade", "parameters": {}, "groups": None},
    ]


class TestCueActiveAt:
    def test_exact_match(self):
        assert _cue_active_at(_cues(), 1500)["action"] == "strobe"

    def test_between_cues_picks_most_recent(self):
        assert _cue_active_at(_cues(), 2000)["action"] == "strobe"

    def test_before_first_cue_returns_none(self):
        assert _cue_active_at(_cues(), 0) is None

    def test_after_last_cue_picks_last(self):
        assert _cue_active_at(_cues(), 999_999)["action"] == "fade"

    def test_empty_cues_returns_none(self):
        assert _cue_active_at([], 500) is None


class TestAudioFilePath:
    def test_none_when_no_audio_file(self):
        assert _audio_file_path({}) is None
        assert _audio_file_path({"audio_file": None}) is None

    def test_none_for_absolute_path(self):
        assert _audio_file_path({"audio_file": "/etc/passwd"}) is None

    def test_none_for_traversal(self):
        assert _audio_file_path({"audio_file": "../../etc/passwd"}) is None

    def test_resolves_within_dir(self, monkeypatch, tmp_path):
        import app as app_module
        monkeypatch.setattr(app_module, "CUE_AUDIO_DIR", tmp_path)
        result = app_module._audio_file_path({"audio_file": "song.wav"})
        assert result == (tmp_path / "song.wav").resolve()


def _write_wav(path, framerate=8000, duration_s=1.0, amplitude=1.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    n_frames = int(framerate * duration_s)
    max_val = 32767
    frames = bytearray()
    for i in range(n_frames):
        t = i / framerate
        sample = int(amplitude * max_val * math.sin(2 * math.pi * 440 * t))
        frames += struct.pack("<h", sample)
    with wave_module.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        wf.writeframes(bytes(frames))


def _write_square_wav(path, framerate=8000, duration_s=1.0):
    """Full-scale square wave — peak and RMS both ≈ 1.0, useful as a known
    reference value."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n_frames = int(framerate * duration_s)
    max_val = 32767
    frames = bytearray()
    for i in range(n_frames):
        sample = max_val if i % 2 == 0 else -max_val
        frames += struct.pack("<h", sample)
    with wave_module.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        wf.writeframes(bytes(frames))


class TestWavPeaks:
    def test_full_scale_square_wave_peak_near_one(self, tmp_path):
        path = tmp_path / "square.wav"
        _write_square_wav(path, framerate=8000, duration_s=1.0)
        peaks = _wav_peaks(path, resolution_ms=50)
        assert len(peaks) == 20
        assert all(p["peak"] > 0.99 for p in peaks)
        assert all(p["rms"] > 0.99 for p in peaks)

    def test_silence_yields_zero(self, tmp_path):
        path = tmp_path / "silence.wav"
        _write_wav(path, amplitude=0.0, framerate=8000, duration_s=0.5)
        peaks = _wav_peaks(path, resolution_ms=50)
        assert len(peaks) == 10
        assert all(p["peak"] == 0.0 for p in peaks)
        assert all(p["rms"] == 0.0 for p in peaks)

    def test_bucket_count_matches_duration(self, tmp_path):
        path = tmp_path / "tone.wav"
        _write_wav(path, framerate=8000, duration_s=0.33)
        peaks = _wav_peaks(path, resolution_ms=50)
        # ceil(330ms / 50ms) = 7
        assert len(peaks) == 7


# ---------------------------------------------------------------------------
# Flask route tests
# ---------------------------------------------------------------------------

@pytest.fixture
def app_module(tmp_path, monkeypatch):
    """Flask app reloaded with MOCK_DMX=1 and cue-list/audio storage
    redirected into tmp_path so tests never touch ~/.qlcplus."""
    os.environ["MOCK_DMX"] = "1"
    import app as _app_module

    importlib.reload(_app_module)
    _app_module.app.config["TESTING"] = True

    monkeypatch.setattr(_app_module, "CUE_LISTS_FILE", tmp_path / "cue_lists.json")
    monkeypatch.setattr(_app_module, "CUE_AUDIO_DIR", tmp_path / "audio")

    yield _app_module

    os.environ.pop("MOCK_DMX", None)
    importlib.reload(_app_module)


@pytest.fixture
def client(app_module):
    with app_module.app.test_client() as c:
        yield c


def _make_cue_list(client, cues, audio_file=None, name="Test List"):
    body = {"name": name, "cues": cues}
    if audio_file:
        body["audio_file"] = audio_file
    r = client.post("/api/cue_lists", json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()["cue_list"]["id"]


class TestPreviewRoute:
    def test_applies_most_recent_cue(self, app_module, client, monkeypatch):
        calls = []
        monkeypatch.setattr(
            app_module,
            "execute_lighting_action",
            lambda action_data, target_groups=None, source="web": calls.append(
                (action_data, target_groups, source)
            ),
        )
        cl_id = _make_cue_list(client, [
            {"at_ms": 0, "action": "blackout"},
            {"at_ms": 1000, "action": "strobe", "parameters": {"rate": 8}},
            {"at_ms": 2000, "action": "fade"},
        ])

        r = client.post(f"/api/cue_lists/{cl_id}/preview", json={"at_ms": 1500})

        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is True
        assert data["applied"]["action"] == "strobe"
        assert len(calls) == 1
        assert calls[0][0] == {"action": "strobe", "parameters": {"rate": 8}}
        assert calls[0][2] == "cue-preview"

    def test_boundary_at_exact_cue_timestamp(self, app_module, client, monkeypatch):
        monkeypatch.setattr(app_module, "execute_lighting_action", lambda *a, **k: None)
        cl_id = _make_cue_list(client, [
            {"at_ms": 0, "action": "blackout"},
            {"at_ms": 1000, "action": "strobe"},
        ])
        r = client.post(f"/api/cue_lists/{cl_id}/preview", json={"at_ms": 0})
        assert r.get_json()["applied"]["action"] == "blackout"

    def test_before_first_cue_applies_nothing(self, app_module, client, monkeypatch):
        calls = []
        monkeypatch.setattr(
            app_module, "execute_lighting_action",
            lambda *a, **k: calls.append(1),
        )
        cl_id = _make_cue_list(client, [{"at_ms": 1000, "action": "blackout"}])

        r = client.post(f"/api/cue_lists/{cl_id}/preview", json={"at_ms": 500})

        assert r.status_code == 200
        data = r.get_json()
        assert data["applied"] is None
        assert calls == []

    def test_does_not_start_playback(self, app_module, client, monkeypatch):
        monkeypatch.setattr(app_module, "execute_lighting_action", lambda *a, **k: None)
        cl_id = _make_cue_list(client, [{"at_ms": 0, "action": "blackout"}])

        client.post(f"/api/cue_lists/{cl_id}/preview", json={"at_ms": 0})

        r = client.get("/api/cue_lists/active")
        assert r.get_json()["active"] == []

    def test_does_not_cancel_running_cue_list(self, app_module, client, monkeypatch):
        monkeypatch.setattr(app_module, "execute_lighting_action", lambda *a, **k: None)
        cl_id = _make_cue_list(client, [
            {"at_ms": 0, "action": "blackout"},
            {"at_ms": 1000, "action": "strobe"},
        ])
        fake_task = MagicMock()
        app_module._active_cue_lists[cl_id] = {
            "started_at": time.time(),
            "cues_fired": [],
            "task": fake_task,
        }
        try:
            r = client.post(f"/api/cue_lists/{cl_id}/preview", json={"at_ms": 500})
            assert r.status_code == 200
            assert cl_id in app_module._active_cue_lists
            fake_task.cancel.assert_not_called()
        finally:
            app_module._active_cue_lists.pop(cl_id, None)

    def test_unknown_cue_list_404(self, client):
        r = client.post("/api/cue_lists/999999/preview", json={"at_ms": 0})
        assert r.status_code == 404

    def test_missing_body_400(self, app_module, client):
        cl_id = _make_cue_list(client, [{"at_ms": 0, "action": "blackout"}])
        r = client.post(f"/api/cue_lists/{cl_id}/preview")
        assert r.status_code == 400

    def test_invalid_at_ms_400(self, app_module, client):
        cl_id = _make_cue_list(client, [{"at_ms": 0, "action": "blackout"}])
        r = client.post(f"/api/cue_lists/{cl_id}/preview", json={"at_ms": "not-a-time"})
        assert r.status_code == 400


class TestWaveformRoute:
    def test_returns_peaks_for_valid_audio(self, app_module, client):
        _write_square_wav(app_module.CUE_AUDIO_DIR / "test.wav")
        cl_id = _make_cue_list(client, [{"at_ms": 0, "action": "blackout"}], audio_file="test.wav")

        r = client.get(f"/api/cue_lists/{cl_id}/waveform")

        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is True
        assert data["audio_file"] == "test.wav"
        assert len(data["peaks"]) > 0
        assert data["peaks"][0]["peak"] > 0.9

    def test_no_audio_file_returns_empty(self, app_module, client):
        cl_id = _make_cue_list(client, [{"at_ms": 0, "action": "blackout"}])

        r = client.get(f"/api/cue_lists/{cl_id}/waveform")

        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is True
        assert data["audio_file"] is None
        assert data["peaks"] == []

    def test_missing_file_returns_empty(self, app_module, client):
        cl_id = _make_cue_list(client, [{"at_ms": 0, "action": "blackout"}], audio_file="nope.wav")

        r = client.get(f"/api/cue_lists/{cl_id}/waveform")

        assert r.status_code == 200
        assert r.get_json()["peaks"] == []

    def test_path_traversal_returns_empty(self, app_module, client):
        cl_id = _make_cue_list(
            client, [{"at_ms": 0, "action": "blackout"}], audio_file="../../etc/passwd"
        )

        r = client.get(f"/api/cue_lists/{cl_id}/waveform")

        assert r.status_code == 200
        assert r.get_json()["peaks"] == []

    def test_unknown_cue_list_404(self, client):
        r = client.get("/api/cue_lists/999999/waveform")
        assert r.status_code == 404
