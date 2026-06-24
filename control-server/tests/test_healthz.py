"""Unit tests for the _healthz_status helper and /healthz route."""
from pathlib import Path
from unittest.mock import MagicMock

from app import _healthz_status


def _fake_ws(closed=False):
    ws = MagicMock()
    ws.closed = closed
    return ws


def _minimal_workspace(tmp_path):
    p = tmp_path / "test.qxw"
    p.write_text('<Workspace xmlns="http://www.qlcplus.org/Workspace"></Workspace>')
    return p


def _readable(dev):
    return True


def _unreadable(dev):
    return False


class TestAllGreen:
    def test_all_critical_ok(self, tmp_path):
        ws_path = _minimal_workspace(tmp_path)
        payload, ok = _healthz_status(
            qlc_ws=_fake_ws(closed=False),
            last_dmx_ts=990.0,
            workspace_path=ws_path,
            dmx_device_glob=["/dev/ttyUSB0"],
            dmx_readable_fn=_readable,
            now=1000.0,
        )
        assert ok is True
        assert payload["flask"] is True
        assert payload["qlc_ws"] is True
        assert payload["workspace_loaded"] is True
        assert payload["last_dmx_write_age_s"] == 10.0
        assert payload["dmx_device"] == "/dev/ttyUSB0"


class TestQlcWsDisconnected:
    def test_closed_ws_returns_503(self, tmp_path):
        ws_path = _minimal_workspace(tmp_path)
        payload, ok = _healthz_status(
            qlc_ws=_fake_ws(closed=True),
            last_dmx_ts=None,
            workspace_path=ws_path,
            dmx_device_glob=[],
            now=1000.0,
        )
        assert ok is False
        assert payload["qlc_ws"] is False

    def test_none_ws_returns_503(self, tmp_path):
        ws_path = _minimal_workspace(tmp_path)
        payload, ok = _healthz_status(
            qlc_ws=None,
            last_dmx_ts=None,
            workspace_path=ws_path,
            dmx_device_glob=[],
            now=1000.0,
        )
        assert ok is False
        assert payload["qlc_ws"] is False


class TestWorkspace:
    def test_missing_workspace_returns_503(self, tmp_path):
        payload, ok = _healthz_status(
            qlc_ws=_fake_ws(closed=False),
            last_dmx_ts=None,
            workspace_path=tmp_path / "missing.qxw",
            dmx_device_glob=["/dev/ttyUSB0"],
            dmx_readable_fn=_readable,
            now=1000.0,
        )
        assert ok is False
        assert payload["workspace_loaded"] is False

    def test_unparseable_workspace_returns_503(self, tmp_path):
        bad = tmp_path / "bad.qxw"
        bad.write_text("not xml at all")
        payload, ok = _healthz_status(
            qlc_ws=_fake_ws(closed=False),
            last_dmx_ts=None,
            workspace_path=bad,
            dmx_device_glob=["/dev/ttyUSB0"],
            dmx_readable_fn=_readable,
            now=1000.0,
        )
        assert ok is False
        assert payload["workspace_loaded"] is False


class TestDmxAge:
    def test_no_dmx_writes_age_is_none(self):
        payload, _ = _healthz_status(
            qlc_ws=_fake_ws(),
            last_dmx_ts=None,
            workspace_path=Path("/nonexistent"),
            dmx_device_glob=[],
            now=1000.0,
        )
        assert payload["last_dmx_write_age_s"] is None

    def test_age_computed_correctly(self):
        payload, _ = _healthz_status(
            qlc_ws=_fake_ws(),
            last_dmx_ts=974.5,
            workspace_path=Path("/nonexistent"),
            dmx_device_glob=[],
            now=1000.0,
        )
        assert payload["last_dmx_write_age_s"] == 25.5

    def test_age_rounded_to_one_decimal(self):
        payload, _ = _healthz_status(
            qlc_ws=_fake_ws(),
            last_dmx_ts=999.999,
            workspace_path=Path("/nonexistent"),
            dmx_device_glob=[],
            now=1000.0,
        )
        assert payload["last_dmx_write_age_s"] == 0.0


class TestDmxDevice:
    def test_no_devices(self, tmp_path):
        payload, _ = _healthz_status(
            qlc_ws=_fake_ws(),
            last_dmx_ts=None,
            workspace_path=_minimal_workspace(tmp_path),
            dmx_device_glob=[],
            now=1000.0,
        )
        assert payload["dmx_device"] is False

    def test_first_device_returned(self, tmp_path):
        payload, _ = _healthz_status(
            qlc_ws=_fake_ws(),
            last_dmx_ts=None,
            workspace_path=_minimal_workspace(tmp_path),
            dmx_device_glob=["/dev/ttyUSB0", "/dev/ttyUSB1"],
            dmx_readable_fn=_readable,
            now=1000.0,
        )
        assert payload["dmx_device"] == "/dev/ttyUSB0"

    def test_unreadable_device_reports_false(self, tmp_path):
        payload, _ = _healthz_status(
            qlc_ws=_fake_ws(),
            last_dmx_ts=None,
            workspace_path=_minimal_workspace(tmp_path),
            dmx_device_glob=["/dev/ttyUSB0"],
            dmx_readable_fn=_unreadable,
            now=1000.0,
        )
        assert payload["dmx_device"] is False

    def test_readable_device_reported(self, tmp_path):
        payload, _ = _healthz_status(
            qlc_ws=_fake_ws(),
            last_dmx_ts=None,
            workspace_path=_minimal_workspace(tmp_path),
            dmx_device_glob=["/dev/ttyUSB0"],
            dmx_readable_fn=_readable,
            now=1000.0,
        )
        assert payload["dmx_device"] == "/dev/ttyUSB0"


class TestFlaskAlwaysTrue:
    def test_flask_field_always_true(self, tmp_path):
        payload, _ = _healthz_status(
            qlc_ws=None,
            last_dmx_ts=None,
            workspace_path=_minimal_workspace(tmp_path),
            dmx_device_glob=[],
            now=1000.0,
        )
        assert payload["flask"] is True
