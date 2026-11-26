"""Every MCP tool calls a Flask route that exists, with the right method.

The MCP server is a thin wrapper: each tool turns its arguments into one or
more HTTP calls to the control server. When a Flask route is renamed, or its
method changes, the tool keeps importing fine and only fails when an agent
calls it. This suite catches that drift for all tools at once:

1. List the registered tools and build arguments for each from its input
   schema (required parameters only, with placeholder values).
2. Point the server's HTTP client at a recording transport that answers
   ``{}``, so nothing reaches Flask or QLC+ and no lights change.
3. Call the tool and check every recorded (method, path) against Flask's
   URL map.

A new tool needs no change here; it is picked up from the registry.
"""
import asyncio
import json

import pytest
from werkzeug.exceptions import MethodNotAllowed, NotFound

# Placeholder per JSON-schema type. Strings get a value that is also a valid
# URL segment so path-building tools produce a matchable path.
_PLACEHOLDER = {
    "string": "probe",
    "integer": 1,
    "number": 0.5,
    "boolean": False,
    "array": [],
    "object": {},
}


def _schema_type(prop: dict) -> str:
    if "type" in prop:
        return prop["type"]
    for option in prop.get("anyOf", []):
        if option.get("type") not in (None, "null"):
            return option["type"]
    return "string"


def _args_for(tool) -> dict:
    schema = tool.inputSchema or {}
    props = schema.get("properties", {})
    return {
        name: _PLACEHOLDER[_schema_type(props[name])]
        for name in schema.get("required", [])
    }


@pytest.fixture(scope="module")
def mcp_module():
    import server

    return server


@pytest.fixture(scope="module")
def tools(mcp_module):
    return asyncio.run(mcp_module.mcp.list_tools())


@pytest.fixture(scope="module")
def url_adapter():
    import app as app_module

    return app_module.app.url_map.bind("testserver")


@pytest.fixture
def recorder(mcp_module):
    """Swap the MCP server's HTTP client for one that records and replies ``{}``.

    Recording at the transport catches every request, including tools that
    call ``_http()`` directly for PATCH, DELETE or query parameters.
    """
    import httpx

    calls: list[tuple[str, str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        return httpx.Response(200, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://testserver")
    original = mcp_module._client
    mcp_module._client = client
    try:
        yield calls
    finally:
        mcp_module._client = original
        client.close()


def _tool_names():
    import server

    return sorted(t.name for t in asyncio.run(server.mcp.list_tools()))


@pytest.mark.parametrize("tool_name", _tool_names())
def test_tool_calls_existing_flask_route(tool_name, tools, mcp_module, recorder, url_adapter):
    tool = next(t for t in tools if t.name == tool_name)
    fn = getattr(mcp_module, tool_name)
    fn(**_args_for(tool))

    assert recorder, f"{tool_name} made no request to the control server"
    for method, path, _payload in recorder:
        route_path = path
        try:
            url_adapter.match(route_path, method=method)
        except MethodNotAllowed:
            pytest.fail(f"{tool_name}: {method} {route_path} exists but not for {method}")
        except NotFound:
            pytest.fail(f"{tool_name}: {method} {route_path} has no Flask route")


def test_request_bodies_are_json_objects(tools, mcp_module, recorder):
    """Every POST and PATCH body is a dict, so Flask's ``request.get_json()`` sees an object."""
    for tool in tools:
        recorder.clear()
        getattr(mcp_module, tool.name)(**_args_for(tool))
        for method, path, payload in recorder:
            if method in ("POST", "PATCH"):
                assert payload is None or isinstance(payload, dict), (
                    f"{tool.name}: {method} {path} body is {type(payload).__name__}"
                )


# ---------------------------------------------------------------------------
# Body keys: a key the MCP tool sends must be one Flask reads
# ---------------------------------------------------------------------------

def _all_args_for(tool) -> dict:
    """Every parameter, optional ones included, so every body key is sent."""
    props = (tool.inputSchema or {}).get("properties", {})
    return {name: _PLACEHOLDER[_schema_type(prop)] for name, prop in props.items()}


def _handler_source(app_module, endpoint: str) -> str:
    """Source of the Flask view plus every app.py function it calls by name.

    Views often hand the body to a helper (``/api/action`` passes it to
    ``execute_lighting_action``), so one level of callees is included.
    """
    import inspect
    import re

    view = app_module.app.view_functions[endpoint]
    source = inspect.getsource(view)
    parts = [source]
    for name in set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", source)):
        fn = getattr(app_module, name, None)
        if inspect.isfunction(fn) and fn.__module__ == app_module.__name__:
            try:
                parts.append(inspect.getsource(fn))
            except (OSError, TypeError):
                pass
    return "\n".join(parts)


def test_body_keys_are_read_by_flask(tools, mcp_module, recorder, url_adapter):
    """Catches a parameter renamed on one side only.

    For every POST/PATCH body, each top-level key has to appear as a string
    literal in the handling view or a helper it calls. A tool sending
    ``"react_to"`` to a route that now reads ``"trigger"`` fails here.
    """
    import app as app_module

    unread = []
    for tool in tools:
        recorder.clear()
        getattr(mcp_module, tool.name)(**_all_args_for(tool))
        for method, path, payload in recorder:
            if method not in ("POST", "PATCH") or not isinstance(payload, dict):
                continue
            endpoint, _ = url_adapter.match(path, method=method)
            source = _handler_source(app_module, endpoint)
            for key in payload:
                if f'"{key}"' not in source and f"'{key}'" not in source:
                    unread.append(f"{tool.name}: {method} {path} sends {key!r}")
    assert not unread, "Body keys no Flask handler reads:\n" + "\n".join(sorted(set(unread)))
