"""SonzaiMemoryProvider — availability, lifecycle, recall, persist, tools."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from plugins.memory.sonzai import SonzaiMemoryProvider


# ─── availability ──────────────────────────────────────────────────────────


def test_is_available_when_env_set(clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    assert SonzaiMemoryProvider().is_available() is True


def test_is_available_when_unset(clean_env) -> None:
    assert SonzaiMemoryProvider().is_available() is False


def test_is_available_does_not_call_network(clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    import urllib.request

    def fail(*a, **kw):
        pytest.fail("network call")

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    SonzaiMemoryProvider().is_available()


# ─── lifecycle ─────────────────────────────────────────────────────────────


def _stub_client(get_context_return=None, **call_overrides) -> MagicMock:
    client = MagicMock()
    client.agents.create.return_value.agent_id = "agent_x"
    if get_context_return is not None:
        client.agents.get_context.return_value = get_context_return
    for key, value in call_overrides.items():
        setattr(client.agents, key, value)
    return client


def test_initialize_calls_sessions_start(hermes_home, clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch("plugins.memory.sonzai.provider.build_client") as bc:
        client = _stub_client()
        bc.return_value = client
        p = SonzaiMemoryProvider()
        p.initialize(session_id="sess_1", hermes_home=str(hermes_home))
        client.agents.sessions.start.assert_called_once()
        kwargs = client.agents.sessions.start.call_args.kwargs
        assert kwargs["session_id"] == "sess_1"
        assert kwargs["user_id"] == "owner"
        assert p._degraded is False


def test_initialize_does_not_raise_on_failure(hermes_home, clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch(
        "plugins.memory.sonzai.provider.build_client",
        side_effect=RuntimeError("net"),
    ):
        p = SonzaiMemoryProvider()
        p.initialize(session_id="sess_1", hermes_home=str(hermes_home))
        assert p._degraded is True


def test_shutdown_closes_client(hermes_home, clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch("plugins.memory.sonzai.provider.build_client") as bc:
        client = _stub_client()
        bc.return_value = client
        p = SonzaiMemoryProvider()
        p.initialize(session_id="sess_1", hermes_home=str(hermes_home))
        p.shutdown()
        client.close.assert_called_once()


# ─── config schema ─────────────────────────────────────────────────────────


def test_schema_marks_api_key_secret() -> None:
    schema = SonzaiMemoryProvider().get_config_schema()
    api_key_field = next(f for f in schema if f["key"] == "api_key")
    assert api_key_field["secret"] is True
    assert api_key_field["env_var"] == "SONZAI_API_KEY"
    assert api_key_field["required"] is True
    assert api_key_field["url"] == "https://sonz.ai"


def test_schema_includes_memory_mode_with_choices() -> None:
    schema = SonzaiMemoryProvider().get_config_schema()
    mode = next(f for f in schema if f["key"] == "memory_mode")
    assert "sync" in mode["choices"]
    assert "async" in mode["choices"]


# ─── prefetch (sync) ───────────────────────────────────────────────────────


def test_prefetch_returns_formatted_block(hermes_home, clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch("plugins.memory.sonzai.provider.build_client") as bc:
        client = _stub_client(
            get_context_return={"loaded_facts": [{"atomic_text": "fact A"}]}
        )
        bc.return_value = client
        p = SonzaiMemoryProvider()
        p.initialize(session_id="s", hermes_home=str(hermes_home))
        out = p.prefetch("what do you remember?")
        assert "fact A" in out
        client.agents.get_context.assert_called_once()


def test_prefetch_returns_empty_on_error(hermes_home, clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch("plugins.memory.sonzai.provider.build_client") as bc:
        client = _stub_client()
        client.agents.get_context.side_effect = RuntimeError("api down")
        bc.return_value = client
        p = SonzaiMemoryProvider()
        p.initialize(session_id="s", hermes_home=str(hermes_home))
        assert p.prefetch("anything") == ""


def test_prefetch_returns_empty_when_degraded(hermes_home, clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch(
        "plugins.memory.sonzai.provider.build_client",
        side_effect=RuntimeError("net"),
    ):
        p = SonzaiMemoryProvider()
        p.initialize(session_id="s", hermes_home=str(hermes_home))
        assert p.prefetch("x") == ""


# ─── prefetch (async deadline) ─────────────────────────────────────────────


def test_async_mode_returns_partial_under_deadline(hermes_home, clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    monkeypatch.setenv("SONZAI_MEMORY_MODE", "async")
    with patch("plugins.memory.sonzai.provider.build_client") as bc:
        client = _stub_client()

        def slow(*a, **k):
            time.sleep(5)
            return {"loaded_facts": [{"atomic_text": "too late"}]}

        client.agents.get_context.side_effect = slow
        bc.return_value = client
        p = SonzaiMemoryProvider()
        p.initialize(session_id="s", hermes_home=str(hermes_home))
        t0 = time.time()
        out = p.prefetch("x")
        elapsed = time.time() - t0
        # Deadline is 0.6s; allow some slack.
        assert elapsed < 1.5
        assert out == ""


def test_queue_prefetch_warms_cache(hermes_home, clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch("plugins.memory.sonzai.provider.build_client") as bc:
        client = _stub_client(
            get_context_return={"loaded_facts": [{"atomic_text": "warmed"}]}
        )
        bc.return_value = client
        p = SonzaiMemoryProvider()
        p.initialize(session_id="s", hermes_home=str(hermes_home))
        p.queue_prefetch("hello")
        # Give the background warm a moment.
        for _ in range(50):
            time.sleep(0.02)
            if "hello" in p._prefetch_cache:
                break
        out = p.prefetch("hello")
        assert "warmed" in out
        # Cache consumed — next call hits the API.
        assert "hello" not in p._prefetch_cache


# ─── sync_turn ─────────────────────────────────────────────────────────────


def test_sync_turn_is_non_blocking(hermes_home, clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch("plugins.memory.sonzai.provider.build_client") as bc:
        client = _stub_client()
        client.agents.process.side_effect = lambda *a, **k: time.sleep(2)
        bc.return_value = client
        p = SonzaiMemoryProvider()
        p.initialize(session_id="s", hermes_home=str(hermes_home))
        t0 = time.time()
        p.sync_turn("hello", "hi back")
        assert time.time() - t0 < 0.2


def test_sync_turn_swallows_errors(hermes_home, clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch("plugins.memory.sonzai.provider.build_client") as bc:
        client = _stub_client()
        client.agents.process.side_effect = RuntimeError("nope")
        bc.return_value = client
        p = SonzaiMemoryProvider()
        p.initialize(session_id="s", hermes_home=str(hermes_home))
        p.sync_turn("u", "a")  # must not raise


def test_sync_turn_passes_correct_messages(hermes_home, clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch("plugins.memory.sonzai.provider.build_client") as bc:
        client = _stub_client()
        bc.return_value = client
        p = SonzaiMemoryProvider()
        p.initialize(session_id="s", hermes_home=str(hermes_home))
        p.sync_turn("u-msg", "a-msg")
        p.shutdown()
        kwargs = client.agents.process.call_args.kwargs
        assert kwargs["messages"] == [
            {"role": "user", "content": "u-msg"},
            {"role": "assistant", "content": "a-msg"},
        ]


# ─── on_session_end ────────────────────────────────────────────────────────


def test_on_session_end_calls_sessions_end(hermes_home, clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch("plugins.memory.sonzai.provider.build_client") as bc:
        client = _stub_client()
        bc.return_value = client
        p = SonzaiMemoryProvider()
        p.initialize(session_id="s", hermes_home=str(hermes_home))
        p.on_session_end([{"role": "user", "content": "bye"}])
        client.agents.sessions.end.assert_called_once()


def test_on_session_end_swallows_errors(hermes_home, clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch("plugins.memory.sonzai.provider.build_client") as bc:
        client = _stub_client()
        client.agents.sessions.end.side_effect = RuntimeError("nope")
        bc.return_value = client
        p = SonzaiMemoryProvider()
        p.initialize(session_id="s", hermes_home=str(hermes_home))
        p.on_session_end([])  # must not raise


# ─── on_pre_compress ───────────────────────────────────────────────────────


def test_on_pre_compress_calls_process_only(hermes_home, clean_env, monkeypatch) -> None:
    """Default: provider only flushes process(); engine owns consolidate."""
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch("plugins.memory.sonzai.provider.build_client") as bc:
        client = _stub_client()
        bc.return_value = client
        p = SonzaiMemoryProvider()
        p.initialize(session_id="s", hermes_home=str(hermes_home))
        p.on_pre_compress(
            [
                {"role": "user", "content": "x"},
                {"role": "assistant", "content": "y"},
            ]
        )
        client.agents.process.assert_called_once()
        client.agents.consolidate.assert_not_called()


def test_on_pre_compress_consolidates_when_opted_in(
    hermes_home, clean_env, monkeypatch
) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    # Write the opt-in flag.
    (hermes_home / "sonzai.json").write_text(
        json.dumps({"also_consolidate": True, "agent_name": "x"})
    )
    with patch("plugins.memory.sonzai.provider.build_client") as bc:
        client = _stub_client()
        bc.return_value = client
        p = SonzaiMemoryProvider()
        p.initialize(session_id="s", hermes_home=str(hermes_home))
        p.on_pre_compress([{"role": "user", "content": "x"}])
        client.agents.process.assert_called_once()
        client.agents.consolidate.assert_called_once()


# ─── prompt + tools ────────────────────────────────────────────────────────


def test_system_prompt_block_mentions_sonzai() -> None:
    block = SonzaiMemoryProvider().system_prompt_block()
    assert "Sonzai" in block
    assert "sonzai-context" in block


def test_schemas_advertise_two_tools() -> None:
    names = {t["name"] for t in SonzaiMemoryProvider().get_tool_schemas()}
    assert names == {"sonzai_memory_search", "sonzai_memory_write"}


def test_handle_search_tool(hermes_home, clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch("plugins.memory.sonzai.provider.build_client") as bc:
        client = _stub_client()
        client.agents.memory.search.return_value = [{"fact": "x"}]
        bc.return_value = client
        p = SonzaiMemoryProvider()
        p.initialize(session_id="s", hermes_home=str(hermes_home))
        result = p.handle_tool_call("sonzai_memory_search", {"query": "anything"})
        assert json.loads(result) == [{"fact": "x"}]


def test_handle_write_tool(hermes_home, clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch("plugins.memory.sonzai.provider.build_client") as bc:
        client = _stub_client()
        client.agents.memory.create_fact.return_value = {"ok": True}
        bc.return_value = client
        p = SonzaiMemoryProvider()
        p.initialize(session_id="s", hermes_home=str(hermes_home))
        result = p.handle_tool_call("sonzai_memory_write", {"content": "new fact"})
        assert json.loads(result) == {"ok": True}


def test_handle_unknown_tool_returns_error(hermes_home, clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch("plugins.memory.sonzai.provider.build_client") as bc:
        bc.return_value = _stub_client()
        p = SonzaiMemoryProvider()
        p.initialize(session_id="s", hermes_home=str(hermes_home))
        result = json.loads(p.handle_tool_call("nope", {}))
        assert "error" in result


def test_handle_tool_call_swallows_errors(hermes_home, clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch("plugins.memory.sonzai.provider.build_client") as bc:
        client = _stub_client()
        client.agents.memory.search.side_effect = RuntimeError("nope")
        bc.return_value = client
        p = SonzaiMemoryProvider()
        p.initialize(session_id="s", hermes_home=str(hermes_home))
        result = json.loads(p.handle_tool_call("sonzai_memory_search", {"query": "q"}))
        assert "error" in result
