"""Stress the QLC+ loop bridge under a *real* eventlet.monkey_patch().

Run as a subprocess by test_eventlet_bridge_patched.py, because monkey-patching
can't be undone inside the pytest process. Imports the real app module (with
MOCK_DMX=1), drives one scenario, then prints a RESULT line and exits 0 if the
QLC+ loop is still alive, no greenlet ever tried to switch across OS threads,
and every worker greenlet finished.

usage: python _eventlet_stress.py <control-server dir> <scenario> [seconds]

scenarios:
  log    - the loop thread logs (via _call_on_hub) while hub greenlets log too.
           Logging straight from the loop thread wedged production in Nov 2026.
  emit   - the loop thread emits events while SSE subscribers wait on queues.
  calls  - many _qlc_run calls, some timing out, plus submit/cancel churn.
  wedge  - block the loop thread; _qlc_run's watchdog must replace the loop.
"""
import eventlet

eventlet.monkey_patch()

import asyncio  # noqa: E402
import io  # noqa: E402
import os  # noqa: E402
import queue  # noqa: E402
import random  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402

src, scenario = sys.argv[1], sys.argv[2]
seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0
os.environ["MOCK_DMX"] = "1"
sys.path.insert(0, src)

# The hub prints exceptions raised inside its timers to stderr; capture them.
captured = io.StringIO()
real_stderr = sys.stderr


class _Tee:
    def write(self, s):
        captured.write(s)
        real_stderr.write(s)

    def flush(self):
        real_stderr.flush()


sys.stderr = _Tee()

import app  # noqa: E402

assert app._eventlet_active(), "eventlet monkey patch not detected"
app._start_qlc_loop()

native_time = eventlet.patcher.original("time")
deadline = time.monotonic() + seconds
stats = {"ok": 0, "timeout": 0, "other": 0}


def _loop_alive(timeout=3.0):
    return app._qlc_loop_responsive(timeout)


def _log_from_loop():
    async def body():
        while time.monotonic() < deadline:
            app._call_on_hub(app.log.info, "loop_side", n=1)
            await asyncio.sleep(0)
    app._qlc_submit(body())

    def hub_logger():
        while time.monotonic() < deadline:
            app.log.info("hub_side", n=1)
            eventlet.sleep(0)
    return [eventlet.spawn(hub_logger) for _ in range(5)]


def _emit_from_loop():
    def subscriber():
        q = app.EVENT_BUS.subscribe()
        while time.monotonic() < deadline:
            try:
                q.get(timeout=0.05)
            except queue.Empty:
                pass

    async def body():
        while time.monotonic() < deadline:
            app._emit("qlc_reconnect", {"url": "test"})
            await asyncio.sleep(0.001)
    workers = [eventlet.spawn(subscriber) for _ in range(5)]
    app._qlc_submit(body())
    return workers


def _calls():
    def caller():
        while time.monotonic() < deadline:
            delay = 0 if random.random() < 0.9 else 0.03
            try:
                app._qlc_run(asyncio.sleep(delay), timeout=0.02)
                stats["ok"] += 1
            except Exception as e:  # noqa: BLE001
                stats["timeout" if type(e).__name__ == "TimeoutError" else "other"] += 1

    def canceller():
        while time.monotonic() < deadline:
            h = app._qlc_submit(asyncio.sleep(random.uniform(0, 0.01)))
            eventlet.sleep(random.uniform(0, 0.01))
            h.cancel()
    return [eventlet.spawn(caller) for _ in range(20)] + [eventlet.spawn(canceller) for _ in range(5)]


def _wedge():
    old_loop = app._qlc_loop
    # Park the loop thread on a native sleep, standing in for a loop thread
    # stuck on a lock it will never get.
    old_loop.call_soon_threadsafe(native_time.sleep, 60)

    def caller():
        recovered_at = None
        while time.monotonic() < deadline + 10:
            try:
                app._qlc_run(asyncio.sleep(0), timeout=0.3)
                if app._qlc_loop is not old_loop:
                    recovered_at = time.monotonic()
                    break
            except Exception as e:  # noqa: BLE001
                stats["timeout" if type(e).__name__ == "TimeoutError" else "other"] += 1
        stats["ok"] += 1 if recovered_at else 0
    return [eventlet.spawn(caller)]


SCENARIOS = {"log": _log_from_loop, "emit": _emit_from_loop, "calls": _calls, "wedge": _wedge}
workers = SCENARIOS[scenario]()

watch_end = deadline + 20
while any(not w.dead for w in workers) and time.monotonic() < watch_end:
    eventlet.sleep(0.1)
stuck = sum(1 for w in workers if not w.dead)
alive = _loop_alive()
cross_thread = captured.getvalue().count("Cannot switch to a different thread")
passed = alive and not cross_thread and not stuck
if scenario == "wedge":
    passed = passed and stats["ok"] == 1 and app._qlc_loop_restarts == 1
print(f"RESULT scenario={scenario} passed={passed} loop_alive={alive} stuck={stuck} "
      f"cross_thread_switches={cross_thread} restarts={app._qlc_loop_restarts} stats={stats}",
      file=real_stderr)
real_stderr.flush()
os._exit(0 if passed else 1)
