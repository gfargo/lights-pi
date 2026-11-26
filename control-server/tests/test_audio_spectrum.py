"""Spectrum effect (#12): frequency bands to fixtures.

Pure maths in audio_react (band ranges, splitting fixtures, frames), the
settings and band assignment in app.py, and the runner with a fake engine.
"""
import asyncio
import math

import app
import audio_react as ar
import pytest
from audio_engine import AudioEngine


def _abs(base):
    return lambda off: base + off + 1


RGB = {"dimmer": 0, "red": 1, "green": 2, "blue": 3, "brightness": [0]}
WHITE = {"dimmer": 0, "brightness": [0]}


class TestBandBins:
    def test_slices_sum_to_band_energies(self):
        sr, n = 44100, 2048
        mags = [math.sin(i) ** 2 + 0.1 for i in range(n // 2 + 1)]
        bins = ar.band_bins(sr, n)
        sliced = tuple(sum(m * m for m in mags[a:b]) for a, b in bins.values())
        assert sliced == pytest.approx(ar.band_energies(mags, sr, n))

    def test_ranges_are_ordered_and_adjacent(self):
        bins = ar.band_bins(44100, 2048)
        assert list(bins) == ["low", "mid", "high"]
        assert bins["low"][1] == bins["mid"][0] and bins["mid"][1] == bins["high"][0]
        assert bins["low"][0] > 0                       # DC and sub-bass excluded


class TestAssignBands:
    def test_thirds_in_order(self):
        assert ar.assign_bands([1, 2, 3, 4, 5, 6]) == {1: "low", 2: "low", 3: "mid", 4: "mid", 5: "high", 6: "high"}

    def test_small_rigs(self):
        assert ar.assign_bands([]) == {}
        assert ar.assign_bands([7]) == {7: "low"}
        assert ar.assign_bands([7, 8]) == {7: "low", 8: "high"}
        assert ar.assign_bands([7, 8, 9]) == {7: "low", 8: "mid", 9: "high"}

    def test_left_to_right_on_the_stage_plan(self):
        positions = {"1": {"x": 5.0}, "2": {"x": 1.0}, "3": {"x": 3.0}}
        assert ar.assign_bands([1, 2, 3], positions) == {2: "low", 3: "mid", 1: "high"}

    def test_falls_back_to_order_when_a_fixture_is_off_the_plan(self):
        positions = {"1": {"x": 5.0}, "2": {"x": 1.0}}
        assert ar.assign_bands([1, 2, 3], positions) == {1: "low", 2: "mid", 3: "high"}


class TestSpectrumFrame:
    def test_rgb_fixture_takes_its_band_colour_at_the_band_level(self):
        plan = [{**ar.plan_fixture(RGB, _abs(0)), "band": "high"}]
        frame = dict(ar.spectrum_frame(plan, {"low": 0, "mid": 0, "high": 1.0}))
        assert frame[1] == 255                          # dimmer open
        assert (frame[2], frame[3], frame[4]) == tuple(ar.quantize(c) for c in ar.BAND_COLOURS["high"])

    def test_silence_glows_at_the_floor(self):
        plan = [{**ar.plan_fixture(WHITE, _abs(0)), "band": "low"}]
        frame = dict(ar.spectrum_frame(plan, {"low": 0.0}))
        assert frame[1] == ar.quantize(round(ar.SPECTRUM_FLOOR * 255))

    def test_bands_drive_their_own_fixtures(self):
        plan = [
            {**ar.plan_fixture(WHITE, _abs(0)), "band": "low"},
            {**ar.plan_fixture(WHITE, _abs(10)), "band": "high"},
        ]
        frame = dict(ar.spectrum_frame(plan, {"low": 1.0, "high": 0.0}))
        assert frame[1] == 255 and frame[11] < 40

    def test_fixtures_without_a_band_are_left_alone(self):
        plan = [ar.plan_fixture(WHITE, _abs(0))]
        assert ar.spectrum_frame(plan, {"low": 1.0}) == []


class TestEngineBands:
    def test_snapshot_and_state(self):
        engine = AudioEngine()
        engine._bands = (0.1, 0.5, 0.9)
        assert engine.bands_snapshot() == {"low": 0.1, "mid": 0.5, "high": 0.9}
        assert engine.get_state()["bands"] == {"low": 0.1, "mid": 0.5, "high": 0.9}


class TestBandsConfig:
    def test_valid_mapping(self):
        settings, err = app._audio_react_config({"effect": "spectrum", "bands": {"low": ["back"], "high": [3, 4], "mid": []}})
        assert err is None
        assert settings["bands"] == {"low": ["back"], "high": [3, 4]}

    @pytest.mark.parametrize("bands", [
        ["low"], {"bass": [1]}, {"low": "back"}, {"low": [1.5]}, {"low": [True]},
    ])
    def test_rejected(self, bands):
        assert app._audio_react_config({"effect": "spectrum", "bands": bands})[1]

    def test_empty_means_automatic(self):
        assert app._audio_react_config({"effect": "spectrum", "bands": {}})[0]["bands"] is None


class TestBandOf:
    FIXTURES = [{"id": 0}, {"id": 1}, {"id": 3}, {"id": 4}]

    def test_automatic_uses_stage_positions(self, monkeypatch):
        monkeypatch.setattr(app, "_load_stage_layout", lambda: {"positions": {
            "0": {"x": 4}, "1": {"x": 3}, "3": {"x": 2}, "4": {"x": 1}}})
        band_of = app._spectrum_band_of(self.FIXTURES, None)
        assert band_of[4] == "low" and band_of[0] == "high"

    def test_explicit_groups_and_ids(self, monkeypatch):
        monkeypatch.setattr(app, "_target_fixtures", lambda groups: [{"id": 0}, {"id": 1}] if groups == ["back"] else [])
        band_of = app._spectrum_band_of(self.FIXTURES, {"low": ["back"], "high": [4, 99]})
        assert band_of == {0: "low", 1: "low", 4: "high"}   # 3 sits out; 99 isn't a fixture

    def test_first_band_wins_for_a_fixture_named_twice(self, monkeypatch):
        monkeypatch.setattr(app, "_target_fixtures", lambda groups: [])
        assert app._spectrum_band_of(self.FIXTURES, {"low": [1], "high": [1]}) == {1: "low"}


class _Engine:
    def __init__(self):
        self.bands = {"low": 0.0, "mid": 0.0, "high": 0.0}

    def onset_snapshot(self):
        return (0, "")

    def level_snapshot(self):
        return 0.0

    def bands_snapshot(self):
        return dict(self.bands)


def test_runner_drives_each_band_on_its_own_fixtures(monkeypatch):
    plan = [
        {**ar.plan_fixture(WHITE, _abs(0)), "band": "low"},
        {**ar.plan_fixture(WHITE, _abs(10)), "band": "high"},
    ]
    cfg = {"effect": "spectrum", "react_to": "any", "decay_ms": 250, "plan": plan, "base": {}}
    engine = _Engine()
    sent = []
    monkeypatch.setattr(app, "_audio_engine", engine)
    monkeypatch.setattr(app, "AUDIO_REACT_FRAME_S", 0)

    async def send(commands):
        sent.append(sorted(commands))
    monkeypatch.setattr(app, "_qlc_send_commands", send)
    monkeypatch.setattr(app, "_audio_react", cfg)

    count = {"n": 0}
    real_sleep = asyncio.sleep

    async def sleep(_):
        count["n"] += 1
        if count["n"] == 3:
            engine.bands = {"low": 1.0, "mid": 0.0, "high": 0.0}   # a kick
        if count["n"] >= 6:
            app._audio_react = None
        await real_sleep(0)
    monkeypatch.setattr(app.asyncio, "sleep", sleep)
    asyncio.run(app._run_audio_react_async(cfg))

    floor = ar.quantize(round(ar.SPECTRUM_FLOOR * 255))
    assert sent[0] == sorted([f"CH|1|{floor}", f"CH|11|{floor}"])
    assert sent[1] == ["CH|1|255"]                  # only the low fixture moved
    assert len(sent) == 2


@pytest.mark.parametrize("freq,band", [(60, "low"), (800, "mid"), (5000, "high")])
def test_capture_loop_band_maths_puts_a_tone_in_its_band(freq, band):
    """The numpy path the capture loop uses: Hann window, rfft, slice sums."""
    np = pytest.importorskip("numpy")
    sr, n = 44100, 2048
    t = np.arange(n) / sr
    signal = 0.5 * np.sin(2 * np.pi * freq * t).astype("float32")
    spectrum = np.abs(np.fft.rfft(signal * np.hanning(n)))
    amps = {b: float(np.sqrt(np.sum(spectrum[a:z] ** 2))) / (n / 4)
            for b, (a, z) in ar.band_bins(sr, n).items()}
    assert max(amps, key=amps.get) == band
    assert amps[band] == pytest.approx(0.5, rel=0.35)    # scaled to about the tone's amplitude
