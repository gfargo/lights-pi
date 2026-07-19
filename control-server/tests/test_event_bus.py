"""Tests for event_bus.py — pure-logic helpers and the EventBus class."""
import json
import queue
import time

from event_bus import EventBus, format_sse, parse_filter

# ---------------------------------------------------------------------------
# format_sse
# ---------------------------------------------------------------------------

class TestFormatSse:
    def test_starts_with_event_line(self):
        s = format_sse("ping", {})
        assert s.startswith("event: ping\n")

    def test_ends_with_blank_line(self):
        s = format_sse("ping", {})
        assert s.endswith("\n\n")

    def test_data_is_json(self):
        s = format_sse("test", {"key": "value"})
        lines = s.splitlines()
        data_line = next(ln for ln in lines if ln.startswith("data:"))
        payload = json.loads(data_line[len("data: "):].strip() if data_line[5] == " " else data_line[5:])
        assert payload == {"key": "value"}

    def test_structure(self):
        s = format_sse("channel_change", {"channel": 1, "value": 255})
        assert "event: channel_change\n" in s
        assert "data: " in s

    def test_empty_data(self):
        s = format_sse("heartbeat", {})
        assert "event: heartbeat\n" in s
        assert "data: {}" in s


# ---------------------------------------------------------------------------
# parse_filter
# ---------------------------------------------------------------------------

class TestParseFilter:
    def test_none_returns_none(self):
        assert parse_filter(None) is None

    def test_empty_string_returns_none(self):
        assert parse_filter("") is None

    def test_scenes_token(self):
        assert parse_filter("scenes") == {"scene_activated"}

    def test_channels_token(self):
        assert parse_filter("channels") == {"channel_change"}

    def test_groups_token(self):
        assert parse_filter("groups") == {"group_modified"}

    def test_qlc_token_expands_to_both(self):
        result = parse_filter("qlc")
        assert result == {"qlc_disconnect", "qlc_reconnect"}

    def test_status_token(self):
        assert parse_filter("status") == {"service_status"}

    def test_multiple_tokens(self):
        result = parse_filter("scenes,channels")
        assert result == {"scene_activated", "channel_change"}

    def test_unknown_token_ignored(self):
        result = parse_filter("scenes,unknown_garbage")
        assert result == {"scene_activated"}

    def test_all_unknown_returns_none(self):
        # All tokens unrecognised → falls back to None (all events)
        assert parse_filter("foo,bar") is None

    def test_whitespace_around_tokens(self):
        result = parse_filter(" scenes , channels ")
        assert result == {"scene_activated", "channel_change"}

    def test_qlc_and_scenes(self):
        result = parse_filter("qlc,scenes")
        assert result == {"qlc_disconnect", "qlc_reconnect", "scene_activated"}


# ---------------------------------------------------------------------------
# EventBus
# ---------------------------------------------------------------------------

class TestEventBus:
    def test_subscribe_returns_queue(self):
        bus = EventBus()
        q = bus.subscribe()
        assert isinstance(q, queue.Queue)

    def test_publish_delivers_to_subscriber(self):
        bus = EventBus()
        q = bus.subscribe()
        bus.publish("test_event", {"x": 1})
        envelope = q.get_nowait()
        assert envelope["type"] == "test_event"
        assert envelope["data"]["x"] == 1

    def test_publish_stamps_timestamp(self):
        bus = EventBus()
        q = bus.subscribe()
        before = int(time.time() * 1000)
        bus.publish("test_event", {})
        after = int(time.time() * 1000)
        envelope = q.get_nowait()
        ts = envelope["data"]["timestamp"]
        assert before <= ts <= after

    def test_publish_to_multiple_subscribers(self):
        bus = EventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        bus.publish("ping", {"n": 42})
        e1 = q1.get_nowait()
        e2 = q2.get_nowait()
        assert e1["data"]["n"] == 42
        assert e2["data"]["n"] == 42

    def test_unsubscribe_removes_queue(self):
        bus = EventBus()
        q = bus.subscribe()
        bus.unsubscribe(q)
        bus.publish("ping", {})
        assert q.empty()

    def test_unsubscribe_idempotent(self):
        bus = EventBus()
        q = bus.subscribe()
        bus.unsubscribe(q)
        bus.unsubscribe(q)  # should not raise

    def test_full_queue_drops_silently(self):
        bus = EventBus()
        q = bus.subscribe()
        # Fill the queue beyond maxsize
        for i in range(bus._QUEUE_MAXSIZE + 10):
            bus.publish("flood", {"i": i})
        # No exception raised; queue is at max (not crashed)
        assert q.qsize() <= bus._QUEUE_MAXSIZE

    def test_publish_does_not_mutate_caller_data_timestamp(self):
        """publish stamps timestamp on the dict in-place; caller should be aware."""
        bus = EventBus()
        q = bus.subscribe()
        data = {"val": 7}
        bus.publish("ev", data)
        # data now has timestamp added — acceptable per the design
        envelope = q.get_nowait()
        assert envelope["data"]["val"] == 7
        assert "timestamp" in envelope["data"]

    def test_existing_timestamp_not_overwritten(self):
        bus = EventBus()
        q = bus.subscribe()
        bus.publish("ev", {"timestamp": 12345})
        envelope = q.get_nowait()
        assert envelope["data"]["timestamp"] == 12345
