"""Gunicorn config for the eventlet worker (issue #47).

Usage: gunicorn -c gunicorn.conf.py app:app
The systemd unit (scripts/services/control_server.sh) also passes -k/-w
explicitly so they're visible in `systemctl status` / `ps aux`; the values
here match and serve as the default for ad hoc/local runs.
"""
import os

worker_class = "eventlet"
workers = 1  # QLC+ WS is a single-writer surface -- do not raise this (see issue #47).
bind = f"0.0.0.0:{os.getenv('CONTROL_PORT', '5000')}"

# Must stay False: the eventlet worker monkey-patches inside each forked
# worker process, before it imports the WSGI app (app:app). Preloading would
# import app.py once in the master — before any patching — and workers would
# inherit that already-imported, unpatched module instead of re-importing it
# post-fork, defeating the eventlet bridge in app.py (_eventlet_active() etc).
preload_app = False


def post_worker_init(worker):
    """Runs once per worker, right after it has imported app:app — i.e. after
    eventlet's monkey-patching (see EventletWorker.init_process in gunicorn)
    but before the worker starts accepting connections. This is where the
    QLC+ background loop, boot-restore threads, and audio subscription start;
    see app.py's init_runtime() for what that covers and why it must run here
    rather than at module import time or in a pre-fork hook.
    """
    from app import init_runtime
    init_runtime()
