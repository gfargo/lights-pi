"""Suite 1 — Flask route integration tests.

Uses a Flask test client with a recording mock in place of the QLC+
WebSocket sender.  Every CH|<ch>|<val> frame the routes would normally
send over the wire is captured in ``recorded`` instead.

What's covered:
  - /api/action     (structured action dispatch)
  - /api/blackout   (zero all channels)
  - /api/batch      (ordered action list)
  - /api/channel    (raw DMX channel write)
  - /api/cue_lists  (CRUD endpoints)
  Request-validation paths (400 / 404) are also exercised.

What's NOT covered here (by design):
  - /api/command   — requires AI provider (mocked separately, see plan)
  - /api/channel_values — requires live QLC+ WebSocket reply
"""


# ---------------------------------------------------------------------------
# /api/action
# ---------------------------------------------------------------------------

class TestApiAction:
    def test_missing_action_returns_400(self, flask_client):
        client, _ = flask_client
        r = client.post("/api/action", json={})
        assert r.status_code == 400
        body = r.get_json()
        assert body["success"] is False
        assert "action" in body["error"].lower()

    def test_unknown_action_returns_failure(self, flask_client):
        client, _ = flask_client
        r = client.post("/api/action", json={"action": "no_such_action", "parameters": {}})
        assert r.status_code == 200
        body = r.get_json()
        assert body["success"] is False

    def test_adjust_brightness_sends_qlc_frames(self, flask_client):
        client, recorded = flask_client
        r = client.post("/api/action", json={
            "action": "adjust_brightness",
            "parameters": {"value": "255"},
        })
        assert r.status_code == 200
        body = r.get_json()
        assert body["success"] is True
        # Workspace has 2 fixtures; at least one brightness channel should be set
        assert any(cmd.startswith("CH|") for cmd in recorded)

    def test_adjust_brightness_response_shape(self, flask_client):
        client, _ = flask_client
        r = client.post("/api/action", json={
            "action": "adjust_brightness",
            "parameters": {"value": "128"},
        })
        body = r.get_json()
        assert "success" in body
        assert "action" in body
        assert "debug" in body

    def test_adjust_color_warm(self, flask_client):
        client, recorded = flask_client
        recorded.clear()
        r = client.post("/api/action", json={
            "action": "adjust_color",
            "parameters": {"color": "warm", "intensity": "200"},
        })
        assert r.status_code == 200
        body = r.get_json()
        assert body["success"] is True

    def test_action_no_body_returns_400(self, flask_client):
        client, _ = flask_client
        r = client.post("/api/action", content_type="application/json", data="")
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# /api/blackout
# ---------------------------------------------------------------------------

class TestApiBlackout:
    def test_blackout_all_fixtures(self, flask_client):
        client, recorded = flask_client
        recorded.clear()
        r = client.post("/api/blackout", json={})
        assert r.status_code == 200
        body = r.get_json()
        assert body["success"] is True
        assert body["fixtures"] == 2   # test workspace has 2 fixtures
        # All channels should be CH|n|0
        assert all(cmd.endswith("|0") for cmd in recorded)

    def test_blackout_response_shape(self, flask_client):
        client, _ = flask_client
        r = client.post("/api/blackout", json={})
        body = r.get_json()
        assert "success" in body
        assert "fixtures" in body
        assert "channels_zeroed" in body
        assert "groups" in body

    def test_blackout_channels_zeroed_count(self, flask_client):
        client, recorded = flask_client
        recorded.clear()
        r = client.post("/api/blackout", json={})
        body = r.get_json()
        # Fixture 1 has 1 channel, Fixture 2 has 3 channels → 4 total
        assert body["channels_zeroed"] == 4
        assert len(recorded) == 4

    def test_blackout_ch_format(self, flask_client):
        client, recorded = flask_client
        recorded.clear()
        client.post("/api/blackout", json={})
        for cmd in recorded:
            parts = cmd.split("|")
            assert len(parts) == 3
            assert parts[0] == "CH"
            assert parts[1].isdigit()
            assert parts[2] == "0"


# ---------------------------------------------------------------------------
# /api/batch
# ---------------------------------------------------------------------------

class TestApiBatch:
    def test_empty_actions_returns_400(self, flask_client):
        client, _ = flask_client
        r = client.post("/api/batch", json={"actions": []})
        assert r.status_code == 400

    def test_no_actions_key_returns_400(self, flask_client):
        client, _ = flask_client
        r = client.post("/api/batch", json={})
        assert r.status_code == 400

    def test_two_action_batch(self, flask_client):
        client, _ = flask_client
        r = client.post("/api/batch", json={
            "actions": [
                {"action": "adjust_brightness", "parameters": {"value": "200"}},
                {"action": "adjust_brightness", "parameters": {"value": "100"}},
            ]
        })
        assert r.status_code == 200
        body = r.get_json()
        assert "success" in body
        assert "results" in body
        assert len(body["results"]) == 2

    def test_batch_stops_on_error_by_default(self, flask_client):
        client, _ = flask_client
        r = client.post("/api/batch", json={
            "actions": [
                {"action": "no_such_action", "parameters": {}},
                {"action": "adjust_brightness", "parameters": {"value": "200"}},
            ]
        })
        body = r.get_json()
        # stop_on_error=True (default): second action should be skipped
        assert body["success"] is False
        assert len(body["results"]) == 1

    def test_batch_exceeds_limit_returns_400(self, flask_client):
        client, _ = flask_client
        actions = [{"action": "adjust_brightness", "parameters": {"value": "0"}}] * 21
        r = client.post("/api/batch", json={"actions": actions})
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# /api/channel
# ---------------------------------------------------------------------------

class TestApiChannel:
    def test_set_channel_known_fixture(self, flask_client):
        client, recorded = flask_client
        recorded.clear()
        r = client.post("/api/channel", json={
            "fixture_id": 1,
            "channel": 0,
            "value": 200,
        })
        assert r.status_code == 200
        body = r.get_json()
        assert body["success"] is True
        assert body["fixture_id"] == 1
        assert body["value"] == 200
        # Should have sent exactly one CH| command
        assert len(recorded) == 1
        assert recorded[0].startswith("CH|")
        assert recorded[0].endswith("|200")

    def test_set_channel_unknown_fixture_returns_404(self, flask_client):
        client, _ = flask_client
        r = client.post("/api/channel", json={
            "fixture_id": 999,
            "channel": 0,
            "value": 128,
        })
        assert r.status_code == 404
        body = r.get_json()
        assert body["success"] is False

    def test_set_channel_missing_fixture_id_returns_400(self, flask_client):
        client, _ = flask_client
        r = client.post("/api/channel", json={"channel": 0, "value": 128})
        assert r.status_code == 400
        body = r.get_json()
        assert body["success"] is False

    def test_set_channel_frame_format(self, flask_client):
        """Verify the CH|<ch>|<val> frame is correctly formed."""
        client, recorded = flask_client
        recorded.clear()
        # Fixture 1: universe=0, address=0, channel_offset=0 → absolute = 0*512+0+0+1 = 1
        client.post("/api/channel", json={"fixture_id": 1, "channel": 0, "value": 77})
        assert recorded == ["CH|1|77"]


# ---------------------------------------------------------------------------
# /api/cue_lists CRUD
# ---------------------------------------------------------------------------

class TestApiCueLists:
    def test_list_empty(self, flask_client):
        client, _ = flask_client
        r = client.get("/api/cue_lists")
        assert r.status_code == 200
        body = r.get_json()
        assert "cue_lists" in body
        assert isinstance(body["cue_lists"], list)

    def test_create_and_list(self, flask_client):
        client, _ = flask_client
        payload = {
            "name": "Test Cue List",
            "description": "integration test",
            "cues": [
                {"at": "0:00", "action": "adjust_brightness", "parameters": {"value": "255"}},
            ],
        }
        r = client.post("/api/cue_lists", json=payload)
        assert r.status_code in (200, 201)
        body = r.get_json()
        assert body.get("success") is True or "id" in body

        r2 = client.get("/api/cue_lists")
        names = [cl["name"] for cl in r2.get_json()["cue_lists"]]
        assert "Test Cue List" in names
