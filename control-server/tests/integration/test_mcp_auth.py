"""Bearer-token auth for the MCP server's /mcp endpoint (issue #25)."""


class TestBearerOk:
    """Pure predicate — no ASGI/uvicorn needed."""

    def test_correct_header_matches(self):
        import server as mcp_module

        assert mcp_module._bearer_ok("Bearer sekrit", "sekrit") is True

    def test_wrong_token_rejected(self):
        import server as mcp_module

        assert mcp_module._bearer_ok("Bearer wrong", "sekrit") is False

    def test_missing_header_rejected(self):
        import server as mcp_module

        assert mcp_module._bearer_ok(None, "sekrit") is False

    def test_missing_bearer_prefix_rejected(self):
        import server as mcp_module

        assert mcp_module._bearer_ok("sekrit", "sekrit") is False

    def test_empty_header_rejected(self):
        import server as mcp_module

        assert mcp_module._bearer_ok("", "sekrit") is False


class TestBearerMiddlewareIntegration:
    """Drives the real Starlette app through the ASGI middleware."""

    def test_rejects_missing_and_wrong_token_allows_correct(self):
        import server as mcp_module
        from starlette.testclient import TestClient

        app = mcp_module.mcp.streamable_http_app()
        app.add_middleware(mcp_module._BearerAuthMiddleware, token="sekrit")

        with TestClient(app) as client:
            r_missing = client.post("/mcp", json={})
            assert r_missing.status_code == 401

            r_wrong = client.post(
                "/mcp", headers={"Authorization": "Bearer wrong"}, json={}
            )
            assert r_wrong.status_code == 401

            r_ok = client.post(
                "/mcp",
                headers={
                    "Authorization": "Bearer sekrit",
                    "Accept": "application/json, text/event-stream",
                },
                json={
                    "jsonrpc": "2.0",
                    "method": "initialize",
                    "id": 1,
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0"},
                    },
                },
            )
            assert r_ok.status_code == 200
