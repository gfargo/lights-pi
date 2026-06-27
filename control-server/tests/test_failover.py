"""Tests for AI provider failover helpers.

Covers _parse_failover_chain, _is_failover_error, circuit-breaker helpers,
and the _run_chat_with_failover orchestrator.  All helpers are pure or accept
an explicit 'now' timestamp so tests never sleep.
"""
import requests as _requests
from app import (
    _BREAKER_COOLDOWN_S,
    _BREAKER_THRESHOLD,
    _breaker_is_open,
    _breaker_record_failure,
    _breaker_record_success,
    _is_failover_error,
    _parse_failover_chain,
    _run_chat_with_failover,
)

# ── _parse_failover_chain ──────────────────────────────────────────────────────

class TestParseFailoverChain:
    def test_ordered_pair(self):
        assert _parse_failover_chain("anthropic,openai", "openai") == ["anthropic", "openai"]

    def test_reversed_pair(self):
        assert _parse_failover_chain("openai,anthropic", "anthropic") == ["openai", "anthropic"]

    def test_whitespace_and_case(self):
        assert _parse_failover_chain("  Anthropic , OpenAI  ", "openai") == ["anthropic", "openai"]

    def test_drops_ollama(self):
        assert _parse_failover_chain("anthropic,ollama,openai", "openai") == ["anthropic", "openai"]

    def test_drops_garbage(self):
        assert _parse_failover_chain("anthropic,garbage,openai", "openai") == ["anthropic", "openai"]

    def test_empty_returns_default(self):
        assert _parse_failover_chain("", "anthropic") == ["anthropic"]

    def test_whitespace_only_returns_default(self):
        assert _parse_failover_chain("   ", "openai") == ["openai"]

    def test_dedupes(self):
        assert _parse_failover_chain("openai,openai,anthropic", "openai") == ["openai", "anthropic"]

    def test_invalid_default_returns_empty(self):
        assert _parse_failover_chain("", "ollama") == []

    def test_all_garbage_falls_back_to_default(self):
        assert _parse_failover_chain("garbage,junk", "anthropic") == ["anthropic"]

    def test_single_provider(self):
        assert _parse_failover_chain("anthropic", "openai") == ["anthropic"]


# ── _is_failover_error ────────────────────────────────────────────────────────

class TestIsFailoverError:
    def _http_error(self, status: int) -> _requests.HTTPError:
        resp = _requests.Response()
        resp.status_code = status
        return _requests.HTTPError(response=resp)

    def test_timeout_is_failover(self):
        assert _is_failover_error(_requests.Timeout()) is True

    def test_connection_error_is_failover(self):
        assert _is_failover_error(_requests.ConnectionError()) is True

    def test_500_is_failover(self):
        assert _is_failover_error(self._http_error(500)) is True

    def test_502_is_failover(self):
        assert _is_failover_error(self._http_error(502)) is True

    def test_503_is_failover(self):
        assert _is_failover_error(self._http_error(503)) is True

    def test_429_is_failover(self):
        assert _is_failover_error(self._http_error(429)) is True

    def test_400_is_not_failover(self):
        assert _is_failover_error(self._http_error(400)) is False

    def test_401_is_not_failover(self):
        assert _is_failover_error(self._http_error(401)) is False

    def test_404_is_not_failover(self):
        assert _is_failover_error(self._http_error(404)) is False

    def test_unrelated_exception_is_not_failover(self):
        assert _is_failover_error(ValueError("oops")) is False


# ── circuit breaker ───────────────────────────────────────────────────────────

class TestCircuitBreaker:
    def test_initially_closed(self):
        assert _breaker_is_open({}, "anthropic", 1000.0) is False

    def test_below_threshold_stays_closed(self):
        state = {}
        for _ in range(_BREAKER_THRESHOLD - 1):
            _breaker_record_failure(state, "anthropic", 1000.0)
        assert _breaker_is_open(state, "anthropic", 1000.0) is False

    def test_threshold_opens_breaker(self):
        state = {}
        for _ in range(_BREAKER_THRESHOLD):
            _breaker_record_failure(state, "anthropic", 1000.0)
        assert _breaker_is_open(state, "anthropic", 1000.0) is True

    def test_breaker_still_open_within_cooldown(self):
        state = {}
        now = 1000.0
        for _ in range(_BREAKER_THRESHOLD):
            _breaker_record_failure(state, "anthropic", now)
        assert _breaker_is_open(state, "anthropic", now + _BREAKER_COOLDOWN_S - 1) is True

    def test_breaker_closed_after_cooldown(self):
        state = {}
        now = 1000.0
        for _ in range(_BREAKER_THRESHOLD):
            _breaker_record_failure(state, "anthropic", now)
        assert _breaker_is_open(state, "anthropic", now + _BREAKER_COOLDOWN_S + 1) is False

    def test_success_resets_open_breaker(self):
        state = {}
        for _ in range(_BREAKER_THRESHOLD):
            _breaker_record_failure(state, "anthropic", 1000.0)
        assert _breaker_is_open(state, "anthropic", 1000.0) is True
        _breaker_record_success(state, "anthropic")
        assert _breaker_is_open(state, "anthropic", 1000.0) is False

    def test_independent_per_provider(self):
        state = {}
        for _ in range(_BREAKER_THRESHOLD):
            _breaker_record_failure(state, "anthropic", 1000.0)
        assert _breaker_is_open(state, "anthropic", 1000.0) is True
        assert _breaker_is_open(state, "openai", 1000.0) is False


# ── _run_chat_with_failover ────────────────────────────────────────────────────

def _success(provider="openai"):
    return {
        "messages": [{"role": "assistant", "content": [{"type": "text", "text": "ok"}]}],
        "tool_calls": [],
        "stop_reason": "end_turn",
    }


def _failover_err(provider="anthropic"):
    return {
        "messages": [],
        "tool_calls": [],
        "stop_reason": "error",
        "error": f"{provider} 503",
        "should_failover": True,
        "http_status": 503,
    }


def _hard_err(provider="anthropic"):
    return {
        "messages": [],
        "tool_calls": [],
        "stop_reason": "error",
        "error": f"{provider} 401 Unauthorized",
        "should_failover": False,
        "http_status": 401,
    }


_MSGS = [{"role": "user", "content": "hi"}]


class TestRunChatWithFailover:
    def _patch(self, monkeypatch, chain, ant_key="key-a", oai_key="key-o"):
        monkeypatch.setattr("app._AI_FAILOVER_CHAIN", chain)
        monkeypatch.setattr("app._ANTHROPIC_API_KEY", ant_key)
        monkeypatch.setattr("app._ANTHROPIC_MODEL", "claude-sonnet-4-6")
        monkeypatch.setattr("app._OPENAI_API_KEY", oai_key)
        monkeypatch.setattr("app._OPENAI_MODEL", "gpt-4.1")
        monkeypatch.setattr("app._provider_breaker", {})

    def test_primary_success_no_switch(self, monkeypatch):
        self._patch(monkeypatch, ["anthropic", "openai"])
        monkeypatch.setattr("app._anthropic_chat_loop", lambda *a, **kw: _success("anthropic"))

        result = _run_chat_with_failover(_MSGS, [])
        assert result["served_by"] == "anthropic"
        assert result["switched"] is False

    def test_primary_fails_secondary_succeeds(self, monkeypatch):
        self._patch(monkeypatch, ["anthropic", "openai"])
        monkeypatch.setattr("app._anthropic_chat_loop", lambda *a, **kw: _failover_err("anthropic"))
        monkeypatch.setattr("app._openai_chat_loop", lambda *a, **kw: _success("openai"))

        result = _run_chat_with_failover(_MSGS, [])
        assert result["served_by"] == "openai"
        assert result["switched"] is True

    def test_hard_error_does_not_failover(self, monkeypatch):
        self._patch(monkeypatch, ["anthropic", "openai"])
        oai_called = []
        monkeypatch.setattr("app._anthropic_chat_loop", lambda *a, **kw: _hard_err("anthropic"))
        monkeypatch.setattr("app._openai_chat_loop", lambda *a, **kw: (oai_called.append(1), _success())[1])

        result = _run_chat_with_failover(_MSGS, [])
        assert result["stop_reason"] == "error"
        assert oai_called == []

    def test_open_breaker_skips_provider(self, monkeypatch):
        self._patch(monkeypatch, ["anthropic", "openai"])
        # Pre-open the anthropic breaker.
        monkeypatch.setattr("app._provider_breaker", {"anthropic": {"fails": 3, "open_until": 9_999_999_999.0}})
        ant_called = []
        oai_called = []
        monkeypatch.setattr("app._anthropic_chat_loop", lambda *a, **kw: (ant_called.append(1), _success())[1])
        monkeypatch.setattr("app._openai_chat_loop", lambda *a, **kw: (oai_called.append(1), _success("openai"))[1])

        result = _run_chat_with_failover(_MSGS, [])
        assert ant_called == []
        assert oai_called == [1]
        assert result["served_by"] == "openai"

    def test_all_fail_returns_error(self, monkeypatch):
        self._patch(monkeypatch, ["anthropic", "openai"])
        monkeypatch.setattr("app._anthropic_chat_loop", lambda *a, **kw: _failover_err("anthropic"))
        monkeypatch.setattr("app._openai_chat_loop", lambda *a, **kw: _failover_err("openai"))

        result = _run_chat_with_failover(_MSGS, [])
        assert result["stop_reason"] == "error"
        assert result["served_by"] is None
        assert len(result["attempts"]) == 2

    def test_missing_key_skipped(self, monkeypatch):
        self._patch(monkeypatch, ["anthropic", "openai"], ant_key="")
        monkeypatch.setattr("app._openai_chat_loop", lambda *a, **kw: _success("openai"))

        result = _run_chat_with_failover(_MSGS, [])
        assert result["served_by"] == "openai"
        assert any(a.get("skipped") == "no_api_key" for a in result["attempts"])

    def test_history_with_tool_use_round_trip(self, monkeypatch):
        """A history that already contains tool_use/tool_result pairs is forwarded correctly."""
        self._patch(monkeypatch, ["anthropic", "openai"])
        history_with_tools = [
            {"role": "user", "content": "run a tool"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "tu1", "name": "list_fixtures", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "[]"}]},
        ]
        received_msgs = []
        monkeypatch.setattr(
            "app._anthropic_chat_loop",
            lambda msgs, *a, **kw: (received_msgs.append(list(msgs)), _success())[1],
        )

        _run_chat_with_failover(history_with_tools, [])
        assert received_msgs[0] == history_with_tools
