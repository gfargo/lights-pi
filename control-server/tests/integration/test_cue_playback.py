"""Suite 3 — Cue list playback engine tests with injectable fake clock.

``_run_cue_list_async`` accepts ``now`` and ``sleep`` keyword arguments so
tests can drive time without real wall-clock delays.

What's covered:
  - Out-of-order cues are sorted by at_ms before firing
  - Each cue fires at approximately its at_ms (verified via fake clock)
  - A cue that raises does not abort remaining cues (fault-tolerant engine)
  - asyncio.CancelledError propagates cleanly (stop_cue_list path)
  - The _active_cue_lists registry is cleaned up on completion and cancellation

What's out of scope here:
  - pause / resume / seek — the engine has no such feature (deferred, see
    https://github.com/gfargo/lights-pi/issues/24 follow-up)
"""
import asyncio

import pytest


def _make_fake_clock():
    """Return (fake_now, fake_sleep) sharing a virtual clock (seconds)."""
    virtual = [0.0]

    def fake_now():
        return virtual[0]

    async def fake_sleep(seconds):
        virtual[0] += seconds

    return fake_now, fake_sleep, virtual


# ---------------------------------------------------------------------------
# Ordering and timing
# ---------------------------------------------------------------------------

class TestCueOrdering:
    def test_out_of_order_input_is_sorted(self, monkeypatch):
        """Cues given in reverse at_ms order fire in chronological order."""
        import app as app_module

        fired_actions = []
        monkeypatch.setattr(
            app_module, "execute_lighting_action",
            lambda action_data, target_groups=None, source=None: fired_actions.append(action_data["action"]),
        )

        fake_now, fake_sleep, _ = _make_fake_clock()
        cues = [
            {"at_ms": 3000, "action": "c", "parameters": {}},
            {"at_ms": 1000, "action": "a", "parameters": {}},
            {"at_ms": 2000, "action": "b", "parameters": {}},
        ]

        asyncio.run(app_module._run_cue_list_async(
            1, cues, now=fake_now, sleep=fake_sleep,
        ))

        assert fired_actions == ["a", "b", "c"]

    def test_cues_fire_at_correct_virtual_time(self, monkeypatch):
        """Each cue fires when the virtual clock reaches its at_ms."""
        import app as app_module

        fired_at: list[tuple[str, int]] = []  # (action, virtual_ms_when_fired)

        fake_now, fake_sleep, virtual = _make_fake_clock()

        def record_execute(action_data, target_groups=None, source=None):
            fired_at.append((action_data["action"], round(virtual[0] * 1000)))

        monkeypatch.setattr(app_module, "execute_lighting_action", record_execute)

        cues = [
            {"at_ms": 500,  "action": "a", "parameters": {}},
            {"at_ms": 2000, "action": "b", "parameters": {}},
            {"at_ms": 3500, "action": "c", "parameters": {}},
        ]

        asyncio.run(app_module._run_cue_list_async(
            2, cues, now=fake_now, sleep=fake_sleep,
        ))

        assert len(fired_at) == 3
        assert fired_at[0] == ("a",  500)
        assert fired_at[1] == ("b", 2000)
        assert fired_at[2] == ("c", 3500)

    def test_zero_at_ms_fires_immediately(self, monkeypatch):
        """A cue at at_ms=0 fires without any sleep."""
        import app as app_module

        sleep_calls: list[float] = []
        fired_actions: list[str] = []

        fake_now, _, _ = _make_fake_clock()

        async def tracking_sleep(seconds):
            sleep_calls.append(seconds)

        monkeypatch.setattr(
            app_module, "execute_lighting_action",
            lambda action_data, target_groups=None, source=None: fired_actions.append(action_data["action"]),
        )

        cues = [{"at_ms": 0, "action": "instant", "parameters": {}}]

        asyncio.run(app_module._run_cue_list_async(
            3, cues, now=fake_now, sleep=tracking_sleep,
        ))

        assert fired_actions == ["instant"]
        assert sleep_calls == []  # no sleep needed for at_ms == 0


# ---------------------------------------------------------------------------
# Fault tolerance
# ---------------------------------------------------------------------------

class TestFaultTolerance:
    def test_failing_cue_does_not_abort_remaining(self, monkeypatch):
        """A cue that raises must not prevent subsequent cues from firing."""
        import app as app_module

        fired_actions: list[str] = []

        def selective_execute(action_data, target_groups=None, source=None):
            if action_data["action"] == "bad":
                raise RuntimeError("simulated cue failure")
            fired_actions.append(action_data["action"])

        monkeypatch.setattr(app_module, "execute_lighting_action", selective_execute)

        fake_now, fake_sleep, _ = _make_fake_clock()
        cues = [
            {"at_ms": 100, "action": "before", "parameters": {}},
            {"at_ms": 200, "action": "bad",    "parameters": {}},
            {"at_ms": 300, "action": "after",  "parameters": {}},
        ]

        asyncio.run(app_module._run_cue_list_async(
            10, cues, now=fake_now, sleep=fake_sleep,
        ))

        assert fired_actions == ["before", "after"]

    def test_all_cues_fire_when_first_raises(self, monkeypatch):
        """Even if the very first cue fails, later ones still run."""
        import app as app_module

        fired_actions: list[str] = []
        call_count = [0]

        def first_raises(action_data, target_groups=None, source=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise ValueError("first cue boom")
            fired_actions.append(action_data["action"])

        monkeypatch.setattr(app_module, "execute_lighting_action", first_raises)

        fake_now, fake_sleep, _ = _make_fake_clock()
        cues = [
            {"at_ms": 0,   "action": "x", "parameters": {}},
            {"at_ms": 100, "action": "y", "parameters": {}},
            {"at_ms": 200, "action": "z", "parameters": {}},
        ]

        asyncio.run(app_module._run_cue_list_async(
            11, cues, now=fake_now, sleep=fake_sleep,
        ))

        assert fired_actions == ["y", "z"]


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

class TestCancellation:
    def test_cancelled_error_propagates(self, monkeypatch):
        """CancelledError escapes the cue engine so the task can be stopped."""
        import app as app_module

        monkeypatch.setattr(
            app_module, "execute_lighting_action",
            lambda *a, **kw: None,
        )

        fake_now, _, _ = _make_fake_clock()

        async def instant_cancel(seconds):
            raise asyncio.CancelledError

        cues = [{"at_ms": 500, "action": "a", "parameters": {}}]

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(app_module._run_cue_list_async(
                20, cues, now=fake_now, sleep=instant_cancel,
            ))

    def test_registry_cleaned_up_after_cancellation(self, monkeypatch):
        """_active_cue_lists must not retain the entry after CancelledError."""
        import app as app_module

        monkeypatch.setattr(
            app_module, "execute_lighting_action",
            lambda *a, **kw: None,
        )

        fake_now, _, _ = _make_fake_clock()

        async def instant_cancel(seconds):
            raise asyncio.CancelledError

        cues = [{"at_ms": 500, "action": "a", "parameters": {}}]

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(app_module._run_cue_list_async(
                21, cues, now=fake_now, sleep=instant_cancel,
            ))

        assert 21 not in app_module._active_cue_lists

    def test_registry_cleaned_up_after_completion(self, monkeypatch):
        """_active_cue_lists is cleared once all cues have fired."""
        import app as app_module

        monkeypatch.setattr(
            app_module, "execute_lighting_action",
            lambda *a, **kw: None,
        )

        fake_now, fake_sleep, _ = _make_fake_clock()
        cues = [{"at_ms": 100, "action": "a", "parameters": {}}]

        asyncio.run(app_module._run_cue_list_async(
            30, cues, now=fake_now, sleep=fake_sleep,
        ))

        assert 30 not in app_module._active_cue_lists
