"""Tests for the eventlet bridge helpers used to run the QLC+ asyncio loop
safely under gunicorn's eventlet worker (issue #47).

These exercise the *real* eventlet.tpool / eventlet.patcher machinery rather
than mocking eventlet itself — only `app._eventlet_active()`'s "are we under
gunicorn" detection is faked via monkeypatch. We deliberately never call
`eventlet.monkey_patch()` here: it mutates process-wide stdlib module state
(swaps out `threading`, `socket`, etc. for every module that already holds a
reference to them) and cannot be undone within a process, so calling it
would leak into every other test file in this suite.
"""
import asyncio
import concurrent.futures
import sys
import threading
import time

import app
import pytest


@pytest.fixture(autouse=True)
def _reset_qlc_loop_globals():
    """_qlc_run() and the hub/loop helpers read module-level globals; make sure
    tests don't leak a loop/thread into each other or into unrelated tests."""
    yield
    loop = app._qlc_loop
    if loop is not None and loop.is_running():
        loop.call_soon_threadsafe(loop.stop)
    app._qlc_loop = None
    app._qlc_loop_thread = None
    app._qlc_loop_thread_ident = None
    app._qlc_consecutive_timeouts = 0
    app._qlc_loop_restarts = 0
    app._hub_pending.clear()


# ---------------------------------------------------------------------------
# _eventlet_active
# ---------------------------------------------------------------------------

class TestEventletActive:
    def test_false_when_not_monkey_patched(self):
        assert app._eventlet_active() is False

    def test_true_when_thread_monkey_patched(self, monkeypatch):
        import eventlet.patcher
        monkeypatch.setattr(
            eventlet.patcher, "is_monkey_patched",
            lambda name: name == "thread",
        )
        assert app._eventlet_active() is True

    def test_false_when_eventlet_not_importable(self, monkeypatch):
        # Setting a submodule to None in sys.modules is the standard way to
        # force `import x.y` to raise ImportError without uninstalling it.
        monkeypatch.setitem(sys.modules, "eventlet.patcher", None)
        assert app._eventlet_active() is False


# ---------------------------------------------------------------------------
# _native_threading_module
# ---------------------------------------------------------------------------

class TestNativeThreadingModule:
    def test_returns_stdlib_threading_when_not_eventlet(self, monkeypatch):
        monkeypatch.setattr(app, "_eventlet_active", lambda: False)
        assert app._native_threading_module() is threading

    def test_returns_original_when_eventlet_active(self, monkeypatch):
        monkeypatch.setattr(app, "_eventlet_active", lambda: True)
        native = app._native_threading_module()
        # eventlet.patcher.original() caches its result, so a second call
        # returns the same module object -- the point under test is that
        # _native_threading_module() defers to it instead of the
        # (possibly-patched) `threading` import in app.py.
        import eventlet.patcher
        assert native is eventlet.patcher.original("threading")
        # And it must still be a real, usable threading module.
        event = native.Event()
        event.set()
        assert event.is_set()


# ---------------------------------------------------------------------------
# _qlc_run — dispatch path (direct vs. eventlet.tpool.execute)
# ---------------------------------------------------------------------------

@pytest.fixture
def _running_qlc_loop():
    """A real asyncio loop on a background thread, wired into app's globals
    the same way _start_qlc_loop_sync() would, without going through the
    QLC+ WebSocket connection itself."""
    ready = threading.Event()
    loop_holder = {}

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop_holder["loop"] = loop
        loop_holder["ident"] = threading.get_ident()
        ready.set()
        loop.run_forever()

    t = threading.Thread(target=_run, daemon=True, name="test-qlc-loop")
    t.start()
    ready.wait(timeout=5)
    loop = loop_holder["loop"]
    app._qlc_loop = loop
    app._qlc_loop_thread = t
    app._qlc_loop_thread_ident = loop_holder["ident"]
    yield loop
    loop.call_soon_threadsafe(loop.stop)


async def _slow_double(x, delay=0.2):
    await asyncio.sleep(delay)
    return x * 2


class TestQlcRunDispatch:
    def test_direct_path_when_not_eventlet(self, monkeypatch, _running_qlc_loop):
        monkeypatch.setattr(app, "_eventlet_active", lambda: False)
        assert app._qlc_run(_slow_double(5, delay=0.01), timeout=2) == 10

    def test_uses_tpool_execute_when_eventlet_active(
        self, monkeypatch, _running_qlc_loop
    ):
        monkeypatch.setattr(app, "_eventlet_active", lambda: True)

        import eventlet.tpool
        calls = []
        real_execute = eventlet.tpool.execute

        def spy_execute(fn, *a, **kw):
            calls.append(fn)
            return real_execute(fn, *a, **kw)

        monkeypatch.setattr(eventlet.tpool, "execute", spy_execute)
        result = app._qlc_run(_slow_double(5, delay=0.01), timeout=2)

        assert result == 10
        # The blocking wait is an original Event.wait, run off the hub.
        assert len(calls) == 1 and getattr(calls[0], "__name__", "") == "wait"

    def test_propagates_coroutine_exception_under_eventlet(
        self, monkeypatch, _running_qlc_loop
    ):
        monkeypatch.setattr(app, "_eventlet_active", lambda: True)

        async def _boom():
            raise RuntimeError("qlc exploded")

        with pytest.raises(RuntimeError, match="qlc exploded"):
            app._qlc_run(_boom(), timeout=2)


# ---------------------------------------------------------------------------
# Concurrency — the actual acceptance criterion from issue #47: two
# concurrent QLC+ calls must run in parallel under the eventlet bridge, not
# serialize behind each other.
# ---------------------------------------------------------------------------

class TestConcurrency:
    def test_concurrent_qlc_run_calls_do_not_serialize(
        self, monkeypatch, _running_qlc_loop
    ):
        # Production concurrency comes from gunicorn's eventlet worker
        # dispatching each request to its own greenthread, all cooperatively
        # scheduled on one native OS thread (the hub thread) -- not from
        # separate real OS threads. eventlet.spawn() is what actually
        # reproduces that; calling _qlc_run() from independent
        # threading.Thread()s instead hangs, because eventlet.tpool.execute()
        # relies on the calling thread's hub to observe the worker's
        # completion signal, and raw OS threads outside eventlet's greenlet
        # scheduling never drive that hub.
        monkeypatch.setattr(app, "_eventlet_active", lambda: True)
        import eventlet

        start = time.monotonic()
        greenthreads = [
            eventlet.spawn(app._qlc_run, _slow_double(i, delay=0.3), timeout=5)
            for i in range(3)
        ]
        results = [gt.wait() for gt in greenthreads]
        elapsed = time.monotonic() - start

        assert results == [0, 2, 4]
        # Serialized, three 0.3s calls would take ~0.9s; run in parallel they
        # should complete close to a single call's duration. Generous
        # threshold to keep this from being flaky under CI load.
        assert elapsed < 0.8, f"expected parallel execution, took {elapsed:.2f}s"


# ---------------------------------------------------------------------------
# _LoopTask / _qlc_submit — the handoff that replaced concurrent.futures.Future
# ---------------------------------------------------------------------------

class TestLoopTask:
    def test_result(self, _running_qlc_loop):
        handle = app._qlc_submit(_slow_double(4, delay=0.01))
        assert handle.wait(2) is True
        assert handle.done()
        assert handle.result() == 8

    def test_exception_propagates(self, _running_qlc_loop):
        async def _boom():
            raise ValueError("bad reply")

        handle = app._qlc_submit(_boom())
        assert handle.wait(2) is True
        with pytest.raises(ValueError, match="bad reply"):
            handle.result()

    def test_cancel_running_task(self, _running_qlc_loop):
        handle = app._qlc_submit(asyncio.sleep(10))
        time.sleep(0.05)
        handle.cancel()
        assert handle.wait(2) is True
        with pytest.raises(asyncio.CancelledError):
            handle.result()

    def test_cancel_before_start_never_runs(self, _running_qlc_loop):
        ran = []

        async def _record():
            ran.append(True)

        # Hold the loop so the submitted start callback queues behind it.
        _running_qlc_loop.call_soon_threadsafe(time.sleep, 0.2)
        handle = app._qlc_submit(_record())
        handle.cancel()
        assert handle.wait(2) is True
        assert ran == []
        with pytest.raises(asyncio.CancelledError):
            handle.result()


# ---------------------------------------------------------------------------
# _qlc_run guard + loop watchdog
# ---------------------------------------------------------------------------

class TestQlcRunWatchdog:
    def test_refuses_to_run_on_the_loop_thread(self, _running_qlc_loop):
        async def _calls_qlc_run_from_loop():
            try:
                app._qlc_run(asyncio.sleep(0), timeout=1)
            except RuntimeError as e:
                return str(e)
            return "no error"

        assert "loop thread" in app._qlc_run(_calls_qlc_run_from_loop(), timeout=2)

    def test_timeouts_with_live_loop_do_not_restart(self, _running_qlc_loop):
        for _ in range(app.QLC_WEDGE_TIMEOUTS):
            with pytest.raises(concurrent.futures.TimeoutError):
                app._qlc_run(asyncio.sleep(5), timeout=0.05)
        # The probe found the loop alive: counter reset, same loop, no restart.
        assert app._qlc_loop is _running_qlc_loop
        assert app._qlc_loop_restarts == 0
        assert app._qlc_consecutive_timeouts == 0

    def test_success_resets_timeout_count(self, _running_qlc_loop):
        with pytest.raises(concurrent.futures.TimeoutError):
            app._qlc_run(asyncio.sleep(5), timeout=0.05)
        assert app._qlc_consecutive_timeouts == 1
        assert app._qlc_run(_slow_double(1, delay=0), timeout=2) == 2
        assert app._qlc_consecutive_timeouts == 0

    def test_wedged_loop_is_replaced(self, _running_qlc_loop):
        wedged = _running_qlc_loop
        # Park the loop thread, standing in for a thread stuck on a lock.
        wedged.call_soon_threadsafe(time.sleep, 4)
        for _ in range(app.QLC_WEDGE_TIMEOUTS):
            with pytest.raises(concurrent.futures.TimeoutError):
                app._qlc_run(asyncio.sleep(0), timeout=0.1)

        assert app._qlc_loop_restarts == 1
        assert app._qlc_loop is not wedged
        assert app._qlc_ws is None  # dropped with the old loop; reconnects lazily
        assert app._qlc_run(_slow_double(3, delay=0), timeout=2) == 6


# ---------------------------------------------------------------------------
# _call_on_hub — keeps the loop thread off green primitives
# ---------------------------------------------------------------------------

class TestCallOnHub:
    def test_runs_inline_off_the_loop_thread(self):
        out = []
        app._call_on_hub(out.append, 1)
        assert out == [1]
        assert len(app._hub_pending) == 0

    def test_defers_from_the_loop_thread_until_drained(self, _running_qlc_loop):
        out = []

        async def _from_loop():
            app._call_on_hub(out.append, "deferred")

        app._qlc_run(_from_loop(), timeout=2)
        # The drainer thread may already have run it; drain whatever is left.
        app._drain_hub_pending()
        assert out == ["deferred"]

    def test_emit_from_loop_thread_reaches_subscribers(self, _running_qlc_loop):
        q = app.EVENT_BUS.subscribe()
        try:
            async def _emit_from_loop():
                app._emit("qlc_reconnect", {"url": "x"})

            app._qlc_run(_emit_from_loop(), timeout=2)
            app._drain_hub_pending()
            event = q.get(timeout=2)
            assert event["type"] == "qlc_reconnect"
        finally:
            app.EVENT_BUS.unsubscribe(q)

    def test_drain_survives_a_failing_call(self, capsys):
        out = []

        def _bad():
            raise RuntimeError("nope")

        app._hub_pending.append((_bad, (), {}))
        app._hub_pending.append((out.append, ("after",), {}))
        assert app._drain_hub_pending() == 2
        assert out == ["after"]
        assert "nope" in capsys.readouterr().out
