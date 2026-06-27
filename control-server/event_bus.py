"""Minimal thread-safe pub/sub event bus for the SSE endpoint.

Publishers run on both Flask request threads and the QLC+ asyncio-loop thread,
so the bus is lock-protected and non-blocking (full queues drop silently).
"""
import json
import queue
import threading
import time
from typing import Optional


# ---------------------------------------------------------------------------
# Filter helpers (pure — unit-testable without Flask)
# ---------------------------------------------------------------------------

# Map friendly filter token → set of event type strings it enables
_FILTER_MAP: dict[str, set[str]] = {
    "channels": {"channel_change"},
    "scenes":   {"scene_activated"},
    "groups":   {"group_modified"},
    "qlc":      {"qlc_disconnect", "qlc_reconnect"},
    "status":   {"service_status"},
}


def parse_filter(raw: Optional[str]) -> Optional[set]:
    """Convert a comma-separated filter query param into a set of event types.

    Returns None when *raw* is absent/empty (means "all events").
    Unknown tokens are silently ignored.

    >>> parse_filter(None) is None
    True
    >>> parse_filter("") is None
    True
    >>> parse_filter("scenes,channels") == {"scene_activated", "channel_change"}
    True
    >>> parse_filter("qlc") == {"qlc_disconnect", "qlc_reconnect"}
    True
    """
    if not raw:
        return None
    types: set[str] = set()
    for token in raw.split(","):
        token = token.strip().lower()
        if token in _FILTER_MAP:
            types |= _FILTER_MAP[token]
    return types if types else None


# ---------------------------------------------------------------------------
# SSE wire-format helper (pure — unit-testable without Flask)
# ---------------------------------------------------------------------------

def format_sse(event_type: str, data: dict) -> str:
    """Return a properly-framed SSE string for one event.

    The ``data:`` field is JSON-encoded on a single line; the block is
    terminated by a blank line as required by the SSE spec.

    >>> s = format_sse("ping", {"ok": True})
    >>> s.startswith("event: ping\\n")
    True
    >>> s.endswith("\\n\\n")
    True
    """
    payload = json.dumps(data, separators=(",", ":"))
    return f"event: {event_type}\ndata: {payload}\n\n"


# ---------------------------------------------------------------------------
# Event bus
# ---------------------------------------------------------------------------

class EventBus:
    """Thread-safe, non-blocking pub/sub bus backed by per-subscriber queues."""

    _QUEUE_MAXSIZE = 500

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue] = []

    # -- publisher side ------------------------------------------------------

    def publish(self, event_type: str, data: dict) -> None:
        """Publish an event to all current subscribers.

        Stamps a ``timestamp`` (epoch milliseconds) onto *data* in-place,
        then puts ``{"type": event_type, "data": data}`` into each queue.
        Drops silently on full queues — never blocks the caller.
        """
        data.setdefault("timestamp", int(time.time() * 1000))
        envelope = {"type": event_type, "data": data}
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(envelope)
            except queue.Full:
                pass  # slow/dead client — drop

    # -- subscriber side -----------------------------------------------------

    def subscribe(self) -> queue.Queue:
        """Register a new subscriber and return its queue."""
        q: queue.Queue = queue.Queue(maxsize=self._QUEUE_MAXSIZE)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        """Remove a subscriber queue (idempotent)."""
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass
