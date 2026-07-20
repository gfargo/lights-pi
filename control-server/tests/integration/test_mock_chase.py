"""Regression test for OSS-885 / lights-pi#65.

``_mock_chase_run`` steps the mock DMX bus on the shared ``_qlc_loop``.
When a step's fade_in + hold timing is zero (absent/non-numeric Hold
defaults to 0, or an explicit ``Hold="0"`` strobe chase), the loop body
must still ``await`` at least once per iteration — otherwise the coroutine
never yields and it busy-spins the loop forever, blocking every other
lighting operation dispatched to it (set_channel_values, blackout, other
chases, and even ``task.cancel()`` for the chase itself).

This test runs the real stepper coroutine on a single event loop alongside
a concurrent sleep. If the stepper doesn't yield, the concurrent sleep
never gets scheduled and the outer ``asyncio.wait_for`` times out — exactly
reproducing the production deadlock without needing a background thread.
"""
import asyncio


def test_zero_timing_step_does_not_block_event_loop():
    import app as app_module

    chase_info = {
        "steps": [{"scene_id": None, "hold_ms": 0, "fade_in_ms": 0}],
        "speed": {"hold_ms": 0, "fade_in_ms": 0},
        "run_order": "Loop",
    }

    async def drive():
        task = asyncio.create_task(
            app_module._mock_chase_run(999999, chase_info)
        )
        # If the stepper never yields, this sleep (scheduled on the same
        # loop) never resumes and the outer wait_for below times out.
        await asyncio.sleep(0.1)
        assert not task.done()  # still looping (run_order == "Loop")

        task.cancel()
        await task  # _mock_chase_run swallows CancelledError internally

    asyncio.run(asyncio.wait_for(drive(), timeout=2.0))


def test_single_shot_zero_timing_chase_completes():
    """A SingleShot chase with zero timing must still finish, not hang."""
    import app as app_module

    chase_info = {
        "steps": [
            {"scene_id": None, "hold_ms": 0, "fade_in_ms": 0},
            {"scene_id": None, "hold_ms": 0, "fade_in_ms": 0},
        ],
        "speed": {"hold_ms": 0, "fade_in_ms": 0},
        "run_order": "SingleShot",
    }

    asyncio.run(asyncio.wait_for(
        app_module._mock_chase_run(999998, chase_info), timeout=2.0
    ))

    assert 999998 not in app_module._mock_chase_tasks
