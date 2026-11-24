"""Regression tests for the QLC+ loop bridge under a REAL eventlet monkey-patch.

test_eventlet_bridge.py deliberately never monkey-patches (it can't be undone
in-process), which is how a cross-thread green-lock bug slipped through and
froze production for three days (Nov 2026): the loop thread logged while a
request handler logged, eventlet tried to switch to a greenlet on another OS
thread, and the loop thread stayed parked on structlog's lock forever.

Each test runs tests/_eventlet_stress.py in a fresh subprocess that patches
exactly like gunicorn's eventlet worker, imports the real app (MOCK_DMX=1),
and stresses one cross-thread path.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("eventlet")

_TESTS_DIR = Path(__file__).resolve().parent
_CONTROL_SERVER_DIR = _TESTS_DIR.parent
_SCRIPT = _TESTS_DIR / "_eventlet_stress.py"


def _run(scenario: str, seconds: float = 3.0) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONWARNINGS="ignore", MOCK_DMX="1")
    return subprocess.run(
        [sys.executable, str(_SCRIPT), str(_CONTROL_SERVER_DIR), scenario, str(seconds)],
        capture_output=True, text=True, timeout=90, env=env, cwd=str(_CONTROL_SERVER_DIR),
    )


def _result_line(proc: subprocess.CompletedProcess) -> str:
    lines = [ln for ln in proc.stderr.splitlines() if ln.startswith("RESULT")]
    return lines[-1] if lines else f"(no RESULT line)\n{proc.stderr[-2000:]}"


@pytest.mark.parametrize("scenario", ["log", "emit", "calls"])
def test_loop_thread_never_wedges(scenario):
    proc = _run(scenario)
    assert proc.returncode == 0, _result_line(proc)


def test_watchdog_replaces_a_wedged_loop():
    proc = _run("wedge", seconds=1.0)
    assert proc.returncode == 0, _result_line(proc)
