"""Fake-Hermes lifecycle harness.

Drives the two plugins through the documented per-turn lifecycle with the
Sonzai SDK fully mocked. Asserts hook ordering, payload shapes, and the
final rebuild shape. Catches regressions that the unit tests' isolated
assertions can miss.

Usage::

    python scripts/verify_lifecycle.py

Exits non-zero on any failure. Prints a numbered trace so the failure
location is obvious.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _load_our_class(plugin_subpath: str, class_name: str):
    """Load a class from one of our plugin packages by file path.

    We use a dotted module name that matches the on-disk path
    (``plugins.memory.sonzai``) so that ``unittest.mock.patch`` targets like
    ``"plugins.memory.sonzai.provider.build_client"`` still resolve. This
    relies on Hermes NOT being on ``sys.path`` for this harness — the
    lifecycle test is plugin-side only and never imports Hermes code.
    """
    import importlib.util

    init_file = REPO_ROOT / plugin_subpath
    package_dir = init_file.parent
    # Recreate the natural dotted path: plugins/memory/sonzai → plugins.memory.sonzai
    relative = init_file.relative_to(REPO_ROOT).parent
    module_name = ".".join(relative.parts)
    if module_name in sys.modules:
        return getattr(sys.modules[module_name], class_name)
    spec = importlib.util.spec_from_file_location(
        module_name,
        str(init_file),
        submodule_search_locations=[str(package_dir)],
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return getattr(mod, class_name)


def _make_client() -> MagicMock:
    """A fully mocked Sonzai client wired with sane return shapes."""
    client = MagicMock()
    client.agents.create.return_value.agent_id = "agent_test"
    client.agents.get_context.return_value = {
        "personality_prompt": "test-persona",
        "loaded_facts": [{"atomic_text": "user-said-marker"}],
        "recent_turns": [
            {"role": "user", "content": "earlier turn", "timestamp": "t"},
        ],
    }
    client.agents.memory.search.return_value = {"results": [{"content": "fact-A"}]}
    client.agents.memory.create_fact.return_value = {"ok": True}
    return client


def _check(label: str, cond: bool, detail: str = "") -> None:
    mark = "✓" if cond else "✘"
    print(f"  {mark} {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        raise AssertionError(f"FAILED: {label}" + (f" — {detail}" if detail else ""))


def drive_memory_provider(hermes_home: Path) -> None:
    print("\n[memory provider] full lifecycle:")
    SonzaiMemoryProvider = _load_our_class("plugins/memory/sonzai/__init__.py", "SonzaiMemoryProvider")

    with patch("plugins.memory.sonzai.provider.build_client") as bc, patch.dict(
        "os.environ", {"SONZAI_API_KEY": "sk_fake"}, clear=False
    ):
        client = _make_client()
        bc.return_value = client

        p = SonzaiMemoryProvider()

        # availability
        _check("is_available() returns True with SONZAI_API_KEY set", p.is_available() is True)

        # initialize
        p.initialize(session_id="user:nas@sonz.ai/sess-1", hermes_home=str(hermes_home))
        _check("initialize: NOT degraded", p._degraded is False)
        _check(
            "initialize: sessions.start called once",
            client.agents.sessions.start.call_count == 1,
        )
        start_kwargs = client.agents.sessions.start.call_args.kwargs
        _check(
            "initialize: user_id parsed from session_id prefix",
            start_kwargs["user_id"] == "nas@sonz.ai",
            f"got {start_kwargs['user_id']}",
        )

        # system prompt block — static
        block = p.system_prompt_block()
        _check("system_prompt_block mentions Sonzai", "Sonzai" in block)

        # prefetch (sync mode)
        out = p.prefetch("what do you remember?", session_id="user:nas@sonz.ai/sess-1")
        _check("prefetch returned non-empty", bool(out))
        _check("prefetch returns wrapped <sonzai-context> block", out.startswith("<sonzai-context>"))
        _check("prefetch surfaces server fact", "user-said-marker" in out)
        _check("prefetch surfaces recent_turns", "earlier turn" in out)
        _check("prefetch called get_context exactly once", client.agents.get_context.call_count == 1)

        # queue_prefetch — should warm cache without re-hitting on next call
        client.agents.get_context.reset_mock()
        p.queue_prefetch("future-q", session_id="user:nas@sonz.ai/sess-1")
        # Give the background thread up to 1s to land.
        for _ in range(50):
            time.sleep(0.02)
            if "future-q" in p._prefetch_cache:
                break
        _check("queue_prefetch warmed the cache", "future-q" in p._prefetch_cache)
        next_call_count_before = client.agents.get_context.call_count
        warm = p.prefetch("future-q", session_id="user:nas@sonz.ai/sess-1")
        _check("warm prefetch served from cache (no extra API call)",
               client.agents.get_context.call_count == next_call_count_before,
               f"before={next_call_count_before}, after={client.agents.get_context.call_count}")
        _check("warm prefetch payload non-empty", bool(warm))

        # sync_turn — must be non-blocking
        t0 = time.time()
        p.sync_turn("hello", "hi back", session_id="user:nas@sonz.ai/sess-1")
        elapsed = time.time() - t0
        _check("sync_turn returns immediately (< 100ms)", elapsed < 0.1,
               f"took {elapsed*1000:.1f}ms")
        # Give the daemon thread a moment to call process().
        for _ in range(50):
            time.sleep(0.02)
            if client.agents.process.called:
                break
        _check("sync_turn fired process() in background", client.agents.process.called)
        proc_kwargs = client.agents.process.call_args.kwargs
        _check("sync_turn shipped both user + assistant messages",
               proc_kwargs["messages"] == [
                   {"role": "user", "content": "hello"},
                   {"role": "assistant", "content": "hi back"},
               ])

        # handle_tool_call — search
        search_result = p.handle_tool_call("sonzai_memory_search", {"query": "anything"}, extra="ignored")
        _check("memory_search returns JSON string", isinstance(search_result, str))
        _check("memory_search payload deserialises",
               json.loads(search_result) == {"results": [{"content": "fact-A"}]})

        # handle_tool_call — write
        write_result = p.handle_tool_call("sonzai_memory_write", {"content": "new fact"})
        _check("memory_write payload acknowledges", json.loads(write_result) == {"ok": True})

        # handle_tool_call — unknown
        err_result = json.loads(p.handle_tool_call("nope", {}))
        _check("unknown tool returns error JSON", "error" in err_result)

        # on_pre_compress — must return str per ABC
        ret = p.on_pre_compress([
            {"role": "user", "content": "old1"},
            {"role": "assistant", "content": "old2"},
        ])
        _check("on_pre_compress returns str (ABC contract)", isinstance(ret, str))
        _check("on_pre_compress called process()", client.agents.process.call_count >= 2)
        _check("on_pre_compress did NOT call consolidate (engine owns it)",
               client.agents.consolidate.call_count == 0)

        # on_session_end
        p.on_session_end([])
        _check("on_session_end called sessions.end", client.agents.sessions.end.called)

        # shutdown
        p.shutdown()
        _check("shutdown closed client", client.close.called)


def drive_context_engine(hermes_home: Path) -> None:
    print("\n[context engine] full lifecycle:")
    SonzaiContextEngine = _load_our_class("plugins/context_engine/sonzai/__init__.py", "SonzaiContextEngine")

    with patch("plugins.context_engine.sonzai.engine.build_client") as bc, patch.dict(
        "os.environ", {"SONZAI_API_KEY": "sk_fake"}, clear=False
    ):
        client = _make_client()
        bc.return_value = client

        e = SonzaiContextEngine()

        # on_session_start
        e.on_session_start("user:nas@sonz.ai/sess-2", hermes_home=str(hermes_home))
        _check("on_session_start: NOT degraded", e._degraded is False)
        _check("on_session_start: sessions.start called",
               client.agents.sessions.start.call_count == 1)

        # update_model — recompute threshold
        e.update_model("gpt-4o", context_length=128_000)
        _check("update_model set context_length", e.context_length == 128_000)
        _check("update_model set threshold_tokens = 0.75 * context",
               e.threshold_tokens == 96_000)

        # update_from_response
        e.update_from_response({"prompt_tokens": 100_001, "completion_tokens": 50, "total_tokens": 100_051})
        _check("update_from_response sets last_prompt_tokens", e.last_prompt_tokens == 100_001)

        # should_compress
        _check("should_compress True when over threshold", e.should_compress() is True)
        _check("should_compress respects explicit arg",
               e.should_compress(prompt_tokens=10) is False)

        # compress — 3-call chain
        messages = [{"role": "user", "content": f"m{i}"} for i in range(20)]
        compressed = e.compress(messages, focus_topic="travel")

        # Call order check
        agent_calls = [
            c[0] for c in client.agents.mock_calls
            if c[0] in {"process", "consolidate", "get_context"}
        ]
        _check("compress hit process → consolidate → get_context in order",
               agent_calls == ["process", "consolidate", "get_context"],
               f"got {agent_calls}")
        _check("compress incremented compression_count", e.compression_count == 1)
        _check("rebuilt[0] is role=system", compressed[0]["role"] == "system")
        _check("rebuilt[0] contains <sonzai-context>",
               "<sonzai-context>" in compressed[0]["content"])
        _check("recency tail preserved verbatim",
               compressed[-1] == messages[-1])
        _check("recency tail length matches protect_last_n",
               compressed[-e.protect_last_n:] == messages[-e.protect_last_n:])

        # get_context was queried with the focus_topic
        gc_kwargs = client.agents.get_context.call_args.kwargs
        _check("compress passed focus_topic to get_context",
               gc_kwargs.get("query") == "travel")

        # get_status
        status = e.get_status()
        _check("get_status returns required keys",
               {"last_prompt_tokens", "threshold_tokens",
                "context_length", "compression_count"}.issubset(status))

        # on_session_reset
        e.on_session_reset()
        _check("on_session_reset cleared compression_count", e.compression_count == 0)
        _check("on_session_reset cleared last_prompt_tokens", e.last_prompt_tokens == 0)

        # on_session_end
        e.on_session_end("user:nas@sonz.ai/sess-2", [])
        _check("on_session_end called sessions.end", client.agents.sessions.end.called)
        _check("on_session_end closed client", client.close.called)


def drive_cooperation(hermes_home: Path) -> None:
    """Both plugins active — same agent_id, no double-consolidation."""
    print("\n[cooperation] memory + engine share identity, engine owns consolidate:")
    SonzaiContextEngine = _load_our_class("plugins/context_engine/sonzai/__init__.py", "SonzaiContextEngine")
    SonzaiMemoryProvider = _load_our_class("plugins/memory/sonzai/__init__.py", "SonzaiMemoryProvider")

    with patch("plugins.memory.sonzai.provider.build_client") as mbc, patch(
        "plugins.context_engine.sonzai.engine.build_client"
    ) as ebc, patch.dict("os.environ", {"SONZAI_API_KEY": "sk_fake"}, clear=False):
        mem_client = _make_client()
        eng_client = _make_client()
        mbc.return_value = mem_client
        ebc.return_value = eng_client

        m = SonzaiMemoryProvider()
        e = SonzaiContextEngine()

        m.initialize(session_id="sess-coop", hermes_home=str(hermes_home))
        e.on_session_start("sess-coop", hermes_home=str(hermes_home))

        _check("memory not degraded", m._degraded is False)
        _check("engine not degraded", e._degraded is False)
        _check("both resolved same agent_id (each plugin's own _common, identical logic)",
               m._agent_id == e._agent_id == "agent_test")

        # Memory provider's on_pre_compress should NOT call consolidate by default —
        # that's the engine's job. Use real messages to ensure process fires.
        m.on_pre_compress([
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": "a"},
        ])
        _check("memory on_pre_compress: process called",
               mem_client.agents.process.called)
        _check("memory on_pre_compress: consolidate NOT called (engine owns it)",
               mem_client.agents.consolidate.call_count == 0)

        # Engine's compress IS allowed to consolidate.
        msgs = [{"role": "user", "content": f"m{i}"} for i in range(20)]
        e.compress(msgs)
        _check("engine compress: consolidate called",
               eng_client.agents.consolidate.called)

        m.shutdown()
        e.on_session_end("sess-coop", [])


def main() -> int:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        hermes_home = Path(td)
        try:
            drive_memory_provider(hermes_home)
            drive_context_engine(hermes_home)
            drive_cooperation(hermes_home)
        except AssertionError as err:
            print(f"\n❌ {err}")
            return 1

    print("\n✅ Lifecycle harness passed — both plugins drive cleanly end-to-end.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
