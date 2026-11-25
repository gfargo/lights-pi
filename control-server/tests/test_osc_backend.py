"""Tests for osc_backend.py — pure routing + outbound emission.

Everything here exercises dispatch_osc()/OscStateEmitter() directly with
synthetic addresses/args and stub collaborators — no real UDP socket, no
python-osc transport classes involved.
"""
import pytest
from event_bus import EventBus
from osc_backend import (
    OscConfig,
    OscFeedbackClient,
    OscMonitor,
    OscStateEmitter,
    dispatch_osc,
    drain_event_bus,
    osc_slug,
)


class RecordingActions:
    def __init__(self):
        self.calls = []

    def activate_scene(self, name):
        self.calls.append(("activate_scene", name))

    def start_chase(self, name):
        self.calls.append(("start_chase", name))

    def stop_chase(self, name):
        self.calls.append(("stop_chase", name))

    def set_channel(self, fixture_id, channel, value):
        self.calls.append(("set_channel", fixture_id, channel, value))

    def set_master(self, value):
        self.calls.append(("set_master", value))

    def blackout(self):
        self.calls.append(("blackout",))

    def cue_go(self, ref):
        self.calls.append(("cue_go", ref))

    def cue_stop(self, ref):
        self.calls.append(("cue_stop", ref))

    def cue_pause(self, ref):
        self.calls.append(("cue_pause", ref))


# ---------------------------------------------------------------------------
# dispatch_osc — routing
# ---------------------------------------------------------------------------

class TestDispatchScene:
    def test_activates_named_scene(self):
        actions = RecordingActions()
        result = dispatch_osc("/scene/warm-wash", (), actions)
        assert actions.calls == [("activate_scene", "warm-wash")]
        assert result["ok"] is True

    def test_no_call_on_missing_name(self):
        actions = RecordingActions()
        result = dispatch_osc("/scene", (), actions)
        assert actions.calls == []
        assert result["ok"] is False


class TestDispatchChase:
    def test_starts_named_chase(self):
        actions = RecordingActions()
        result = dispatch_osc("/chase/strobe", (), actions)
        assert actions.calls == [("start_chase", "strobe")]
        assert result["ok"] is True


class TestDispatchFixture:
    def test_sets_channel_with_raw_value(self):
        actions = RecordingActions()
        result = dispatch_osc("/fixture/1/0", (128,), actions)
        assert actions.calls == [("set_channel", 1, 0, 128)]
        assert result["ok"] is True

    def test_normalizes_float_fraction_to_0_255(self):
        actions = RecordingActions()
        dispatch_osc("/fixture/3/2", (0.5,), actions)
        assert actions.calls == [("set_channel", 3, 2, 128)]

    def test_missing_channel_segment_errors_without_calling(self):
        actions = RecordingActions()
        result = dispatch_osc("/fixture/1", (128,), actions)
        assert actions.calls == []
        assert result["ok"] is False

    def test_missing_value_arg_errors_without_calling(self):
        actions = RecordingActions()
        result = dispatch_osc("/fixture/1/0", (), actions)
        assert actions.calls == []
        assert result["ok"] is False

    def test_non_numeric_ids_error_without_calling(self):
        actions = RecordingActions()
        result = dispatch_osc("/fixture/foo/0", (128,), actions)
        assert actions.calls == []
        assert result["ok"] is False


class TestDispatchMaster:
    def test_sets_master_raw(self):
        actions = RecordingActions()
        result = dispatch_osc("/master", (200,), actions)
        assert actions.calls == [("set_master", 200)]
        assert result["ok"] is True

    def test_sets_master_from_float_fraction(self):
        actions = RecordingActions()
        dispatch_osc("/master", (1.0,), actions)
        assert actions.calls == [("set_master", 255)]

    def test_missing_value_errors_without_calling(self):
        actions = RecordingActions()
        result = dispatch_osc("/master", (), actions)
        assert actions.calls == []
        assert result["ok"] is False


class TestDispatchBlackout:
    def test_triggers_blackout(self):
        actions = RecordingActions()
        result = dispatch_osc("/blackout", (), actions)
        assert actions.calls == [("blackout",)]
        assert result["ok"] is True


class TestDispatchCue:
    def test_go(self):
        actions = RecordingActions()
        result = dispatch_osc("/cue/go", (), actions)
        assert actions.calls == [("cue_go", None)]
        assert result["ok"] is True

    def test_go_with_ref_arg(self):
        actions = RecordingActions()
        dispatch_osc("/cue/go", ("main",), actions)
        assert actions.calls == [("cue_go", "main")]

    def test_stop(self):
        actions = RecordingActions()
        result = dispatch_osc("/cue/stop", (), actions)
        assert actions.calls == [("cue_stop", None)]
        assert result["ok"] is True

    def test_pause(self):
        actions = RecordingActions()
        result = dispatch_osc("/cue/pause", (), actions)
        assert actions.calls == [("cue_pause", None)]
        assert result["ok"] is True

    def test_unknown_verb_errors_without_calling(self):
        actions = RecordingActions()
        result = dispatch_osc("/cue/rewind", (), actions)
        assert actions.calls == []
        assert result["ok"] is False


class TestDispatchUnknownOrMalformed:
    def test_unknown_address_returns_error_no_exception(self):
        actions = RecordingActions()
        result = dispatch_osc("/bogus", (1, 2, 3), actions)
        assert actions.calls == []
        assert result["ok"] is False

    def test_empty_address_returns_error_no_exception(self):
        actions = RecordingActions()
        result = dispatch_osc("/", (), actions)
        assert actions.calls == []
        assert result["ok"] is False

    def test_garbage_args_never_raises(self):
        actions = RecordingActions()
        result = dispatch_osc("/fixture/1/0", ("not-a-number",), actions)
        assert actions.calls == []
        assert result["ok"] is False

    def test_action_raising_is_caught(self):
        class ExplodingActions(RecordingActions):
            def activate_scene(self, name):
                raise RuntimeError("boom")

        result = dispatch_osc("/scene/warm-wash", (), ExplodingActions())
        assert result["ok"] is False
        assert "boom" in result["error"]


# ---------------------------------------------------------------------------
# OscStateEmitter — outbound emission against a stub client
# ---------------------------------------------------------------------------

class StubOscClient:
    def __init__(self):
        self.sent = []

    def send_message(self, address, value):
        self.sent.append((address, value))


class TestOscStateEmitter:
    def test_scene_activated_emits_state(self):
        client = StubOscClient()
        emitter = OscStateEmitter(client)
        emitter.on_event("scene_activated", {"scene_name": "warm-wash"})
        assert client.sent == [("/state/scene-active", "warm-wash"), ("/scene/warm-wash", 1.0)]

    def test_scene_feedback_lights_new_and_clears_previous(self):
        client = StubOscClient()
        emitter = OscStateEmitter(client)
        emitter.on_event("scene_activated", {"scene_id": "3", "scene_name": "Lights ON"})
        assert client.sent == [("/state/scene-active", "Lights ON"),
                               ("/scene/3", 1.0), ("/scene/lights-on", 1.0)]
        client.sent.clear()
        emitter.on_event("scene_activated", {"scene_id": "7", "scene_name": "Red"})
        assert client.sent == [("/state/scene-active", "Red"),
                               ("/scene/3", 0.0), ("/scene/lights-on", 0.0),
                               ("/scene/7", 1.0), ("/scene/red", 1.0)]

    def test_same_scene_again_is_not_cleared(self):
        client = StubOscClient()
        emitter = OscStateEmitter(client)
        emitter.on_event("scene_activated", {"scene_id": "3", "scene_name": "Red"})
        client.sent.clear()
        emitter.on_event("scene_activated", {"scene_id": "3", "scene_name": "Red"})
        assert ("/scene/3", 0.0) not in client.sent

    def test_master_changed_emits_state(self):
        client = StubOscClient()
        emitter = OscStateEmitter(client)
        emitter.on_event("master_changed", {"value": 51})
        assert client.sent == [("/state/master", 51), ("/master", 0.2)]

    def test_chase_started_emits_state(self):
        client = StubOscClient()
        emitter = OscStateEmitter(client)
        emitter.on_event("chase_started", {"chase_id": 12, "chase_name": "Rainbow Fade"})
        assert client.sent == [("/state/chase-active", "Rainbow Fade"),
                               ("/chase/12", 1.0), ("/chase/rainbow-fade", 1.0)]

    def test_chase_stopped_emits_empty_state(self):
        client = StubOscClient()
        emitter = OscStateEmitter(client)
        emitter.on_event("chase_stopped", {"chase_id": 12, "chase_name": "strobe"})
        assert client.sent == [("/state/chase-active", ""), ("/chase/12", 0.0), ("/chase/strobe", 0.0)]

    def test_unrelated_event_is_ignored(self):
        client = StubOscClient()
        emitter = OscStateEmitter(client)
        emitter.on_event("channel_change", {"channels": []})
        assert client.sent == []

    def test_missing_payload_field_does_not_send(self):
        client = StubOscClient()
        emitter = OscStateEmitter(client)
        emitter.on_event("scene_activated", {})
        assert client.sent == []

    def test_client_exception_is_swallowed(self):
        class ExplodingClient:
            def send_message(self, address, value):
                raise OSError("network unreachable")

        emitter = OscStateEmitter(ExplodingClient())
        emitter.on_event("scene_activated", {"scene_name": "x"})  # must not raise


class TestDrainEventBus:
    def test_forwards_published_events_to_emitter_until_stopped(self):
        import threading

        bus = EventBus()
        client = StubOscClient()
        emitter = OscStateEmitter(client)
        stop_event = threading.Event()

        thread = threading.Thread(target=drain_event_bus, args=(bus, emitter, stop_event))
        thread.start()
        try:
            bus.publish("scene_activated", {"scene_name": "warm-wash"})
            # Poll briefly for the background thread to drain the queue.
            for _ in range(100):
                if client.sent:
                    break
                threading.Event().wait(0.01)
            assert client.sent[0] == ("/state/scene-active", "warm-wash")
        finally:
            stop_event.set()
            thread.join(timeout=2)


# ---------------------------------------------------------------------------
# OscConfig
# ---------------------------------------------------------------------------

class TestOscConfig:
    def test_defaults(self, monkeypatch):
        for key in ("OSC_ENABLED", "OSC_LISTEN_HOST", "OSC_LISTEN_PORT", "OSC_OUT_HOST", "OSC_OUT_PORT"):
            monkeypatch.delenv(key, raising=False)
        config = OscConfig.from_env()
        assert config.enabled is True
        assert config.listen_host == "0.0.0.0"
        assert config.listen_port == 8000

    def test_overrides_from_env(self, monkeypatch):
        monkeypatch.setenv("OSC_ENABLED", "false")
        monkeypatch.setenv("OSC_LISTEN_PORT", "9100")
        monkeypatch.setenv("OSC_OUT_HOST", "192.168.1.50")
        monkeypatch.setenv("OSC_OUT_PORT", "9200")
        config = OscConfig.from_env()
        assert config.enabled is False
        assert config.listen_port == 9100
        assert config.out_host == "192.168.1.50"
        assert config.out_port == 9200

    @pytest.mark.parametrize("raw", ["0", "false", "False", "no", "NO"])
    def test_falsy_enabled_values(self, monkeypatch, raw):
        monkeypatch.setenv("OSC_ENABLED", raw)
        assert OscConfig.from_env().enabled is False


# ---------------------------------------------------------------------------
# Clean-start / no-op safety
# ---------------------------------------------------------------------------

class TestCleanStartSafety:
    def test_disabled_config_and_router_do_no_network_io(self):
        """Constructing a disabled config and dispatching garbage must never
        touch a socket — this module only imports python-osc's transport
        classes inside start_listener()/build_udp_client()."""
        config = OscConfig(enabled=False)
        assert config.enabled is False
        actions = RecordingActions()
        result = dispatch_osc("", (), actions)
        assert result["ok"] is False
        assert actions.calls == []


# ---------------------------------------------------------------------------
# TouchOSC buttons: press vs release
# ---------------------------------------------------------------------------

class TestButtonRelease:
    @pytest.mark.parametrize("address", ["/scene/red", "/blackout", "/cue/go", "/cue/stop", "/cue/pause"])
    def test_release_is_ignored(self, address):
        actions = RecordingActions()
        result = dispatch_osc(address, (0.0,), actions)
        assert actions.calls == []
        assert result == {"ok": True, "address": address, "action": "release"}

    def test_press_fires(self):
        actions = RecordingActions()
        dispatch_osc("/scene/red", (1.0,), actions)
        dispatch_osc("/blackout", (1,), actions)
        assert actions.calls == [("activate_scene", "red"), ("blackout",)]

    def test_chase_toggle_off_stops(self):
        actions = RecordingActions()
        dispatch_osc("/chase/rainbow", (1.0,), actions)
        dispatch_osc("/chase/rainbow", (0.0,), actions)
        assert actions.calls == [("start_chase", "rainbow"), ("stop_chase", "rainbow")]

    def test_cue_numeric_arg_is_not_a_ref(self):
        actions = RecordingActions()
        dispatch_osc("/cue/go", (1.0,), actions)
        assert actions.calls == [("cue_go", None)]

    def test_string_zero_is_not_a_release(self):
        actions = RecordingActions()
        dispatch_osc("/cue/go", ("0",), actions)
        assert actions.calls == [("cue_go", "0")]


class TestSlug:
    @pytest.mark.parametrize("name,slug", [
        ("Lights ON", "lights-on"), ("White - Cool", "white-cool"),
        ("  Red!! ", "red"), ("", ""), (None, ""), (12, "12"),
    ])
    def test_slug(self, name, slug):
        assert osc_slug(name) == slug


class TestFeedbackClient:
    def _client(self, **kw):
        made = []

        def make(host, port):
            stub = StubOscClient()
            made.append(((host, port), stub))
            return stub
        return OscFeedbackClient(make, **kw), made

    def test_sender_mode_waits_for_a_sender(self):
        fb, made = self._client(mode="sender", port=9000)
        fb.send_message("/x", 1)
        assert made == []
        fb.note_sender("192.168.1.20")
        fb.send_message("/x", 1)
        assert made[0][0] == ("192.168.1.20", 9000)
        assert made[0][1].sent == [("/x", 1)]

    def test_sender_mode_follows_the_latest_sender(self):
        fb, made = self._client(mode="sender", port=9000)
        fb.note_sender("10.0.0.2")
        fb.send_message("/x", 1)
        fb.note_sender("10.0.0.3")
        fb.send_message("/x", 2)
        assert [m[0] for m in made] == [("10.0.0.2", 9000), ("10.0.0.3", 9000)]

    def test_fixed_mode_ignores_senders(self):
        fb, made = self._client(mode="fixed", host="10.0.0.9", port=9100)
        fb.note_sender("10.0.0.2")
        fb.send_message("/x", 1)
        fb.send_message("/y", 2)
        assert len(made) == 1 and made[0][0] == ("10.0.0.9", 9100)
        assert made[0][1].sent == [("/x", 1), ("/y", 2)]

    def test_off_sends_nothing(self):
        fb, made = self._client(mode="off")
        fb.note_sender("10.0.0.2")
        fb.send_message("/x", 1)
        assert made == [] and fb.destination() is None

    def test_configure_validates_mode(self):
        fb, _ = self._client()
        with pytest.raises(ValueError):
            fb.configure("broadcast", "", 9000)
        fb.configure("fixed", "10.0.0.9", "9001")
        assert fb.destination() == ("10.0.0.9", 9001)

    def test_monitor_counts_sends(self):
        monitor = OscMonitor()
        fb = OscFeedbackClient(lambda h, p: StubOscClient(), mode="fixed", host="h", port=1, monitor=monitor)
        fb.send_message("/x", 1)
        assert monitor.sent == 1 and monitor.last_sent_to == "h:1"


class TestMonitor:
    def test_keeps_recent_newest_first_and_bounded(self):
        monitor = OscMonitor(size=3)
        for i in range(5):
            monitor.note_received("10.0.0.2", f"/scene/{i}", (1.0,), {"ok": True, "action": "activate_scene"})
        recent = monitor.recent()
        assert monitor.received == 5
        assert [r["address"] for r in recent] == ["/scene/4", "/scene/3", "/scene/2"]
        assert recent[0]["from"] == "10.0.0.2" and recent[0]["args"] == [1.0] and recent[0]["ok"] is True

    def test_records_errors(self):
        monitor = OscMonitor()
        monitor.note_received(None, "/nope", (), {"ok": False, "error": "unknown address: /nope"})
        assert monitor.recent()[0]["error"] == "unknown address: /nope"


class TestOscConfigOutHost:
    def test_out_host_set_flag(self, monkeypatch):
        monkeypatch.delenv("OSC_OUT_HOST", raising=False)
        assert OscConfig.from_env().out_host_set is False
        monkeypatch.setenv("OSC_OUT_HOST", "10.0.0.5")
        config = OscConfig.from_env()
        assert config.out_host_set is True and config.out_host == "10.0.0.5"
