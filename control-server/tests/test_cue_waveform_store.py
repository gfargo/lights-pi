"""The waveform the timeline editor decodes in the browser (#29).

The browser decodes the show's audio and sends only peaks; the Pi checks
them with ``_validate_waveform`` before storing. Pure-helper tests here;
the routes are exercised in ``integration/test_cue_timeline.py``.
"""
from app import WAVEFORM_MAX_BUCKETS, _normalize_cues, _validate_waveform


def _body(**over):
    body = {
        "audio_file": "song.mp3",
        "resolution_ms": 50,
        "audio_duration_ms": 150,
        "peaks": [[0.5, 0.2], [1.0, 0.7], [0.0, 0.0]],
    }
    body.update(over)
    return body


class TestValidateWaveform:
    def test_accepts_pairs_and_normalises_to_objects(self):
        clean, err = _validate_waveform(_body())
        assert err is None
        assert clean["peaks"] == [
            {"peak": 0.5, "rms": 0.2}, {"peak": 1.0, "rms": 0.7}, {"peak": 0.0, "rms": 0.0},
        ]
        assert clean["resolution_ms"] == 50
        assert clean["audio_duration_ms"] == 150

    def test_accepts_objects(self):
        clean, err = _validate_waveform(_body(peaks=[{"peak": 0.3, "rms": 0.1}]))
        assert err is None
        assert clean["peaks"] == [{"peak": 0.3, "rms": 0.1}]

    def test_clamps_out_of_range_values(self):
        clean, _ = _validate_waveform(_body(peaks=[[1.7, -0.2]]))
        assert clean["peaks"] == [{"peak": 1.0, "rms": 0.0}]

    def test_keeps_only_the_file_name(self):
        clean, _ = _validate_waveform(_body(audio_file="../../etc/show.wav"))
        assert clean["audio_file"] == "show.wav"

    def test_missing_duration_falls_back_to_bucket_count(self):
        clean, _ = _validate_waveform(_body(audio_duration_ms=None))
        assert clean["audio_duration_ms"] == 150

    def test_rejections(self):
        cases = [
            ("not a dict", "JSON object"),
            (_body(audio_file=""), "audio_file"),
            (_body(resolution_ms="50"), "resolution_ms"),
            (_body(resolution_ms=True), "resolution_ms"),
            (_body(resolution_ms=1), "at least"),
            (_body(peaks=[]), "non-empty"),
            (_body(peaks=[[0.1]]), "Each peak"),
            (_body(peaks=[["a", 0.1]]), "Each peak"),
            (_body(peaks=[[float("nan"), 0.1]]), "Each peak"),
            (_body(peaks=[[0.1, 0.1]] * (WAVEFORM_MAX_BUCKETS + 1)), "At most"),
        ]
        for body, fragment in cases:
            clean, err = _validate_waveform(body)
            assert clean is None, body if not isinstance(body, dict) else fragment
            assert fragment in err


class TestNormalizeCues:
    def test_sorts_by_time_and_keeps_canonical_form(self):
        cues, errors = _normalize_cues([
            {"at_ms": 2000, "action": "blackout"},
            {"at": "0:01", "action": "strobe", "parameters": {"rate": 8}, "groups": ["front"]},
        ])
        assert errors == []
        assert [c["at_ms"] for c in cues] == [1000, 2000]
        assert cues[0] == {"at_ms": 1000, "action": "strobe", "parameters": {"rate": 8}, "groups": ["front"]}

    def test_reports_errors_by_input_index(self):
        _, errors = _normalize_cues([{"at_ms": 0, "action": "blackout"}, {"action": "blackout"}])
        assert errors == [{"index": 1, "error": "Cue missing 'at' or 'at_ms'"}]

    def test_empty_or_wrong_type(self):
        for raw in ([], None, {"at_ms": 0}):
            cues, errors = _normalize_cues(raw)
            assert cues == [] and errors
