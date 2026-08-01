"""Tests for shared-password auth (issue #25)."""
import os
import sys
from pathlib import Path

import pytest

# Ensure control-server/ is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import _is_auth_exempt, _login_rate_check, _verify_password

# ---------------------------------------------------------------------------
# _verify_password — pure
# ---------------------------------------------------------------------------


class TestVerifyPassword:
    def test_match(self):
        assert _verify_password("secret", "secret") is True

    def test_mismatch(self):
        assert _verify_password("wrong", "secret") is False

    def test_none_expected_is_open_mode_never_matches(self):
        assert _verify_password("secret", None) is False

    def test_empty_expected_never_matches(self):
        assert _verify_password("secret", "") is False


# ---------------------------------------------------------------------------
# _is_auth_exempt — pure
# ---------------------------------------------------------------------------


class TestIsAuthExempt:
    @pytest.mark.parametrize(
        "path",
        [
            "/login",
            "/healthz",
            "/manifest.json",
            "/icon.svg",
            "/sw.js",
            "/logo",
            "/static/app.js",
        ],
    )
    def test_exempt_paths(self, path):
        assert _is_auth_exempt(path) is True

    @pytest.mark.parametrize("path", ["/", "/api/status", "/api/blackout", "/logout"])
    def test_guarded_paths(self, path):
        assert _is_auth_exempt(path) is False


# ---------------------------------------------------------------------------
# _login_rate_check — pure, injected state + clock
# ---------------------------------------------------------------------------


class TestLoginRateCheck:
    def test_allows_under_limit(self):
        state = {"1.2.3.4": [0, 1, 2, 3]}
        allowed, retry_after = _login_rate_check(state, "1.2.3.4", now=4)
        assert allowed is True
        assert retry_after == 0

    def test_unknown_ip_allowed(self):
        allowed, retry_after = _login_rate_check({}, "9.9.9.9", now=100)
        assert allowed is True
        assert retry_after == 0

    def test_fifth_failure_locks(self):
        state = {"1.2.3.4": [10, 20, 30, 40, 50]}
        allowed, retry_after = _login_rate_check(state, "1.2.3.4", now=50)
        assert allowed is False
        assert retry_after == 20

    def test_unlocks_after_60s(self):
        state = {"1.2.3.4": [10, 20, 30, 40, 50]}
        allowed, retry_after = _login_rate_check(state, "1.2.3.4", now=70)
        assert allowed is True
        assert retry_after == 0

    def test_lockout_is_per_ip(self):
        state = {"1.2.3.4": [10, 20, 30, 40, 50]}
        allowed, retry_after = _login_rate_check(state, "5.6.7.8", now=50)
        assert allowed is True
        assert retry_after == 0


# ---------------------------------------------------------------------------
# Flask integration — LIGHTS_PASSWORD set (auth enforced)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def auth_client():
    """Flask test client with LIGHTS_PASSWORD set."""
    os.environ["LIGHTS_PASSWORD"] = "test-pw-123"
    import importlib

    import app as _app_module
    importlib.reload(_app_module)
    _app_module.app.config["TESTING"] = True
    with _app_module.app.test_client() as client:
        yield client
    # Cleanup — reload with the env cleared so later test modules see open mode.
    os.environ.pop("LIGHTS_PASSWORD", None)
    importlib.reload(_app_module)


class TestAuthIntegrationFlow:
    def test_full_auth_flow(self, auth_client):
        # Unauthenticated JSON API access is blocked with 401.
        r_api = auth_client.get("/api/status")
        assert r_api.status_code == 401

        # Unauthenticated browser navigation redirects to /login.
        r_nav = auth_client.get("/", follow_redirects=False)
        assert r_nav.status_code == 302
        assert r_nav.headers["Location"].endswith("/login")

        # /healthz stays reachable even while unauthenticated (watchdog).
        r_health = auth_client.get("/healthz")
        assert r_health.status_code in (200, 503)

        # GET /login renders the form.
        r_form = auth_client.get("/login")
        assert r_form.status_code == 200

        # Wrong password is rejected.
        r_wrong = auth_client.post("/login", data={"password": "wrong"})
        assert r_wrong.status_code == 401

        # Correct password sets the session cookie and redirects home.
        r_ok = auth_client.post(
            "/login", data={"password": "test-pw-123"}, follow_redirects=False
        )
        assert r_ok.status_code == 302
        assert r_ok.headers["Location"] == "/"

        # Now authenticated.
        r_status = auth_client.get("/api/status")
        assert r_status.status_code == 200

        # Logout clears the session; guarded routes are blocked again.
        auth_client.post("/logout")
        r_after_logout = auth_client.get("/api/status")
        assert r_after_logout.status_code == 401


class TestLoginRateLimitIntegration:
    def test_lockout_after_five_failures(self, auth_client):
        import app as _app_module

        _app_module._LOGIN_ATTEMPTS.clear()
        for _ in range(5):
            r = auth_client.post("/login", data={"password": "wrong"})
            assert r.status_code == 401
        r_locked = auth_client.post("/login", data={"password": "wrong"})
        assert r_locked.status_code == 429
        _app_module._LOGIN_ATTEMPTS.clear()


# ---------------------------------------------------------------------------
# Flask integration — LIGHTS_PASSWORD unset (open mode, backwards compat)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def open_client():
    """Flask test client with LIGHTS_PASSWORD unset — everything open."""
    os.environ.pop("LIGHTS_PASSWORD", None)
    import importlib

    import app as _app_module
    importlib.reload(_app_module)
    _app_module.app.config["TESTING"] = True
    with _app_module.app.test_client() as client:
        yield client
    importlib.reload(_app_module)


class TestOpenModeBackCompat:
    def test_status_reachable_without_login(self, open_client):
        r = open_client.get("/api/status")
        assert r.status_code == 200

    def test_index_reachable_without_login(self, open_client):
        r = open_client.get("/")
        assert r.status_code == 200

    def test_login_route_redirects_home(self, open_client):
        r = open_client.get("/login", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["Location"] == "/"
