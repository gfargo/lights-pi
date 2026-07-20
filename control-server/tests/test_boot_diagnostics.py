"""Tests for boot/power diagnostics pure helpers and the boot-restore
decision logic: _decode_throttled, _filter_dmx_usb_lines,
_analyze_boot_history, _parse_last_look, _should_restore_look."""
import json

from app import (
    BOOT_RESTORE_MAX_UPTIME_S,
    _analyze_boot_history,
    _decode_throttled,
    _filter_dmx_usb_lines,
    _last_look_file,
    _parse_last_look,
    _parse_systemd_show_property,
    _should_restore_look,
)


class TestParseSystemdShowProperty:
    def test_extracts_value(self):
        out = "ActiveEnterTimestampMonotonic=123456789\n"
        assert _parse_systemd_show_property(out, "ActiveEnterTimestampMonotonic") == "123456789"

    def test_missing_key(self):
        assert _parse_systemd_show_property("Other=x\n", "NRestarts") == ""
        assert _parse_systemd_show_property("", "NRestarts") == ""
        assert _parse_systemd_show_property(None, "NRestarts") == ""


class TestDecodeThrottled:
    def test_clean(self):
        result = _decode_throttled("throttled=0x0")
        assert result["ok"] is True
        assert result["raw"] == "0x0"
        assert result["issues"] == []

    def test_undervoltage_now_and_since_boot(self):
        # 0x50005 = bits 0, 2, 16, 18 — the classic bad-PSU signature
        result = _decode_throttled("throttled=0x50005")
        assert result["ok"] is False
        assert result["issues"] == [
            "undervoltage_now",
            "throttled_now",
            "undervoltage_since_boot",
            "throttled_since_boot",
        ]

    def test_since_boot_only(self):
        # 0x50000 = brownout happened earlier but is not happening now
        result = _decode_throttled("0x50000")
        assert result["ok"] is False
        assert result["issues"] == ["undervoltage_since_boot", "throttled_since_boot"]

    def test_bare_hex_without_prefix_key(self):
        assert _decode_throttled("0x0")["ok"] is True

    def test_garbage_returns_none(self):
        assert _decode_throttled("vcgencmd: command not found") is None
        assert _decode_throttled("") is None
        assert _decode_throttled(None) is None


class TestFilterDmxUsbLines:
    # Real lsusb output from the Pi: the ENTTEC's FT232 has no
    # FTDI/ENTTEC/DMX substring — only the vendor id gives it away.
    FT232 = "Bus 001 Device 004: ID 0403:6001 Future Technology Devices International, Ltd FT232 Serial (UART) IC"
    HUB = "Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub"
    ETH = "Bus 001 Device 003: ID 0424:ec00 Microchip Technology, Inc. (formerly SMSC) SMSC9512/9514 Fast Ethernet Adapter"

    def test_matches_ftdi_vendor_id(self):
        assert _filter_dmx_usb_lines([self.HUB, self.ETH, self.FT232]) == [self.FT232]

    def test_matches_by_name_too(self):
        line = "Bus 001 Device 005: ID 0403:6001 ENTTEC DMX USB PRO"
        assert _filter_dmx_usb_lines([line]) == [line]

    def test_no_match(self):
        assert _filter_dmx_usb_lines([self.HUB, self.ETH]) == []


class TestAnalyzeBootHistory:
    CLEAN_TAIL = "Stopped Session 1 of User riversway.\nsystemd-shutdown[1]: Syncing filesystems\nJournal stopped"
    ABRUPT_TAIL = 'GET /api/channel_values HTTP/1.1" 200 -\nGET /api/channel_values HTTP/1.1" 200 -'
    ORPHAN_KERNEL = "EXT4-fs (mmcblk0p2): orphan cleanup on readonly fs"

    def test_clean_shutdown(self):
        result = _analyze_boot_history(self.CLEAN_TAIL, "")
        assert result["previous_boot_unclean"] is False
        assert result["evidence"] == []

    def test_abrupt_end_detected_from_journal(self):
        result = _analyze_boot_history(self.ABRUPT_TAIL, "")
        assert result["previous_boot_unclean"] is True
        assert "shutdown sequence" in result["evidence"][0]

    def test_fs_evidence_detected(self):
        result = _analyze_boot_history(self.CLEAN_TAIL, self.ORPHAN_KERNEL)
        assert result["previous_boot_unclean"] is True
        assert "orphan cleanup" in result["evidence"]

    def test_nothing_to_judge(self):
        result = _analyze_boot_history("", "")
        assert result["previous_boot_unclean"] is None


class TestParseLastLook:
    def test_valid(self):
        text = json.dumps({"values": {"1": 34, "2": 255}, "saved_at": "2026-07-18T12:00:00"})
        assert _parse_last_look(text) == {1: 34, 2: 255}

    def test_clamps_and_drops_invalid(self):
        text = json.dumps({"values": {"1": 999, "0": 50, "x": 10, "3": "40"}})
        assert _parse_last_look(text) == {1: 255, 3: 40}

    def test_garbage(self):
        assert _parse_last_look("not json") == {}
        assert _parse_last_look(json.dumps({"values": [1, 2]})) == {}
        assert _parse_last_look(json.dumps([])) == {}


class TestShouldRestoreLook:
    SAVED = {1: 34, 2: 255}
    DARK = {1: 0, 2: 0, 3: 0}

    def test_restores_on_fresh_boot_when_dark(self):
        assert _should_restore_look(60, self.DARK, self.SAVED) is True

    def test_skips_when_not_fresh_boot(self):
        assert _should_restore_look(BOOT_RESTORE_MAX_UPTIME_S + 1, self.DARK, self.SAVED) is False
        assert _should_restore_look(None, self.DARK, self.SAVED) is False

    def test_skips_when_output_already_lit(self):
        assert _should_restore_look(60, {1: 0, 2: 128}, self.SAVED) is False

    def test_empty_fetch_is_not_evidence_of_dark(self):
        assert _should_restore_look(60, {}, self.SAVED) is False

    def test_skips_without_saved_look(self):
        assert _should_restore_look(60, self.DARK, {}) is False


class TestLastLookFile:
    """A saved look must not leak across a workspace switch: different
    workspaces can have entirely different fixture patches, so replaying
    another venue's raw channel values via the restart-triggered restore
    (load_workspace restarts qlcplus-web) is exactly the bug this scoping
    prevents. Unlike fixture groups, there's no cross-workspace fallback
    except for the pre-workspace-switching 'default' workspace."""

    def test_new_workspace_gets_its_own_unwritten_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.WORKSPACE_DIR", tmp_path)
        monkeypatch.setattr("app.LAST_LOOK_FILE", tmp_path / "last_look.json")
        monkeypatch.setattr("app._active_workspace_name", lambda: "venue-b")
        result = _last_look_file()
        assert result == tmp_path / "last_look.venue-b.json"
        assert not result.exists()

    def test_uses_existing_per_workspace_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.WORKSPACE_DIR", tmp_path)
        monkeypatch.setattr("app.LAST_LOOK_FILE", tmp_path / "last_look.json")
        monkeypatch.setattr("app._active_workspace_name", lambda: "venue-a")
        scoped = tmp_path / "last_look.venue-a.json"
        scoped.write_text('{"values": {}}')
        assert _last_look_file() == scoped

    def test_default_workspace_falls_back_to_legacy_global_file(self, tmp_path, monkeypatch):
        """Upgrading installs shouldn't lose crash-recovery on their first
        restart after this change ships."""
        monkeypatch.setattr("app.WORKSPACE_DIR", tmp_path)
        legacy = tmp_path / "last_look.json"
        legacy.write_text('{"values": {"1": 255}}')
        monkeypatch.setattr("app.LAST_LOOK_FILE", legacy)
        monkeypatch.setattr("app._active_workspace_name", lambda: "default")
        assert _last_look_file() == legacy

    def test_non_default_workspace_ignores_legacy_global_file(self, tmp_path, monkeypatch):
        """The legacy-file fallback only applies to 'default' — any other
        workspace gets its own empty scoped path, never the old global
        snapshot (which could be from an unrelated fixture patch)."""
        monkeypatch.setattr("app.WORKSPACE_DIR", tmp_path)
        legacy = tmp_path / "last_look.json"
        legacy.write_text('{"values": {"1": 255}}')
        monkeypatch.setattr("app.LAST_LOOK_FILE", legacy)
        monkeypatch.setattr("app._active_workspace_name", lambda: "venue-b")
        assert _last_look_file() == tmp_path / "last_look.venue-b.json"
