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
    """_qlc_run()/_wait_for_future() read module-level globals; make sure
    tests don't leak a loop/thread into each other or into unrelated tests."""
    yield
    app._qlc_loop = None
    app._qlc_loop_thread = None


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
        # eventlet.patcher.original() always hands back a distinct module
        # object even when nothing is actually monkey-patched yet -- the
        # point under test is that _native_threading_module() defers to it
        # instead of the (possibly-patched) `threading` import in app.py.
        import eventlet.patcher
        assert native is eventlet.patcher.original("threading")
        # And it must still be a real, usable threading module.
        event = native.Event()
        event.set()
        assert event.is_set()


# ---------------------------------------------------------------------------
# _wait_for_future
# ---------------------------------------------------------------------------

class TestWaitForFuture:
    def test_returns_result_once_future_completes(self):
        future = concurrent.futures.Future()
        threading.Timer(0.05, lambda: future.set_result(42)).start()
        assert app._wait_for_future(future, timeout=2) == 42

    def test_propagates_exception(self):
        future = concurrent.futures.Future()
        threading.Timer(
            0.05, lambda: future.set_exception(ValueError("boom"))
        ).start()
        with pytest.raises(ValueError, match="boom"):
            app._wait_for_future(future, timeout=2)

    def test_timeout_raises_and_cancels_future(self):
        future = concurrent.futures.Future()
        with pytest.raises(concurrent.futures.TimeoutError):
            app._wait_for_future(future, timeout=0.05)
        # _wait_for_future() cancels on timeout so the caller's loop doesn't
        # keep running an abandoned coroutine.
        assert future.cancelled() or not future.set_running_or_notify_cancel()

    def test_already_done_future_returns_immediately(self):
        future = concurrent.futures.Future()
        future.set_result("done")
        assert app._wait_for_future(future, timeout=1) == "done"


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
        ready.set()
        loop.run_forever()

    t = threading.Thread(target=_run, daemon=True, name="test-qlc-loop")
    t.start()
    ready.wait(timeout=5)
    loop = loop_holder["loop"]
    app._qlc_loop = loop
    app._qlc_loop_thread = t
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
        assert calls == [app._wait_for_future]

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
