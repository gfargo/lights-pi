"""Tests for the OSC settings helpers and /api/osc routes in app.py."""
import json

import app
import pytest
from osc_backend import OscConfig, OscFeedbackClient


class Recorder:
    def __init__(self):
        self.sent = []

    def send_message(self, address, value):
        self.sent.append((address, value))


class TestResolveOscRef:
    ITEMS = [{"id": 3, "name": "Lights ON"}, {"id": 7, "name": "White - Cool"}]

    @pytest.mark.parametrize("ref,expected", [
        ("3", 3), ("lights on", 3), ("lights-on", 3), ("white-cool", 7), ("nope", "nope"),
    ])
    def test_resolves(self, ref, expected):
        assert app._resolve_osc_ref(ref, self.ITEMS) == expected


class TestValidateSettings:
    def test_modes(self):
        assert app._validate_osc_settings({"mode": "sender", "port": 9000}) == (
            {"mode": "sender", "host": "", "port": 9000}, None)
        assert app._validate_osc_settings({"mode": "off"})[0]["mode"] == "off"
        assert app._validate_osc_settings({"mode": "loud"})[1]

    def test_fixed_needs_a_valid_host(self):
        assert app._validate_osc_settings({"mode": "fixed", "port": 9000})[1]
        assert app._validate_osc_settings({"mode": "fixed", "host": "ipad; rm -rf", "port": 9000})[1]
        ok, err = app._validate_osc_settings({"mode": "fixed", "host": "192.168.1.40", "port": "9001"})
        assert err is None and ok == {"mode": "fixed", "host": "192.168.1.40", "port": 9001}

    @pytest.mark.parametrize("port", [0, 70000, "x"])
    def test_bad_ports(self, port):
        assert app._validate_osc_settings({"mode": "sender", "port": port})[1]


class TestLoadSettings:
    def test_defaults_reply_to_sender(self, monkeypatch, tmp_path):
        monkeypatch.setattr(app, "OSC_SETTINGS_FILE", tmp_path / "osc.json")
        assert app._load_osc_settings(OscConfig()) == {"mode": "sender", "host": "", "port": 9000}

    def test_env_out_host_means_fixed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(app, "OSC_SETTINGS_FILE", tmp_path / "osc.json")
        config = OscConfig(out_host="10.0.0.5", out_port=9100, out_host_set=True)
        assert app._load_osc_settings(config) == {"mode": "fixed", "host": "10.0.0.5", "port": 9100}

    def test_saved_file_wins_and_bad_file_falls_back(self, monkeypatch, tmp_path):
        f = tmp_path / "osc.json"
        monkeypatch.setattr(app, "OSC_SETTINGS_FILE", f)
        f.write_text(json.dumps({"mode": "off", "port": 9000}))
        assert app._load_osc_settings(OscConfig())["mode"] == "off"
        f.write_text("{not json")
        assert app._load_osc_settings(OscConfig())["mode"] == "sender"


class TestRoutes:
    @pytest.fixture
    def client(self, monkeypatch, tmp_path):
        monkeypatch.setattr(app, "OSC_SETTINGS_FILE", tmp_path / "osc.json")
        self.made = []

        def make(host, port):
            r = Recorder()
            self.made.append(((host, port), r))
            return r
        monkeypatch.setattr(app, "_osc_feedback", OscFeedbackClient(make, "sender", "", 9000))
        monkeypatch.setattr(app, "_local_ipv4_addresses", lambda: ["192.168.1.10"])
        return app.app.test_client()

    def test_status(self, client):
        data = client.get("/api/osc").get_json()
        assert data["listen_port"] == app._osc_config.listen_port
        assert data["addresses"] == ["192.168.1.10"]
        assert data["feedback"]["mode"] == "sender"
        assert data["feedback"]["destination"] is None

    def test_test_needs_a_sender(self, client):
        resp = client.post("/api/osc/test")
        assert resp.status_code == 409

    def test_settings_then_test(self, client, tmp_path):
        resp = client.post("/api/osc/settings", json={"mode": "fixed", "host": "192.168.1.40", "port": 9000})
        assert resp.status_code == 200
        assert json.loads((tmp_path / "osc.json").read_text())["host"] == "192.168.1.40"
        assert resp.get_json()["feedback"]["destination"] == "192.168.1.40:9000"
        sent = client.post("/api/osc/test").get_json()
        assert sent == {"success": True, "sent_to": "192.168.1.40:9000"}
        assert self.made[0][1].sent == [("/state/test", 1)]

    def test_bad_settings_rejected(self, client):
        assert client.post("/api/osc/settings", json={"mode": "fixed"}).status_code == 400

    def test_off_blocks_test(self, client):
        client.post("/api/osc/settings", json={"mode": "off"})
        resp = client.post("/api/osc/test")
        assert resp.status_code == 409 and "off" in resp.get_json()["error"].lower()

    def test_backend_not_running(self, client, monkeypatch):
        monkeypatch.setattr(app, "_osc_feedback", None)
        assert client.post("/api/osc/test").status_code == 503
        assert client.post("/api/osc/settings", json={"mode": "off"}).status_code == 200

    def test_address_book(self, client, monkeypatch):
        monkeypatch.setattr(app, "get_workspace_scenes", lambda: [{"id": 3, "name": "Lights ON"}])
        monkeypatch.setattr(app, "get_workspace_chases", lambda: [{"id": 9, "name": "!!"}])
        data = client.get("/api/osc/addresses").get_json()
        assert data["scenes"] == [{"id": 3, "name": "Lights ON", "address": "/scene/3", "alias": "/scene/lights-on"}]
        assert data["chases"][0]["alias"] is None
