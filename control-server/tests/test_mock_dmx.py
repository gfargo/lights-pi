"""Tests for the mock_dmx module."""
import itertools
import os
import random
import sys
import threading
import time
from pathlib import Path

import pytest

# Ensure control-server/ is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mock_dmx


@pytest.fixture(autouse=True)
def _reset_bus():
    """Clear the mock bus before and after each test."""
    mock_dmx.reset()
    yield
    mock_dmx.reset()


# ---------------------------------------------------------------------------
# apply_commands
# ---------------------------------------------------------------------------

class TestApplyCommands:
    def test_single_command(self):
        mock_dmx.apply_commands(["CH|1|255"])
        # abs 1 → divmod(0, 512) = (0, 0)
        assert mock_dmx._BUS == {(0, 0): 255}

    def test_second_universe(self):
        # abs 513 → divmod(512, 512) = (1, 0)
        mock_dmx.apply_commands(["CH|513|128"])
        assert mock_dmx._BUS == {(1, 0): 128}

    def test_multiple_commands(self):
        mock_dmx.apply_commands(["CH|1|255", "CH|513|128"])
        assert mock_dmx._BUS[(0, 0)] == 255
        assert mock_dmx._BUS[(1, 0)] == 128

    def test_value_clamped_high(self):
        mock_dmx.apply_commands(["CH|1|999"])
        assert mock_dmx._BUS[(0, 0)] == 255

    def test_value_clamped_low(self):
        mock_dmx.apply_commands(["CH|1|-10"])
        assert mock_dmx._BUS[(0, 0)] == 0

    def test_non_ch_commands_ignored(self):
        mock_dmx.apply_commands(["QLC+API|setFunctionStatus|1|1", "CH|1|100"])
        assert mock_dmx._BUS == {(0, 0): 100}

    def test_malformed_ignored(self):
        mock_dmx.apply_commands(["notacommand", "CH|abc|255", "CH|1|xyz"])
        assert mock_dmx._BUS == {}

    def test_abs_zero_ignored(self):
        # abs 0 is invalid (1-based), shouldn't write
        mock_dmx.apply_commands(["CH|0|100"])
        assert mock_dmx._BUS == {}

    def test_overwrite(self):
        mock_dmx.apply_commands(["CH|1|100"])
        mock_dmx.apply_commands(["CH|1|200"])
        assert mock_dmx._BUS[(0, 0)] == 200


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------

class TestSnapshot:
    def test_empty(self):
        assert mock_dmx.snapshot() == {}

    def test_string_keys(self):
        mock_dmx.apply_commands(["CH|1|255", "CH|513|128"])
        s = mock_dmx.snapshot()
        assert s["0/0"] == 255
        assert s["1/0"] == 128

    def test_sorted(self):
        mock_dmx.apply_commands(["CH|3|30", "CH|1|10", "CH|2|20"])
        keys = list(mock_dmx.snapshot().keys())
        assert keys == sorted(keys)

    def test_concurrent_writes_and_reads_dont_raise(self):
        """Regression: snapshot() must not race with apply_commands() inserting
        new keys from another thread (RuntimeError: dict changed size during
        iteration)."""
        stop = threading.Event()
        errors = []

        def writer():
            ch = 1
            while not stop.is_set():
                mock_dmx.apply_commands([f"CH|{ch}|100"])
                ch = (ch % 500) + 1

        def reader():
            try:
                while not stop.is_set():
                    mock_dmx.snapshot()
            except Exception as e:  # pragma: no cover - failure path
                errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(2)]
        threads += [threading.Thread(target=reader) for _ in range(2)]
        for t in threads:
            t.start()
        time.sleep(0.3)
        stop.set()
        for t in threads:
            t.join(timeout=2)
        assert errors == []


# ---------------------------------------------------------------------------
# serialize_get_channels_values — round-trip with _fetch_channel_values logic
# ---------------------------------------------------------------------------

class TestSerializeGetChannelsValues:
    def _parse_like_app(self, msg: str) -> dict:
        """Mirror the parsing logic in _fetch_channel_values."""
        values = {}
        parts = msg.split("|")
        for i in range(2, len(parts) - 1, 3):
            try:
                values[int(parts[i])] = int(parts[i + 1])
            except (ValueError, IndexError):
                continue
        return values

    def test_empty_bus(self):
        msg = mock_dmx.serialize_get_channels_values(4)
        parsed = self._parse_like_app(msg)
        assert parsed[1] == 0
        assert parsed[2] == 0
        assert parsed[3] == 0
        assert parsed[4] == 0

    def test_round_trip(self):
        mock_dmx.apply_commands(["CH|1|200", "CH|2|100"])
        msg = mock_dmx.serialize_get_channels_values(4)
        parsed = self._parse_like_app(msg)
        assert parsed[1] == 200
        assert parsed[2] == 100
        assert parsed[3] == 0
        assert parsed[4] == 0

    def test_starts_with_header(self):
        msg = mock_dmx.serialize_get_channels_values(2)
        assert msg.startswith("QLC+API|getChannelsValues|")


# ---------------------------------------------------------------------------
# MockQLCWebSocket
# ---------------------------------------------------------------------------

class TestMockQLCWebSocket:
    # asyncio.run(), not get_event_loop().run_until_complete(): implicit
    # loop creation outside a running loop is gone in Python 3.12+.
    def test_send_ch_updates_bus(self):
        import asyncio
        ws = mock_dmx.MockQLCWebSocket()
        asyncio.run(ws.send("CH|1|255"))
        assert mock_dmx._BUS[(0, 0)] == 255

    def test_send_non_ch_ignored(self):
        import asyncio
        ws = mock_dmx.MockQLCWebSocket()
        asyncio.run(ws.send("QLC+API|setFunctionStatus|1|1"))
        assert mock_dmx._BUS == {}

    def test_close_sets_closed(self):
        import asyncio
        ws = mock_dmx.MockQLCWebSocket()
        assert ws.closed is False
        asyncio.run(ws.close())
        assert ws.closed is True


# ---------------------------------------------------------------------------
# _chase_index_sequence — run_order fidelity (pure helper, app.py)
# ---------------------------------------------------------------------------

class TestChaseIndexSequence:
    def _seq(self, n, run_order, count):
        import app as _app_module
        return list(itertools.islice(_app_module._chase_index_sequence(n, run_order), count))

    def test_loop_repeats(self):
        assert self._seq(3, "Loop", 7) == [0, 1, 2, 0, 1, 2, 0]

    def test_single_shot_runs_once_then_stops(self):
        import app as _app_module
        assert list(_app_module._chase_index_sequence(3, "SingleShot")) == [0, 1, 2]

    def test_ping_pong_bounces(self):
        # n=3 → forward 0,1,2 then back through the middle only: 1
        assert self._seq(3, "PingPong", 8) == [0, 1, 2, 1, 0, 1, 2, 1]

    def test_random_stays_in_range(self):
        random.seed(0)
        import app as _app_module
        picks = list(itertools.islice(_app_module._chase_index_sequence(5, "Random"), 50))
        assert all(0 <= p < 5 for p in picks)

    def test_zero_steps_yields_nothing(self):
        assert self._seq(0, "Loop", 5) == []

    def test_unknown_run_order_falls_back_to_loop(self):
        assert self._seq(2, "Bogus", 5) == [0, 1, 0, 1, 0]


# ---------------------------------------------------------------------------
# Flask integration tests — MOCK_DMX=1
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def mock_client():
    """Flask test client with MOCK_DMX=1 and sample workspace."""
    sample_ws = str(Path(__file__).resolve().parent / "fixtures" / "sample.qxw")
    os.environ["MOCK_DMX"] = "1"
    os.environ["QLC_WORKSPACE"] = sample_ws
    # Import app AFTER setting env vars (module-level config reads them)
    import importlib

    import app as _app_module
    importlib.reload(_app_module)
    _app_module.app.config["TESTING"] = True
    with _app_module.app.test_client() as client:
        yield client
    # Cleanup — and reload once more with the env cleared, so later test
    # files see the app module in its normal (non-mock) configuration.
    os.environ.pop("MOCK_DMX", None)
    os.environ.pop("QLC_WORKSPACE", None)
    importlib.reload(_app_module)
    mock_dmx.reset()


class TestFlaskMockIntegration:
    def test_status_shows_mock(self, mock_client):
        r = mock_client.get("/api/status")
        assert r.status_code == 200
        data = r.get_json()
        assert "mock" in data["services"]["qlc_ws"]["detail"]

    def test_fixtures_loaded_from_sample(self, mock_client):
        r = mock_client.get("/api/fixtures")
        assert r.status_code == 200
        fixtures = r.get_json()["fixtures"]
        assert len(fixtures) == 5

    def test_debug_dmx_state_available(self, mock_client):
        r = mock_client.get("/debug/dmx-state")
        assert r.status_code == 200
        assert isinstance(r.get_json(), dict)

    def test_blackout_updates_bus(self, mock_client):
        # First put some values in the bus
        mock_dmx.apply_commands(["CH|1|200", "CH|2|100"])
        r = mock_client.post("/api/blackout")
        assert r.status_code == 200
        # After blackout all channels should be 0
        r2 = mock_client.get("/debug/dmx-state")
        state = r2.get_json()
        assert all(v == 0 for v in state.values())

    def test_activate_scene_updates_bus(self, mock_client):
        r = mock_client.post("/api/scenes/1/activate")
        assert r.status_code == 200
        r2 = mock_client.get("/debug/dmx-state")
        state = r2.get_json()
        # Scene 1 ("Lights ON") sets channels — bus should be non-empty
        assert len(state) > 0

    def test_chase_advances_mock_bus(self, mock_client):
        """Chase stepper must write to the bus (was broken by .items() on a list)."""
        import time

        mock_dmx.reset()
        # Start the test chase (ID 100, two 100 ms steps)
        r = mock_client.post("/api/chases/100/start")
        assert r.status_code == 200
        # Wait long enough for at least one step to fire (hold=100ms, add buffer)
        time.sleep(0.5)
        state = mock_client.get("/debug/dmx-state").get_json()
        # Stop the chase before asserting so it doesn't race with cleanup
        mock_client.post("/api/chases/100/stop")
        assert len(state) > 0, "Chase stepper never wrote to the mock DMX bus"

    def test_restart_then_stop_leaves_no_orphan_stepper(self, mock_client):
        """Regression: restarting a chase must not let the old task's cleanup
        pop the newly-registered task, leaving an unstoppable orphan stepper."""
        import app as _app_module

        mock_dmx.reset()
        r1 = mock_client.post("/api/chases/100/start")
        assert r1.status_code == 200
        r2 = mock_client.post("/api/chases/100/start")  # restart
        assert r2.status_code == 200
        r3 = mock_client.post("/api/chases/100/stop")
        assert r3.status_code == 200

        assert 100 not in _app_module._mock_chase_tasks

        state1 = mock_dmx.snapshot()
        time.sleep(0.3)
        state2 = mock_dmx.snapshot()
        assert state1 == state2, "Bus kept changing after stop — an orphan stepper is still running"

    def test_start_empty_chase_reports_failure(self, mock_client):
        """Chase 101 has zero steps; starting it must not report success."""
        r = mock_client.post("/api/chases/101/start")
        data = r.get_json()
        assert data["success"] is False


class TestDebugEndpointNotMounted:
    """Verify /debug/dmx-state returns 404 when not in mock mode."""

    def test_not_mounted_without_mock(self):
        # Reload app without MOCK_DMX set
        os.environ.pop("MOCK_DMX", None)
        # Only need to verify route isn't registered; use a fresh import context
        import importlib

        import app as _app_module
        importlib.reload(_app_module)
        _app_module.app.config["TESTING"] = True
        with _app_module.app.test_client() as c:
            r = c.get("/debug/dmx-state")
        assert r.status_code == 404
