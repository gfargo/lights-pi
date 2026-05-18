"""Tests for the systemd unit-state helpers used by /api/diagnostics/system.

The diagnostics endpoint distinguishes "not_installed" (unit file missing)
from "inactive" (unit loaded but stopped) so the UI can render the optional
MCP server as "→ install" instead of "broken" when the user hasn't run the
installer. Pure-helper coverage for the parsing logic + the combined
state-resolution.
"""
from app import _parse_systemd_load_state, _systemd_unit_state


class TestParseSystemdLoadState:
    def test_loaded(self):
        assert _parse_systemd_load_state("LoadState=loaded") == "loaded"

    def test_not_found(self):
        assert _parse_systemd_load_state("LoadState=not-found") == "not-found"

    def test_masked(self):
        assert _parse_systemd_load_state("LoadState=masked") == "masked"

    def test_with_surrounding_whitespace(self):
        assert _parse_systemd_load_state("  LoadState=loaded  \n") == "loaded"

    def test_multiline_picks_loadstate(self):
        # systemctl show emits one property per line; we only want LoadState.
        output = "ActiveState=inactive\nLoadState=not-found\nSubState=dead\n"
        assert _parse_systemd_load_state(output) == "not-found"

    def test_empty_string(self):
        assert _parse_systemd_load_state("") == ""

    def test_none_input(self):
        # Real systemd output is always a string, but execute_command can return
        # None if the command errors — defensive handling.
        assert _parse_systemd_load_state(None) == ""

    def test_no_loadstate_line(self):
        assert _parse_systemd_load_state("Description=Something\n") == ""

    def test_malformed_line(self):
        # Just "LoadState" with no equals — shouldn't crash, just return "".
        assert _parse_systemd_load_state("LoadStateloaded") == ""


class TestSystemdUnitState:
    """_systemd_unit_state combines LoadState (existence) + is-active (runtime)."""

    @staticmethod
    def _mock_exec(responses):
        """Build an exec_fn that returns canned responses keyed by substring.

        responses: dict of (cmd-substring → {"output": "...", ...})
        Returned function picks the first matching key in the cmd.
        """
        def fn(cmd):
            for sub, resp in responses.items():
                if sub in cmd:
                    return resp
            return {"output": "", "success": False, "error": "no mock"}
        return fn

    def test_not_installed_short_circuits(self):
        """When LoadState=not-found, we return 'not_installed' without ever
        calling `systemctl is-active` (which would just say 'inactive' and
        lose information)."""
        exec_fn = self._mock_exec({
            "show -p LoadState": {"output": "LoadState=not-found\n"},
            # is-active should NOT be called; if it is, fail loudly.
            "is-active": {"output": "FAIL_should_not_run\n"},
        })
        assert _systemd_unit_state("lighting-mcp.service", exec_fn=exec_fn) == "not_installed"

    def test_masked_short_circuits(self):
        exec_fn = self._mock_exec({
            "show -p LoadState": {"output": "LoadState=masked\n"},
            "is-active": {"output": "inactive\n"},
        })
        assert _systemd_unit_state("foo.service", exec_fn=exec_fn) == "masked"

    def test_active(self):
        exec_fn = self._mock_exec({
            "show -p LoadState": {"output": "LoadState=loaded\n"},
            "is-active": {"output": "active\n"},
        })
        assert _systemd_unit_state("lighting-control.service", exec_fn=exec_fn) == "active"

    def test_inactive_loaded_but_stopped(self):
        exec_fn = self._mock_exec({
            "show -p LoadState": {"output": "LoadState=loaded\n"},
            "is-active": {"output": "inactive\n"},
        })
        assert _systemd_unit_state("foo.service", exec_fn=exec_fn) == "inactive"

    def test_failed(self):
        exec_fn = self._mock_exec({
            "show -p LoadState": {"output": "LoadState=loaded\n"},
            "is-active": {"output": "failed\n"},
        })
        assert _systemd_unit_state("foo.service", exec_fn=exec_fn) == "failed"

    def test_activating(self):
        exec_fn = self._mock_exec({
            "show -p LoadState": {"output": "LoadState=loaded\n"},
            "is-active": {"output": "activating\n"},
        })
        assert _systemd_unit_state("foo.service", exec_fn=exec_fn) == "activating"

    def test_unknown_when_both_empty(self):
        """No LoadState line and no is-active output — defensive fallback."""
        exec_fn = self._mock_exec({
            "show -p LoadState": {"output": ""},
            "is-active": {"output": ""},
        })
        assert _systemd_unit_state("foo.service", exec_fn=exec_fn) == "unknown"

    def test_is_active_output_stripped(self):
        """systemctl tends to add a trailing newline."""
        exec_fn = self._mock_exec({
            "show -p LoadState": {"output": "LoadState=loaded\n"},
            "is-active": {"output": "  active  \n"},
        })
        assert _systemd_unit_state("foo.service", exec_fn=exec_fn) == "active"
