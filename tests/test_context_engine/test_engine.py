"""SonzaiContextEngine — token tracking, threshold, lifecycle, compress."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from plugins.context_engine.sonzai import SonzaiContextEngine
from plugins.memory.sonzai._common import SonzaiConfig


# ─── token tracking ────────────────────────────────────────────────────────


def test_update_from_response_sets_counters() -> None:
    e = SonzaiContextEngine()
    e.update_from_response(
        {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    )
    assert e.last_prompt_tokens == 100
    assert e.last_completion_tokens == 50
    assert e.last_total_tokens == 150


def test_update_from_response_handles_empty() -> None:
    e = SonzaiContextEngine()
    e.update_from_response({})
    assert e.last_prompt_tokens == 0


# ─── threshold ─────────────────────────────────────────────────────────────


def test_threshold_recomputed_on_update_model() -> None:
    e = SonzaiContextEngine()
    e.update_model("gpt-4o", context_length=128_000)
    assert e.threshold_tokens == int(128_000 * 0.75)


def test_should_compress_below_threshold() -> None:
    e = SonzaiContextEngine()
    e.update_model("gpt-4o", context_length=128_000)
    assert e.should_compress(prompt_tokens=80_000) is False


def test_should_compress_at_threshold() -> None:
    e = SonzaiContextEngine()
    e.update_model("gpt-4o", context_length=128_000)
    assert e.should_compress(prompt_tokens=96_000) is True


def test_should_compress_uses_last_prompt_tokens_when_arg_omitted() -> None:
    e = SonzaiContextEngine()
    e.update_model("gpt-4o", context_length=128_000)
    e.last_prompt_tokens = 100_000
    assert e.should_compress() is True


def test_should_compress_false_before_update_model() -> None:
    e = SonzaiContextEngine()
    assert e.should_compress(prompt_tokens=999_999) is False


# ─── lifecycle ─────────────────────────────────────────────────────────────


def _stub_client() -> MagicMock:
    client = MagicMock()
    client.agents.create.return_value.agent_id = "agent_x"
    client.agents.get_context.return_value = {"loaded_facts": [{"atomic_text": "F"}]}
    return client


def test_on_session_start_calls_sessions_start(hermes_home, clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch("plugins.context_engine.sonzai.engine.build_client") as bc:
        client = _stub_client()
        bc.return_value = client
        e = SonzaiContextEngine()
        e.on_session_start("sess_x", hermes_home=str(hermes_home))
        client.agents.sessions.start.assert_called_once()
        assert e._degraded is False


def test_on_session_start_degrades_on_failure(hermes_home, clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch(
        "plugins.context_engine.sonzai.engine.build_client",
        side_effect=RuntimeError("boom"),
    ):
        e = SonzaiContextEngine()
        e.on_session_start("sess_x", hermes_home=str(hermes_home))
        assert e._degraded is True


def test_on_session_end_ends_and_closes(hermes_home, clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch("plugins.context_engine.sonzai.engine.build_client") as bc:
        client = _stub_client()
        bc.return_value = client
        e = SonzaiContextEngine()
        e.on_session_start("sess_x", hermes_home=str(hermes_home))
        e.on_session_end("sess_x", [])
        client.agents.sessions.end.assert_called_once()
        client.close.assert_called_once()


def test_on_session_reset_clears_counters() -> None:
    e = SonzaiContextEngine()
    e.last_prompt_tokens = 100
    e.compression_count = 3
    e.on_session_reset()
    assert e.last_prompt_tokens == 0
    assert e.compression_count == 0


# ─── compress ──────────────────────────────────────────────────────────────


def _attach_client(engine: SonzaiContextEngine, **overrides) -> MagicMock:
    client = MagicMock()
    client.agents.get_context.return_value = {"loaded_facts": [{"atomic_text": "F"}]}
    for k, v in overrides.items():
        setattr(client.agents, k, v)
    engine._client = client
    engine._agent_id = "agent"
    engine._user_id = "user"
    engine._session_id = "sess"
    engine._config = SonzaiConfig(api_key="x", context_token_budget=500)
    engine.context_length = 8000
    engine._degraded = False
    return client


def test_compress_calls_process_then_consolidate_then_get_context() -> None:
    e = SonzaiContextEngine()
    client = _attach_client(e)
    messages = [{"role": "user", "content": f"msg {i}"} for i in range(50)]
    e.compress(messages, focus_topic="travel")

    # Order matters
    names = [
        c[0]
        for c in client.agents.mock_calls
        if c[0] in {"process", "consolidate", "get_context"}
    ]
    assert names == ["process", "consolidate", "get_context"]


def test_compress_returns_system_then_recency_tail() -> None:
    e = SonzaiContextEngine()
    _attach_client(e)
    messages = [{"role": "user", "content": f"msg-{i}"} for i in range(20)]
    out = e.compress(messages)
    assert out[0]["role"] == "system"
    assert "<sonzai-context>" in out[0]["content"]
    # Last N raw turns preserved verbatim
    assert out[-1] == messages[-1]
    assert out[-6:] == messages[-6:]


def test_compress_increments_count() -> None:
    e = SonzaiContextEngine()
    _attach_client(e)
    e.compress([])
    e.compress([])
    assert e.compression_count == 2


def test_compress_returns_messages_when_degraded() -> None:
    e = SonzaiContextEngine()
    e._degraded = True
    msgs = [{"role": "user", "content": "hi"}]
    assert e.compress(msgs) == msgs
    assert e.compression_count == 1


def test_compress_swallows_errors() -> None:
    e = SonzaiContextEngine()
    client = _attach_client(e)
    client.agents.process.side_effect = RuntimeError("p")
    client.agents.consolidate.side_effect = RuntimeError("c")
    client.agents.get_context.side_effect = RuntimeError("g")
    # Should still produce a list (recency tail only).
    msgs = [{"role": "user", "content": f"m{i}"} for i in range(10)]
    out = e.compress(msgs)
    assert isinstance(out, list)
    assert out[-1] == msgs[-1]


def test_compress_focus_topic_passed_to_get_context() -> None:
    e = SonzaiContextEngine()
    client = _attach_client(e)
    e.compress([{"role": "user", "content": "old"}], focus_topic="travel-plans")
    kwargs = client.agents.get_context.call_args.kwargs
    assert kwargs["query"] == "travel-plans"


def test_compress_via_session_boundary_when_opted_in(hermes_home, clean_env) -> None:
    (hermes_home / "sonzai.json").write_text(
        json.dumps({"compress_via_session_boundary": True})
    )
    e = SonzaiContextEngine()
    e._hermes_home = str(hermes_home)
    client = _attach_client(e)
    messages = [{"role": "user", "content": "hello"}]
    out = e.compress(messages)

    # Should call sessions.end + sessions.start + get_context — NOT process/consolidate.
    names = [
        c[0]
        for c in client.agents.mock_calls
        if c[0] in {"process", "consolidate", "get_context", "sessions.end", "sessions.start"}
    ]
    assert names == ["sessions.end", "sessions.start", "get_context"]
    # wait=True forced on the end call
    end_kwargs = client.agents.sessions.end.call_args.kwargs
    assert end_kwargs["wait"] is True
    # session rotates so next get_context targets the fresh session
    assert e._session_id != "sess"
    # still produces a system + tail
    assert out[0]["role"] == "system"


def test_compress_via_session_boundary_default_off() -> None:
    """Without the opt-in flag, the three-call path runs."""
    e = SonzaiContextEngine()
    client = _attach_client(e)
    # 10 messages so the recency-tail (6) leaves a slice to process.
    e.compress([{"role": "user", "content": f"m{i}"} for i in range(10)])
    names = [c[0] for c in client.agents.mock_calls if c[0] in {"process", "consolidate", "get_context", "sessions.end"}]
    assert "sessions.end" not in names
    assert "process" in names
    assert "consolidate" in names


def test_compress_uses_last_user_msg_as_query_default() -> None:
    e = SonzaiContextEngine()
    client = _attach_client(e)
    e.compress(
        [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "old-a"},
            {"role": "user", "content": "latest user query"},
        ]
    )
    kwargs = client.agents.get_context.call_args.kwargs
    assert kwargs["query"] == "latest user query"


# ─── status ────────────────────────────────────────────────────────────────


def test_get_status_returns_engine_snapshot() -> None:
    e = SonzaiContextEngine()
    e.update_model("gpt-4o", context_length=128_000)
    e.update_from_response(
        {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    )
    e.compression_count = 2
    status = e.get_status()
    assert status["engine"] == "sonzai"
    # ABC-compatible field names (match Hermes ContextEngine.get_status default)
    assert status["compression_count"] == 2
    assert status["last_prompt_tokens"] == 100
    assert status["threshold_tokens"] == int(128_000 * 0.75)
    assert status["context_length"] == 128_000
    assert "usage_percent" in status
