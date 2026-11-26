"""Shared fixtures for the integration test suite.

Wires up:
  - Flask test client with mocked QLC+ WebSocket sender (recording mock)
  - Module-level paths redirected to tmp_path so tests never touch ~/.qlcplus
  - httpx.Client via WSGITransport for MCP compliance tests
  - Fake-clock helpers for cue playback tests
"""
import asyncio
import sys
from pathlib import Path

import pytest

_INTEGRATION_DIR = Path(__file__).resolve().parent
_FIXTURES_DIR = _INTEGRATION_DIR / "fixtures"

# Ensure control-server/ is importable as 'app'
_CONTROL_SERVER_DIR = _INTEGRATION_DIR.parent.parent
if str(_CONTROL_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_CONTROL_SERVER_DIR))

# Ensure mcp-server/ is importable as 'server'
_MCP_SERVER_DIR = _CONTROL_SERVER_DIR.parent / "mcp-server"
if str(_MCP_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_MCP_SERVER_DIR))


# Module-level paths in app.py that point into ~/.qlcplus.
_DATA_FILES = (
    "AUDIO_CHASES_FILE",
    "CUE_AUDIO_DIR",
    "CUE_LISTS_FILE",
    "GROUPS_FILE",
    "LAST_LOOK_FILE",
    "MIDI_MAPPINGS_FILE",
    "OSC_SETTINGS_FILE",
    "RF_HISTORY_FILE",
    "RF_SETTINGS_FILE",
    "STAGE_LAYOUT_FILE",
)


@pytest.fixture(scope="session")
def test_workspace():
    """Minimal .qxw fixture with two test fixtures (dimmer + RGB)."""
    return _FIXTURES_DIR / "test_workspace.qxw"


@pytest.fixture
def patched_app(monkeypatch, tmp_path, test_workspace):
    """Flask app module with:
    - WORKSPACE_PATH → a tmp_path copy of the test fixture
    - every ~/.qlcplus data file and the chat DB → tmp_path
    - _qlc_run → local asyncio.run (no background thread)
    - _qlc_send_commands → recording mock (appends CH|… strings to a list)
    Returns (flask_app_object, recorded_commands_list).
    """
    import shutil

    import app as app_module
    import chat_store

    # Everything the app keeps under ~/.qlcplus goes to tmp_path instead, so
    # a route test can create, save and delete without touching the
    # developer's real rig data. The workspace is a copy, so writes are safe.
    data_dir = tmp_path / ".qlcplus"
    data_dir.mkdir()
    workspace = data_dir / test_workspace.name
    shutil.copy(test_workspace, workspace)
    monkeypatch.setattr(app_module, "WORKSPACE_DIR", data_dir)
    monkeypatch.setattr(app_module, "WORKSPACE_PATH", workspace)
    monkeypatch.setattr(app_module, "_WORKSPACE_POINTER", data_dir / "current_workspace")
    for name in _DATA_FILES:
        if hasattr(app_module, name):
            original = getattr(app_module, name)
            monkeypatch.setattr(app_module, name, data_dir / Path(original).name)

    chat_db = data_dir / "chat_history.db"
    chat_store.init_db(chat_db)
    monkeypatch.setattr(app_module, "CHAT_DB_PATH", chat_db)

    (data_dir / "fixture_groups.json").write_text("{}")
    (data_dir / "cue_lists.json").write_text('{"cue_lists": []}')

    recorded: list[str] = []

    async def _mock_send_commands(commands):
        recorded.extend(commands)

    def _mock_qlc_run(coro, timeout=10):
        # Run on a fresh event loop — safe because Flask routes are sync (no
        # running loop at call time). The background WS thread is never started.
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        except Exception:
            # Propagate so callers (e.g. get_current_channel_values) can catch
            raise
        finally:
            loop.close()

    monkeypatch.setattr(app_module, "_qlc_send_commands", _mock_send_commands)
    monkeypatch.setattr(app_module, "_qlc_run", _mock_qlc_run)

    return app_module.app, recorded


@pytest.fixture
def flask_client(patched_app):
    """Flask test client + command recorder.  Returns (client, recorded_list)."""
    flask_app, recorded = patched_app
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as client:
        yield client, recorded


@pytest.fixture
def mcp_flask_client(patched_app):
    """httpx.Client routing through the Flask WSGI app.

    Replaces the MCP server's global _client so tool functions hit the real
    Flask routes in-process (no network, no QLC+ WebSocket needed).
    Returns (mcp_server_module, flask_app_httpx_client, recorded_commands_list).
    """
    import httpx
    import server as mcp_module

    flask_app, recorded = patched_app
    flask_app.config["TESTING"] = True

    transport = httpx.WSGITransport(app=flask_app)
    test_client = httpx.Client(transport=transport, base_url="http://testserver")

    # Inject test client into the MCP server's global (bypasses the lazy init)
    original_client = mcp_module._client
    mcp_module._client = test_client
    try:
        yield mcp_module, test_client, recorded
    finally:
        mcp_module._client = original_client
        test_client.close()
