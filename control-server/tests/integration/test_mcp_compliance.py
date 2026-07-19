"""Suite 2 — MCP protocol compliance tests.

Boots the FastMCP server in-process and replaces its HTTP client with an
httpx.Client backed by a WSGITransport pointing at the Flask test app.  Tool
functions then exercise real Flask routes without any network.

What's covered:
  - Tool count == 48 (drift detector: adding/removing a tool must update this)
  - Discovery tools (_get paths): get_status, list_fixtures, list_groups,
    list_scenes, list_templates, get_channel_values
  - Action tools (_post paths): adjust_brightness, adjust_color, blackout,
    set_channel
  - Cue list tools: list_cue_lists, get_active_cue_lists
  - Response shape: every called tool returns a dict (never raises)

NOTE: The 48-tool assertion is intentionally brittle — it IS the drift
detector.  When you add a new @mcp.tool() legitimately, update the count here.
"""
import asyncio

EXPECTED_TOOL_COUNT = 48


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

class TestToolRegistry:
    def test_tool_count(self, mcp_flask_client):
        """All 48 @mcp.tool() decorators must be registered on the FastMCP instance."""
        mcp_module, _, _ = mcp_flask_client
        tools = asyncio.run(mcp_module.mcp.list_tools())
        assert len(tools) == EXPECTED_TOOL_COUNT, (
            f"Expected {EXPECTED_TOOL_COUNT} MCP tools, found {len(tools)}. "
            "If you added/removed a tool intentionally, update EXPECTED_TOOL_COUNT."
        )

    def test_tool_names_are_unique(self, mcp_flask_client):
        mcp_module, _, _ = mcp_flask_client
        tools = asyncio.run(mcp_module.mcp.list_tools())
        names = [t.name for t in tools]
        assert len(names) == len(set(names)), "Duplicate tool names detected"

    def test_all_tools_have_descriptions(self, mcp_flask_client):
        mcp_module, _, _ = mcp_flask_client
        tools = asyncio.run(mcp_module.mcp.list_tools())
        missing = [t.name for t in tools if not (t.description or "").strip()]
        assert not missing, f"Tools missing descriptions: {missing}"


# ---------------------------------------------------------------------------
# Discovery tools (_get path)
# ---------------------------------------------------------------------------

class TestDiscoveryTools:
    def test_get_status_returns_dict(self, mcp_flask_client):
        mcp_module, _, _ = mcp_flask_client
        result = mcp_module.get_status()
        assert isinstance(result, dict)

    def test_list_fixtures_returns_dict(self, mcp_flask_client):
        mcp_module, _, _ = mcp_flask_client
        result = mcp_module.list_fixtures()
        assert isinstance(result, dict)
        # The test workspace has 2 fixtures
        assert "fixtures" in result
        assert len(result["fixtures"]) == 2

    def test_list_groups_returns_dict(self, mcp_flask_client):
        mcp_module, _, _ = mcp_flask_client
        result = mcp_module.list_groups()
        assert isinstance(result, dict)

    def test_list_scenes_returns_dict(self, mcp_flask_client):
        mcp_module, _, _ = mcp_flask_client
        result = mcp_module.list_scenes()
        assert isinstance(result, dict)

    def test_list_templates_returns_dict(self, mcp_flask_client):
        mcp_module, _, _ = mcp_flask_client
        result = mcp_module.list_templates()
        assert isinstance(result, dict)

    def test_get_channel_values_returns_dict(self, mcp_flask_client):
        mcp_module, _, _ = mcp_flask_client
        result = mcp_module.get_channel_values()
        assert isinstance(result, dict)
        # "values" key present even when QLC+ is not running
        assert "values" in result


# ---------------------------------------------------------------------------
# Action tools (_post path)
# ---------------------------------------------------------------------------

class TestActionTools:
    def test_adjust_brightness_forwards_to_flask(self, mcp_flask_client):
        mcp_module, _, recorded = mcp_flask_client
        recorded.clear()
        result = mcp_module.adjust_brightness("200")
        assert isinstance(result, dict)
        assert "success" in result
        # At least one CH| frame should have been sent for the 2 test fixtures
        assert any(cmd.startswith("CH|") for cmd in recorded)

    def test_adjust_color_returns_dict(self, mcp_flask_client):
        mcp_module, _, _ = mcp_flask_client
        result = mcp_module.adjust_color("warm")
        assert isinstance(result, dict)
        assert "success" in result

    def test_blackout_forwards_to_flask(self, mcp_flask_client):
        mcp_module, _, recorded = mcp_flask_client
        recorded.clear()
        result = mcp_module.blackout()
        assert isinstance(result, dict)
        assert result.get("success") is True
        # All frames must be zero-value
        assert all(cmd.endswith("|0") for cmd in recorded)

    def test_set_channel_known_fixture(self, mcp_flask_client):
        mcp_module, _, recorded = mcp_flask_client
        recorded.clear()
        result = mcp_module.set_channel(fixture_id=1, channel=0, value=128)
        assert isinstance(result, dict)
        assert result.get("success") is True
        assert recorded == ["CH|1|128"]

    def test_set_channel_unknown_fixture_surfaces_error(self, mcp_flask_client):
        mcp_module, _, _ = mcp_flask_client
        result = mcp_module.set_channel(fixture_id=9999, channel=0, value=0)
        assert isinstance(result, dict)
        # _post surfaces 4xx bodies as success=False rather than raising
        assert result.get("success") is False

    def test_batch_action_returns_dict(self, mcp_flask_client):
        mcp_module, _, _ = mcp_flask_client
        result = mcp_module.batch_action(actions=[
            {"action": "adjust_brightness", "parameters": {"value": "100"}},
        ])
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Cue list tools
# ---------------------------------------------------------------------------

class TestCueListTools:
    def test_list_cue_lists_returns_dict(self, mcp_flask_client):
        mcp_module, _, _ = mcp_flask_client
        result = mcp_module.list_cue_lists()
        assert isinstance(result, dict)
        assert "cue_lists" in result

    def test_get_active_cue_lists_returns_dict(self, mcp_flask_client):
        mcp_module, _, _ = mcp_flask_client
        result = mcp_module.get_active_cue_lists()
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Group management tools
# ---------------------------------------------------------------------------

class TestGroupTools:
    def test_create_and_delete_group(self, mcp_flask_client):
        mcp_module, _, _ = mcp_flask_client
        result = mcp_module.create_group(name="test-group", fixtures=[1, 2])
        assert isinstance(result, dict)
        # Verify group appears in list
        groups = mcp_module.list_groups()
        assert "test-group" in str(groups)
        # Clean up
        mcp_module.delete_group("test-group")
