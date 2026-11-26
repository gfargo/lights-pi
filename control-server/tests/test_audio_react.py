"""Tests for audio_react.py (pure effect maths) and the effect wiring in app.py."""
import asyncio
import math
import types

import app
import audio_react as ar
import pytest
from audio_engine import AudioEngine

# ---------------------------------------------------------------------------
# Hit classification
# ---------------------------------------------------------------------------


def _spectrum(sr, n, peaks):
    """Magnitudes with energy only at the given frequencies (Hz -> magnitude)."""
    mags = [0.0] * (n // 2 + 1)
    for freq, mag in peaks.items():
        mags[int(round(freq * n / sr))] = mag
    return mags


class TestBands:
    def test_energy_lands_in_the_right_band(self):
        low, mid, high = ar.band_energies(_spectrum(44100, 2048, {60: 3.0, 500: 2.0, 4000: 1.0}), 44100, 2048)
        assert (low, mid, high) == pytest.approx((9.0, 4.0, 1.0))

    def test_out_of_band_ignored(self):
        assert ar.band_energies(_spectrum(44100, 2048, {10: 5.0, 12000: 5.0}), 44100, 2048) == (0, 0, 0)


class TestClassifyHit:
    @pytest.mark.parametrize("bands,kind", [
        ((9, 2, 1), "kick"),
        ((1, 4, 5), "snare"),
        ((1, 8, 1), "other"),
        ((3, 2, 5), "other"),      # bright but with real low end: not a clean snare
        ((0, 0, 0), "other"),
    ])
    def test_kinds(self, bands, kind):
        assert ar.classify_hit(*bands) == kind

    @pytest.mark.parametrize("react_to,kind,expected", [
        ("any", "other", True), ("any", "kick", True),
        ("kick", "kick", True), ("kick", "snare", False),
        ("snare", "snare", True), (None, "kick", False), ("", "kick", False),
    ])
    def test_hit_matches(self, react_to, kind, expected):
        assert ar.hit_matches(react_to, kind) is expected

    def test_classifies_a_synthetic_kick_and_snare(self):
        np = pytest.importorskip("numpy")
        sr, n = 44100, 2048
        t = np.arange(n) / sr
        win = np.hanning(n)
        kick = np.sin(2 * np.pi * 55 * t) * np.exp(-t * 30)
        rng = np.random.default_rng(1)
        hiss = rng.standard_normal(n)
        snare = np.real(np.fft.irfft(np.fft.rfft(hiss) * (np.fft.rfftfreq(n, 1 / sr) > 2500)))
        for signal, kind in ((kick, "kick"), (snare, "snare")):
            mags = np.abs(np.fft.rfft(signal * win))
            assert ar.classify_hit(*ar.band_energies(mags, sr, n)) == kind


# ---------------------------------------------------------------------------
# Loudness and curves
# ---------------------------------------------------------------------------

class TestLevelTracker:
    def test_silence_is_zero(self):
        t = ar.LevelTracker()
        for _ in range(50):
            assert t.update(0.0) == 0.0

    def test_below_gate_is_zero(self):
        t = ar.LevelTracker()
        for _ in range(50):
            level = t.update(0.01, gate=0.02)
        assert level == 0.0

    def test_steady_signal_reaches_full_then_eases_down(self):
        t = ar.LevelTracker()
        for _ in range(40):
            loud = t.update(0.2)
        assert loud == pytest.approx(1.0, abs=0.01)
        quiet = [t.update(0.05) for _ in range(5)]
        assert quiet[0] > quiet[-1] > 0          # falls gradually, not instantly

    def test_auto_gain_uses_full_range_for_quiet_rooms(self):
        t = ar.LevelTracker()
        for _ in range(40):
            level = t.update(0.015)
        assert level > 0.9

    def test_level_bounded(self):
        t = ar.LevelTracker()
        for rms in (0.5, 5.0, -1.0, 0.0):
            assert 0.0 <= t.update(rms) <= 1.0


class TestCurves:
    @pytest.mark.parametrize("elapsed,expected", [(0, 1.0), (125, 0.25), (250, 0.0), (400, 0.0), (-5, 0.0)])
    def test_flash_envelope(self, elapsed, expected):
        assert ar.flash_envelope(elapsed, 250) == pytest.approx(expected)

    def test_flash_envelope_zero_decay(self):
        assert ar.flash_envelope(0, 0) == 0.0

    def test_envelope_is_monotonic(self):
        vals = [ar.flash_envelope(ms, 300) for ms in range(0, 301, 10)]
        assert all(a >= b for a, b in zip(vals, vals[1:], strict=False))

    @pytest.mark.parametrize("base,peak,amount,out", [(40, 255, 0, 40), (40, 255, 1, 255), (0, 200, 0.5, 100), (40, 255, 2, 255)])
    def test_mix(self, base, peak, amount, out):
        assert ar.mix(base, peak, amount) == out

    def test_amplitude_colour_walks_blue_to_amber(self):
        quiet, loud = ar.amplitude_color(0.0), ar.amplitude_color(1.0)
        assert quiet[2] > quiet[0] and quiet[1] == 0         # blue
        assert loud[0] == 255 and loud[1] > loud[2]          # amber
        assert max(quiet) < max(loud)                         # louder is brighter
        assert ar.amplitude_color(5) == loud and ar.amplitude_color(-1) == quiet

    @pytest.mark.parametrize("v,out", [(0, 0), (255, 255), (1, 0), (2, 4), (130, 132), (254, 255), (300, 255)])
    def test_quantize(self, v, out):
        assert ar.quantize(v) == out


# ---------------------------------------------------------------------------
# Plans and frames
# ---------------------------------------------------------------------------

def _abs(base):
    return lambda off: base + off + 1


class TestFrames:
    RGB_PAR = {"red": 0, "green": 1, "blue": 2, "brightness": [0, 1, 2]}
    RGBA_DIM = {"dimmer": 0, "red": 1, "green": 2, "blue": 3, "amber": 4, "brightness": [0]}
    WWA = {"dimmer": 0, "warm": 1, "cool": 2, "brightness": [0]}

    def test_plan(self):
        assert ar.plan_fixture(self.RGB_PAR, _abs(7)) == {"brightness": [8, 9, 10], "dimmer": None, "rgb": (8, 9, 10)}
        assert ar.plan_fixture(self.WWA, _abs(0)) == {"brightness": [1], "dimmer": 1, "rgb": None}

    def test_flash_frame_rises_from_base(self):
        plan = [ar.plan_fixture(self.RGBA_DIM, _abs(0))]
        assert ar.flash_frame(plan, {1: 40}, 0.0) == [(1, 40)]
        assert ar.flash_frame(plan, {1: 40}, 1.0) == [(1, 255)]
        assert ar.flash_frame(plan, {}, 0.5) == [(1, 128)]

    def test_amplitude_frame(self):
        plan = [ar.plan_fixture(self.RGBA_DIM, _abs(0)), ar.plan_fixture(self.WWA, _abs(10))]
        frame = dict(ar.amplitude_frame(plan, 1.0))
        assert frame[1] == 255                       # dimmer opened on the RGB fixture
        r, g, b = ar.amplitude_color(1.0)
        assert (frame[2], frame[3], frame[4]) == (ar.quantize(r), ar.quantize(g), ar.quantize(b))
        assert frame[11] == 255                      # WWA fixture: brightness follows level
        assert dict(ar.amplitude_frame(plan, 0.0))[11] == ar.quantize(64)

    def test_changed_values(self):
        assert ar.changed_values([(1, 5), (2, 6)], {1: 5}) == [(2, 6)]


# ---------------------------------------------------------------------------
# Engine wiring
# ---------------------------------------------------------------------------

class TestEngineWiring:
    def test_dispatch_hook_receives_every_subscriber_call(self):
        engine = AudioEngine()
        seen, delivered = [], []
        engine.set_dispatch(lambda cb, ev: delivered.append(ev) or cb(ev))
        engine.subscribe(seen.append)
        engine._notify({"type": "level", "level": 0.5})
        assert seen == delivered == [{"type": "level", "level": 0.5}]

    def test_use_threading_swaps_the_lock(self):
        engine = AudioEngine()
        fake = types.SimpleNamespace(Lock=lambda: "native-lock", Thread=None)
        engine.use_threading(fake)
        assert engine._lock == "native-lock" and engine._threading is fake

    def test_snapshots_and_state(self):
        engine = AudioEngine()
        engine._last_onset_ms, engine._last_onset_kind, engine._level = 1234, "kick", 0.4567
        assert engine.onset_snapshot() == (1234, "kick")
        assert engine.level_snapshot() == 0.4567
        state = engine.get_state()
        assert state["last_onset_kind"] == "kick" and state["level"] == 0.457

    def test_set_sensitivity(self):
        engine = AudioEngine()
        engine.set_sensitivity(0.05)
        assert engine.get_state()["sensitivity"] == 0.05


# ---------------------------------------------------------------------------
# app.py: settings, base tracking, the runner
# ---------------------------------------------------------------------------

class TestReactConfig:
    def test_defaults_and_legacy_react_to(self):
        assert app._audio_react_config({}) == ({"effect": None, "react_to": "any", "decay_ms": 250, "groups": None}, None)
        assert app._audio_react_config({"react_to": "kick"})[0]["effect"] == "flash"
        assert app._audio_react_config({"effect": "off"})[0]["effect"] is None

    def test_validation(self):
        assert app._audio_react_config({"effect": "strobe"})[1]
        assert app._audio_react_config({"effect": "flash", "react_to": "hat"})[1]
        assert app._audio_react_config({"effect": "flash", "decay_ms": "x"})[1]
        assert app._audio_react_config({"effect": "flash", "decay_ms": 99999})[0]["decay_ms"] == 2000
        assert app._audio_react_config({"effect": "amplitude", "groups": ["a", 3]})[0]["groups"] == ["a"]


class TestBaseTracking:
    def test_writes_to_controlled_channels_update_base(self, monkeypatch):
        cfg = {"base": {1: 10, 2: 20}}
        monkeypatch.setattr(app, "_audio_react", cfg)
        app._note_audio_react_base([(1, 200), ("2", 300), (9, 50), ("x", 1)])
        assert cfg["base"] == {1: 200, 2: 255}

    def test_no_effect_is_a_no_op(self, monkeypatch):
        monkeypatch.setattr(app, "_audio_react", None)
        app._note_audio_react_base([(1, 200)])


class FakeEngine:
    def __init__(self):
        self.onset = (0, "")
        self.level = 0.0

    def onset_snapshot(self):
        return self.onset

    def level_snapshot(self):
        return self.level


class TestRunner:
    def _run(self, monkeypatch, cfg, script, frames=12):
        """Run the effect loop for *frames* frames, calling script(i, engine) before each."""
        engine = FakeEngine()
        sent = []
        clock = {"ms": 1_000_000.0}
        monkeypatch.setattr(app, "_audio_engine", engine)
        monkeypatch.setattr(app.time, "time", lambda: clock["ms"] / 1000)
        monkeypatch.setattr(app, "AUDIO_REACT_FRAME_S", 0)

        async def send(commands):
            sent.append(list(commands))
        monkeypatch.setattr(app, "_qlc_send_commands", send)
        monkeypatch.setattr(app, "_audio_react", cfg)

        count = {"n": 0}
        real_sleep = asyncio.sleep

        async def sleep(_):
            count["n"] += 1
            clock["ms"] += 30
            if count["n"] >= frames:
                app._audio_react = None
            script(count["n"], engine, clock["ms"])
            await real_sleep(0)
        monkeypatch.setattr(app.asyncio, "sleep", sleep)
        asyncio.run(app._run_audio_react_async(cfg))
        return sent

    def test_flash_rises_on_a_hit_and_fades_back(self, monkeypatch):
        plan = [ar.plan_fixture({"dimmer": 0, "brightness": [0]}, _abs(0))]
        cfg = {"effect": "flash", "react_to": "any", "decay_ms": 150, "plan": plan, "base": {1: 40}}

        def script(i, engine, now):
            if i == 2:
                engine.onset = (now, "other")
        sent = self._run(monkeypatch, cfg, script)
        values = [int(c[0].split("|")[2]) for c in sent]
        assert values[0] == 40                      # first frame: the look underneath
        assert values[1] == 255                     # the hit
        assert values[-1] == 40                     # faded back
        assert values[1:] == sorted(values[1:], reverse=True)

    def test_kick_only_ignores_snares(self, monkeypatch):
        plan = [ar.plan_fixture({"dimmer": 0, "brightness": [0]}, _abs(0))]
        cfg = {"effect": "flash", "react_to": "kick", "decay_ms": 150, "plan": plan, "base": {1: 0}}

        def script(i, engine, now):
            if i == 2:
                engine.onset = (now, "snare")
        sent = self._run(monkeypatch, cfg, script)
        assert sent == [["CH|1|0"]]                 # only the initial frame

    def test_amplitude_only_sends_changes(self, monkeypatch):
        plan = [ar.plan_fixture({"red": 0, "green": 1, "blue": 2, "brightness": [0, 1, 2]}, _abs(0))]
        cfg = {"effect": "amplitude", "react_to": "any", "decay_ms": 250, "plan": plan, "base": {}}

        def script(i, engine, now):
            engine.level = 1.0 if i >= 5 else 0.0
        sent = self._run(monkeypatch, cfg, script)
        assert len(sent) == 2                       # quiet frame, then the loud frame
        r, g, b = (ar.quantize(v) for v in ar.amplitude_color(1.0))
        assert sorted(sent[1]) == sorted([f"CH|1|{r}", f"CH|2|{g}", f"CH|3|{b}"])


class TestRoutes:
    @pytest.fixture
    def client(self, monkeypatch):
        calls = {"start": [], "stop": []}
        engine = types.SimpleNamespace(
            available=True,
            state={"available": True, "enabled": False, "bpm": 0.0, "sensitivity": 0.02},
        )
        engine.get_state = lambda: dict(engine.state)
        engine.start = lambda device=None, sensitivity=None: calls["start"].append((device, sensitivity)) or engine.state.update(enabled=True) or True
        engine.stop = lambda: engine.state.update(enabled=False)
        engine.subscribe = engine.unsubscribe = lambda cb: None
        engine.set_sensitivity = lambda v: engine.state.update(sensitivity=v)
        monkeypatch.setattr(app, "_audio_engine", engine)
        monkeypatch.setattr(app, "_start_audio_react", lambda s: calls["start"].append(("react", s["effect"])) or {})
        monkeypatch.setattr(app, "_stop_audio_react", lambda restore=True: calls["stop"].append(restore))
        self.calls = calls
        return app.app.test_client()

    def test_enable_with_effect(self, client):
        resp = client.post("/api/audio/enable", json={"effect": "amplitude"})
        assert resp.status_code == 200
        assert self.calls["start"] == [(None, None), ("react", "amplitude")]

    def test_changing_effect_does_not_restart_capture(self, client):
        client.post("/api/audio/enable", json={"effect": "flash"})
        client.post("/api/audio/enable", json={"effect": "amplitude"})
        assert [c for c in self.calls["start"] if c[0] != "react"] == [(None, None)]

    def test_enable_without_effect_stops_it(self, client):
        client.post("/api/audio/enable", json={})
        assert self.calls["stop"] == [True]

    def test_bad_effect(self, client):
        assert client.post("/api/audio/enable", json={"effect": "laser"}).status_code == 400

    def test_disable_restores(self, client):
        assert client.post("/api/audio/disable").get_json()["react"] is None
        assert self.calls["stop"] == [True]

    @pytest.mark.parametrize("body,code", [({"sensitivity": 0.05}, 200), ({"sensitivity": "x"}, 400), ({"sensitivity": 3}, 400), ({}, 400)])
    def test_sensitivity(self, client, body, code):
        assert client.post("/api/audio/sensitivity", json=body).status_code == code


def test_flash_envelope_matches_quadratic():
    assert ar.flash_envelope(50, 200) == pytest.approx(math.pow(0.75, 2))
