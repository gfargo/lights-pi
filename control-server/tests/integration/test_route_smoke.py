"""Every Flask route answers without a server error.

Each route in the app's URL map is called once through the Flask test
client, against the mocked QLC+ link and the tmp_path data directory from
``patched_app``:

- URL parameters get a value that names nothing (``missing``, ``999``),
  so lookups take their not-found path;
- POST, PATCH and DELETE send an empty JSON object, so validation takes its
  bad-request path.

A 2xx, 3xx or 4xx is a pass; a 5xx or an exception is a failure, except a
503 from a route in ``UNAVAILABLE_OK`` (it reports a service that is down). That catches
the commonest route regressions: a renamed helper, a ``KeyError`` on a
missing field, a ``None`` that used to be a dict.

Routes that can't be called this way are listed in ``SKIPPED`` with the
reason. A new route is picked up automatically; if it can't run here, add it
to ``SKIPPED`` and say why. ``test_skip_list_is_current`` fails when an entry
no longer matches a route, so the list doesn't rot.
"""
import re

import pytest

SKIPPED = {
    ("GET", "/api/events"): "Server-Sent Events stream; never finishes",
    ("POST", "/api/chat"): "Calls the AI provider",
    ("POST", "/api/command"): "Calls the AI provider",
    ("POST", "/api/diagnostics/rf_scan"): "Scans Wi-Fi with system tools",
    ("POST", "/api/audio/enable"): "Opens the microphone",
    ("POST", "/api/audio/calibrate"): "Opens the microphone",
    ("POST", "/api/audio/chase/<path:chase_id>/start"): "Opens the microphone",
    ("POST", "/api/diagnostics/test_dmx"): (
        "Writes to the QLC+ WebSocket directly rather than through "
        "_qlc_send_commands, so it needs the real link"
    ),
}

# Routes that answer 503 on purpose when a service is down, which it always
# is in this suite (no QLC+ link, no OSC listener).
UNAVAILABLE_OK = {
    ("GET", "/healthz"),
    ("POST", "/api/osc/test"),
}

_PLACEHOLDER = {"int": "999", "path": "missing", "default": "missing"}


def _routes():
    import app as app_module

    for rule in app_module.app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            yield method, rule.rule


def _fill(rule: str) -> str:
    def sub(match):
        converter = match.group(1) or "default"
        return _PLACEHOLDER.get(converter, "missing")

    return re.sub(r"<(?:(\w+):)?\w+>", sub, rule)


_ALL = sorted(set(_routes()))


@pytest.mark.parametrize(
    "method,rule",
    [r for r in _ALL if r not in SKIPPED],
    ids=[f"{m} {r}" for m, r in _ALL if (m, r) not in SKIPPED],
)
def test_route_does_not_error(method, rule, flask_client):
    client, _recorded = flask_client
    path = _fill(rule)
    if method == "GET":
        response = client.get(path)
    else:
        response = client.open(path, method=method, json={})
    if (method, rule) in UNAVAILABLE_OK and response.status_code == 503:
        return
    assert response.status_code < 500, (
        f"{method} {path} returned {response.status_code}: "
        f"{response.get_data(as_text=True)[:300]}"
    )


def test_skip_list_is_current():
    listed = set(SKIPPED) | UNAVAILABLE_OK
    stale = sorted(f"{m} {r}" for m, r in listed if (m, r) not in _ALL)
    assert not stale, f"These lists name routes that no longer exist: {stale}"
