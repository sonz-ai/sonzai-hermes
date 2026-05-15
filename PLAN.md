# sonzai-hermes Implementation Plan

**Goal:** Take the scaffold in this repo from `NotImplementedError` stubs to two
working Hermes plugins (Memory Provider + Context Engine) backed by the Sonzai
SDK.

**Architecture:** Two Python plugins sharing a `sonzai_common/` module for client
construction, config, identity, and the `EnrichedContextResponse → str`
formatter. Both plugins talk to Sonzai exclusively through `pip install sonzai`
(`from sonzai import Sonzai`). The Memory Provider runs every turn; the Context
Engine fires only on the token-budget threshold and is the sole owner of
`consolidate()`.

**Tech stack:** Python 3.11+, `sonzai>=1.5.6`, `pyyaml`, `pytest`, `ruff`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax. Each task ends with a commit.

> **Contract source of truth:** [`SPEC.md`](./SPEC.md). Every method on every
> plugin maps to a row in that table. If you change a method signature, update
> the spec first.

> **Behaviour invariants** (apply to every task):
> 1. Never block the agent. Every Sonzai call is wrapped; failures log and
>    degrade, never raise into Hermes.
> 2. `sync_turn` and `queue_prefetch` are non-blocking — daemon threads.
> 3. One agent identity across both plugins.
> 4. `recent_turns` (2h TTL) closes the latency gap — don't add client-side
>    caches that hide it.
> 5. Config precedence: env var > saved config file > default. Resolved once.

---

## File structure (already scaffolded)

```
sonzai_common/
├── __init__.py            re-exports
├── config.py              SonzaiConfig + load_config + save_config
├── client.py              build_client + close_client
├── identity.py            resolve_agent_id + resolve_user_id
└── format.py              format_enriched_context

plugins/memory/sonzai/
├── __init__.py            register(ctx)
├── plugin.yaml            hooks manifest
├── provider.py            SonzaiMemoryProvider
├── cli.py                 hermes sonzai setup / health
└── README.md

plugins/context_engine/sonzai/
├── __init__.py            exports SonzaiContextEngine
├── plugin.yaml
├── engine.py              SonzaiContextEngine
└── README.md

tests/
├── conftest.py            hermes_home + clean_env fixtures
├── test_common/           one file per sonzai_common module
├── test_memory/           one file per provider method group
└── test_context_engine/   one file per engine method group
```

---

## Phase 1 — Shared foundation

### Task 1: Project bootstrap + smoke test

**Files:**
- Modify: `pyproject.toml` (already scaffolded — verify installable)
- Create: `tests/test_smoke.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_smoke.py
import importlib


def test_packages_importable():
    importlib.import_module("sonzai_common")
    importlib.import_module("plugins.memory.sonzai")
    importlib.import_module("plugins.context_engine.sonzai")


def test_sonzai_sdk_importable():
    importlib.import_module("sonzai")
```

- [ ] **Step 2: Run test to verify it fails**

```
pip install -e ".[dev]"
pytest tests/test_smoke.py -v
```

Expected: PASS for `test_packages_importable` (stubs already importable),
FAIL for `test_sonzai_sdk_importable` only if `sonzai` is not yet on PyPI.

- [ ] **Step 3: Add CI config**

Create `.github/workflows/ci.yml` running `ruff check` + `pytest` on Python
3.11 and 3.12.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml tests/test_smoke.py .github/workflows/ci.yml
git commit -m "chore: bootstrap dev env + smoke tests + CI"
```

---

### Task 2: `SonzaiConfig` dataclass

**Files:**
- Modify: `sonzai_common/config.py:21-30`
- Create: `tests/test_common/test_config_dataclass.py`

- [ ] **Step 1: Test default field values**

```python
from sonzai_common import SonzaiConfig


def test_defaults():
    cfg = SonzaiConfig()
    assert cfg.api_key is None
    assert cfg.agent_id is None
    assert cfg.agent_name == "hermes-agent"
    assert cfg.base_url == "https://api.sonz.ai"
    assert cfg.default_user_id == "owner"
    assert cfg.memory_mode == "sync"
    assert cfg.context_token_budget == 2000
```

- [ ] **Step 2: Run — should already PASS**

The dataclass is already scaffolded. If the test fails, the scaffold is
broken and must be fixed before proceeding.

- [ ] **Step 3: Commit**

```bash
git add tests/test_common/test_config_dataclass.py
git commit -m "test: pin SonzaiConfig defaults"
```

---

### Task 3: `load_config` + `save_config`

**Files:**
- Modify: `sonzai_common/config.py:33-50`
- Create: `tests/test_common/test_config_io.py`

Precedence (per SPEC §Shared config): env var > saved file > default.

- [ ] **Step 1: Failing tests**

```python
import json
from sonzai_common import load_config, save_config


def test_defaults_when_nothing_set(hermes_home, clean_env):
    cfg = load_config(hermes_home)
    assert cfg.agent_name == "hermes-agent"
    assert cfg.base_url == "https://api.sonz.ai"


def test_env_overrides_file(hermes_home, clean_env, monkeypatch):
    save_config({"agent_name": "from-file", "base_url": "https://file.example"}, hermes_home)
    monkeypatch.setenv("SONZAI_AGENT_NAME", "from-env")
    cfg = load_config(hermes_home)
    assert cfg.agent_name == "from-env"
    assert cfg.base_url == "https://file.example"  # not overridden


def test_api_key_from_env_only(hermes_home, clean_env, monkeypatch):
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    cfg = load_config(hermes_home)
    assert cfg.api_key == "sk_test"


def test_save_config_rejects_secret(hermes_home):
    save_config({"api_key": "sk_should_not_be_written", "agent_name": "x"}, hermes_home)
    on_disk = json.loads((hermes_home / "sonzai.json").read_text())
    assert "api_key" not in on_disk
    assert on_disk["agent_name"] == "x"
```

- [ ] **Step 2: Implement `load_config` and `save_config`**

`load_config`: build a `SonzaiConfig()`, layer the saved JSON file over it,
then layer env vars on top. Map env keys: `SONZAI_API_KEY`, `SONZAI_AGENT_ID`,
`SONZAI_AGENT_NAME`, `SONZAI_BASE_URL`, `SONZAI_MEMORY_MODE`.

`save_config`: strip `api_key` from the input dict (secret — goes to `.env`),
write the rest as JSON to `<hermes_home>/sonzai.json`. Atomic write
(write+rename).

- [ ] **Step 3: Run — all pass**

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(common): load_config + save_config with env > file > default precedence"
```

---

### Task 4: `build_client` + `close_client`

**Files:**
- Modify: `sonzai_common/client.py`
- Create: `tests/test_common/test_client.py`

- [ ] **Step 1: Failing test**

```python
import pytest
from sonzai_common import SonzaiConfig, build_client, close_client


def test_build_client_requires_api_key():
    with pytest.raises(ValueError, match="api_key"):
        build_client(SonzaiConfig())


def test_build_client_returns_client():
    cfg = SonzaiConfig(api_key="sk_test", base_url="https://api.sonz.ai")
    client = build_client(cfg)
    assert client is not None
    close_client(client)  # must not raise


def test_close_client_handles_none():
    close_client(None)  # must not raise
```

- [ ] **Step 2: Implement**

```python
from sonzai import Sonzai

def build_client(config):
    if not config.api_key:
        raise ValueError("api_key is required; set SONZAI_API_KEY")
    return Sonzai(api_key=config.api_key, base_url=config.base_url)

def close_client(client):
    if client is None:
        return
    try:
        client.close()
    except Exception:  # never raise into Hermes
        pass
```

- [ ] **Step 3: Run + commit**

```bash
git commit -m "feat(common): Sonzai client construction with never-raise close"
```

---

### Task 5: `resolve_agent_id` + `resolve_user_id`

**Files:**
- Modify: `sonzai_common/identity.py`
- Create: `tests/test_common/test_identity.py`

- [ ] **Step 1: Failing tests**

```python
from unittest.mock import MagicMock
from sonzai_common import SonzaiConfig, resolve_agent_id, resolve_user_id


def test_resolve_agent_id_uses_configured():
    client = MagicMock()
    cfg = SonzaiConfig(api_key="x", agent_id="agent_already_set")
    assert resolve_agent_id(client, cfg) == "agent_already_set"
    client.agents.create.assert_not_called()


def test_resolve_agent_id_provisions_when_missing():
    client = MagicMock()
    client.agents.create.return_value.id = "agent_new"
    cfg = SonzaiConfig(api_key="x", agent_id=None, agent_name="hermes-agent")
    assert resolve_agent_id(client, cfg) == "agent_new"
    client.agents.create.assert_called_once_with(name="hermes-agent")


def test_resolve_user_id_cli_session():
    cfg = SonzaiConfig(api_key="x", default_user_id="owner")
    assert resolve_user_id(None, cfg) == "owner"
    assert resolve_user_id("cli-session-abc", cfg) == "owner"


def test_resolve_user_id_parses_handle_when_present():
    # session-id shape: "user:nas@sonz.ai/session-xyz"
    cfg = SonzaiConfig(api_key="x", default_user_id="owner")
    assert resolve_user_id("user:nas@sonz.ai/session-xyz", cfg) == "nas@sonz.ai"
```

- [ ] **Step 2: Implement** per SPEC §User-identity resolution. The
`user:HANDLE/...` parser mirrors `parseSessionKey` in
`sonzai-openclaw/src/cli.ts`.

- [ ] **Step 3: Run + commit**

```bash
git commit -m "feat(common): agent-id provisioning + session→user-id resolution"
```

---

### Task 6: `format_enriched_context`

**Files:**
- Modify: `sonzai_common/format.py`
- Create: `tests/test_common/test_format.py`

- [ ] **Step 1: Failing tests**

```python
from sonzai_common import format_enriched_context


def test_empty_response_returns_empty_string():
    assert format_enriched_context(None, token_budget=2000) == ""
    assert format_enriched_context({}, token_budget=2000) == ""


def test_renders_sonzai_context_block():
    response = {
        "agent": {"name": "hermes-agent", "personality": {"big5": {"openness": 0.8}}},
        "memory": {"facts": ["user lives in SG"]},
        "mood": "curious",
        "recent_turns": [{"role": "user", "content": "hi"}],
    }
    out = format_enriched_context(response, token_budget=2000)
    assert out.startswith("<sonzai-context>")
    assert out.endswith("</sonzai-context>")
    assert "user lives in SG" in out


def test_trims_to_token_budget():
    huge = {"memory": {"facts": ["x" * 100] * 1000}}
    out = format_enriched_context(huge, token_budget=200)
    # rough proxy: 1 token ≈ 4 chars
    assert len(out) <= 200 * 4 + len("<sonzai-context>") + len("</sonzai-context>")
```

- [ ] **Step 2: Implement** the 7-layer formatter. Sections in order: agent
profile, personality/Big5, evolution/goals/habits/breakthroughs, relationship
(chemistry/diary/narrative), mood, memory tree, supplementary search,
`recent_turns`. Trim from the *bottom* (drop supplementary first, then memory
tail) when over budget.

- [ ] **Step 3: Run + commit**

```bash
git commit -m "feat(common): EnrichedContextResponse → <sonzai-context> formatter"
```

---

## Phase 2 — Memory Provider

### Task 7: `is_available` (no network)

**Files:**
- Modify: `plugins/memory/sonzai/provider.py:is_available`
- Create: `tests/test_memory/test_availability.py`

- [ ] **Step 1: Failing tests**

```python
from plugins.memory.sonzai import SonzaiMemoryProvider


def test_is_available_when_env_set(clean_env, monkeypatch):
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    assert SonzaiMemoryProvider().is_available() is True


def test_is_available_when_unset(clean_env, hermes_home):
    assert SonzaiMemoryProvider().is_available() is False


def test_is_available_does_not_call_network(clean_env, monkeypatch):
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("network call"))
    SonzaiMemoryProvider().is_available()
```

- [ ] **Step 2: Implement** — pure value check on env + saved config.

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(memory): is_available — env + saved config check, no network"
```

---

### Task 8: `initialize` + `shutdown`

**Files:**
- Modify: `provider.py:initialize`, `provider.py:shutdown`
- Create: `tests/test_memory/test_lifecycle.py`

- [ ] **Step 1: Failing tests**

```python
from unittest.mock import MagicMock, patch
from plugins.memory.sonzai import SonzaiMemoryProvider


def test_initialize_calls_sessions_start(hermes_home, monkeypatch):
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch("plugins.memory.sonzai.provider.build_client") as bc:
        client = MagicMock()
        client.agents.create.return_value.id = "agent_x"
        bc.return_value = client
        p = SonzaiMemoryProvider()
        p.initialize(session_id="sess_1", hermes_home=str(hermes_home))
        client.agents.sessions.start.assert_called_once()


def test_initialize_does_not_raise_on_failure(hermes_home, monkeypatch):
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch("plugins.memory.sonzai.provider.build_client", side_effect=RuntimeError("net")):
        p = SonzaiMemoryProvider()
        p.initialize(session_id="sess_1", hermes_home=str(hermes_home))  # MUST NOT RAISE
        assert p._degraded is True


def test_shutdown_closes_client(hermes_home, monkeypatch):
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch("plugins.memory.sonzai.provider.build_client") as bc:
        bc.return_value = MagicMock()
        p = SonzaiMemoryProvider()
        p.initialize(session_id="sess_1", hermes_home=str(hermes_home))
        p.shutdown()
        bc.return_value.close.assert_called_once()
```

- [ ] **Step 2: Implement**, wrapping in try/except → `self._degraded = True`
on any failure. Join daemon threads on shutdown with a 2s timeout.

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(memory): initialize + shutdown with degraded-mode failure"
```

---

### Task 9: `get_config_schema` + `save_config` proxy

**Files:**
- Modify: `provider.py:get_config_schema`, `provider.py:save_config`
- Create: `tests/test_memory/test_config_schema.py`

- [ ] **Step 1: Test the schema shape**

```python
def test_schema_marks_api_key_secret():
    schema = SonzaiMemoryProvider().get_config_schema()
    api_key_field = next(f for f in schema if f["key"] == "api_key")
    assert api_key_field["secret"] is True
    assert api_key_field["env_var"] == "SONZAI_API_KEY"
    assert api_key_field["required"] is True
    assert api_key_field["url"] == "https://sonz.ai"
```

- [ ] **Step 2: Implement** — return the field descriptors for every key in
SPEC §Shared config. `save_config` delegates to `sonzai_common.save_config`.

- [ ] **Step 3: Commit**

---

### Task 10: `prefetch` (recall, sync mode)

**Files:**
- Modify: `provider.py:prefetch`
- Create: `tests/test_memory/test_prefetch.py`

- [ ] **Step 1: Failing tests** (sync mode first)

```python
def test_prefetch_returns_formatted_block(hermes_home, monkeypatch):
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch("plugins.memory.sonzai.provider.build_client") as bc:
        client = MagicMock()
        client.agents.get_context.return_value = {"memory": {"facts": ["fact A"]}}
        bc.return_value = client
        p = SonzaiMemoryProvider()
        p.initialize(session_id="s", hermes_home=str(hermes_home))
        out = p.prefetch("what do you remember?")
        assert "fact A" in out
        client.agents.get_context.assert_called_once()


def test_prefetch_returns_empty_on_error(hermes_home, monkeypatch):
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch("plugins.memory.sonzai.provider.build_client") as bc:
        client = MagicMock()
        client.agents.get_context.side_effect = RuntimeError("api down")
        bc.return_value = client
        p = SonzaiMemoryProvider()
        p.initialize(session_id="s", hermes_home=str(hermes_home))
        assert p.prefetch("anything") == ""  # never raises
```

- [ ] **Step 2: Implement** sync path: call `get_context` with the resolved
ids, pass response through `format_enriched_context`, return string. Wrap
everything in try/except → `""`.

- [ ] **Step 3: Commit**

---

### Task 11: `prefetch` async mode + `queue_prefetch`

**Files:**
- Modify: `provider.py:prefetch`, `provider.py:queue_prefetch`
- Create: `tests/test_memory/test_async_prefetch.py`

- [ ] **Step 1: Test async deadline**

```python
import time

def test_async_mode_returns_partial_under_deadline(hermes_home, monkeypatch):
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    monkeypatch.setenv("SONZAI_MEMORY_MODE", "async")
    with patch("plugins.memory.sonzai.provider.build_client") as bc:
        client = MagicMock()
        def slow(*a, **k):
            time.sleep(5)
            return {"memory": {"facts": ["too late"]}}
        client.agents.get_context.side_effect = slow
        bc.return_value = client
        p = SonzaiMemoryProvider()
        p.initialize(session_id="s", hermes_home=str(hermes_home))
        t0 = time.time()
        out = p.prefetch("x")
        assert time.time() - t0 < 1.0  # respected deadline
        assert out == ""


def test_queue_prefetch_warms_cache(hermes_home, monkeypatch):
    # First call: queue_prefetch fires in background.
    # Wait briefly, then prefetch returns cached result without re-calling.
    ...
```

- [ ] **Step 2: Implement** the async deadline (use
`concurrent.futures.Future.result(timeout=)`) and the warm-ahead cache keyed
on the query string.

- [ ] **Step 3: Commit**

---

### Task 12: `sync_turn` (non-blocking persist)

**Files:**
- Modify: `provider.py:sync_turn`
- Create: `tests/test_memory/test_sync_turn.py`

- [ ] **Step 1: Tests**

```python
import time

def test_sync_turn_is_non_blocking(hermes_home, monkeypatch):
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch("plugins.memory.sonzai.provider.build_client") as bc:
        client = MagicMock()
        client.agents.process.side_effect = lambda *a, **k: time.sleep(2)
        bc.return_value = client
        p = SonzaiMemoryProvider()
        p.initialize(session_id="s", hermes_home=str(hermes_home))
        t0 = time.time()
        p.sync_turn("hello", "hi back")
        assert time.time() - t0 < 0.1  # returned immediately


def test_sync_turn_swallows_errors(hermes_home, monkeypatch):
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch("plugins.memory.sonzai.provider.build_client") as bc:
        client = MagicMock()
        client.agents.process.side_effect = RuntimeError("nope")
        bc.return_value = client
        p = SonzaiMemoryProvider()
        p.initialize(session_id="s", hermes_home=str(hermes_home))
        p.sync_turn("u", "a")  # must NOT raise
        # thread will log internally; nothing for us to assert beyond no-throw
```

- [ ] **Step 2: Implement** with a daemon thread; append to
`self._threads` so `shutdown` can join.

- [ ] **Step 3: Commit**

---

### Task 13: `on_session_end`

**Files:**
- Modify: `provider.py:on_session_end`
- Create: `tests/test_memory/test_session_end.py`

- [ ] Test: calls `client.agents.sessions.end(agent_id, user_id, session_id)`.
Never raises. Commit.

---

### Task 14: `on_pre_compress`

**Files:**
- Modify: `provider.py:on_pre_compress`
- Create: `tests/test_memory/test_pre_compress.py`

- [ ] **Step 1: Test**

```python
def test_on_pre_compress_calls_process_only(hermes_home, monkeypatch):
    """When Context Engine plugin owns consolidation, provider only flushes process()."""
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch("plugins.memory.sonzai.provider.build_client") as bc:
        client = MagicMock()
        bc.return_value = client
        p = SonzaiMemoryProvider()
        p.initialize(session_id="s", hermes_home=str(hermes_home))
        p.on_pre_compress([{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}])
        client.agents.process.assert_called_once()
        client.agents.consolidate.assert_not_called()
```

- [ ] **Step 2: Implement.** Document in the docstring that pairing with a
non-Sonzai context engine should set a flag to also call `consolidate()` —
keep this behind a config key (`also_consolidate: bool = False`) added in
Task 9, defaulting `False`.

- [ ] **Step 3: Commit**

---

### Task 15: `system_prompt_block`

**Files:**
- Modify: `provider.py:system_prompt_block`
- Create: `tests/test_memory/test_system_prompt.py`

- [ ] Test: returns a non-empty string mentioning Sonzai and `<sonzai-context>`.
Implement. Commit.

---

### Task 16: `get_tool_schemas` + `handle_tool_call`

**Files:**
- Modify: `provider.py:get_tool_schemas`, `provider.py:handle_tool_call`
- Create: `tests/test_memory/test_tools.py`

- [ ] **Step 1: Tests**

```python
import json

def test_schemas_advertise_two_tools():
    names = {t["name"] for t in SonzaiMemoryProvider().get_tool_schemas()}
    assert names == {"sonzai_memory_search", "sonzai_memory_write"}


def test_handle_search_tool(hermes_home, monkeypatch):
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch("plugins.memory.sonzai.provider.build_client") as bc:
        client = MagicMock()
        client.agents.memory.search.return_value = [{"fact": "x"}]
        bc.return_value = client
        p = SonzaiMemoryProvider()
        p.initialize(session_id="s", hermes_home=str(hermes_home))
        result = p.handle_tool_call("sonzai_memory_search", {"query": "anything"})
        assert json.loads(result) == [{"fact": "x"}]


def test_handle_write_tool(hermes_home, monkeypatch):
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    with patch("plugins.memory.sonzai.provider.build_client") as bc:
        client = MagicMock()
        client.agents.memory.create_fact.return_value = {"ok": True}
        bc.return_value = client
        p = SonzaiMemoryProvider()
        p.initialize(session_id="s", hermes_home=str(hermes_home))
        result = p.handle_tool_call("sonzai_memory_write", {"content": "new fact"})
        assert json.loads(result) == {"ok": True}
```

- [ ] **Step 2: Implement.** Unknown tool → return `{"error": "..."}` JSON.

- [ ] **Step 3: Commit**

---

### Task 17: `hermes sonzai setup` + `health` CLI

**Files:**
- Modify: `plugins/memory/sonzai/cli.py`
- Create: `tests/test_memory/test_cli.py`

- [ ] **Step 1: Tests** with mocked `input()` and a fake `client.health.get()`.

- [ ] **Step 2: Implement.** Setup wizard:
  1. Read `SONZAI_API_KEY` from env, else prompt (masked input).
  2. Ask for `agent_id` (blank = provision a new one).
  3. Ask `sync` / `async` memory mode (default `sync`).
  4. Call `save_config(values, hermes_home)`; print where the secret needs to go (`.env`).
  
  Health: build client, `GET {base_url}/health`, print status code.

- [ ] **Step 3: Commit**

---

## Phase 3 — Context Engine

### Task 18: `update_from_response`

**Files:**
- Modify: `engine.py:update_from_response`
- Create: `tests/test_context_engine/test_token_tracking.py`

- [ ] Test: passing `{"prompt_tokens": 100, "completion_tokens": 50,
"total_tokens": 150}` sets `last_*` accordingly. Implement. Commit.

---

### Task 19: `update_model` + `should_compress`

**Files:**
- Modify: `engine.py:update_model`, `engine.py:should_compress`
- Create: `tests/test_context_engine/test_threshold.py`

- [ ] **Step 1: Tests**

```python
from plugins.context_engine.sonzai import SonzaiContextEngine


def test_threshold_recomputed_on_update_model():
    e = SonzaiContextEngine()
    e.update_model("gpt-4o", context_length=128_000)
    assert e.threshold_tokens == int(128_000 * 0.75)


def test_should_compress_below_threshold():
    e = SonzaiContextEngine()
    e.update_model("gpt-4o", context_length=128_000)
    assert e.should_compress(prompt_tokens=80_000) is False


def test_should_compress_at_threshold():
    e = SonzaiContextEngine()
    e.update_model("gpt-4o", context_length=128_000)
    assert e.should_compress(prompt_tokens=96_000) is True


def test_uses_last_prompt_tokens_when_arg_omitted():
    e = SonzaiContextEngine()
    e.update_model("gpt-4o", context_length=128_000)
    e.last_prompt_tokens = 100_000
    assert e.should_compress() is True
```

- [ ] **Step 2: Implement.** Commit.

---

### Task 20: Session lifecycle

**Files:**
- Modify: `engine.py:on_session_start`, `on_session_end`, `on_session_reset`
- Create: `tests/test_context_engine/test_lifecycle.py`

- [ ] Tests + impl mirroring Memory Provider lifecycle. Reuses
`sonzai_common`. Commit.

---

### Task 21: `compress` — the load-bearing method

**Files:**
- Modify: `engine.py:compress`
- Create: `tests/test_context_engine/test_compress.py`

- [ ] **Step 1: Test the 3-call chain order**

```python
from unittest.mock import MagicMock, call


def test_compress_calls_process_then_consolidate_then_get_context():
    e = SonzaiContextEngine()
    e._client = MagicMock()
    e._agent_id, e._user_id, e._session_id = "agent", "user", "sess"
    e._config = SonzaiConfig(api_key="x", context_token_budget=500)
    e.context_length = 8000

    e._client.agents.get_context.return_value = {"memory": {"facts": ["F"]}}

    messages = [{"role": "user", "content": "msg %d" % i} for i in range(50)]
    out = e.compress(messages, focus_topic="travel")

    calls = e._client.agents.mock_calls
    names = [c[0] for c in calls if c[0] in {"process", "consolidate", "get_context"}]
    assert names == ["process", "consolidate", "get_context"]


def test_compress_returns_system_plus_recency_tail():
    # First message must be role=system; last N must be recency tail verbatim.
    ...


def test_compress_increments_count():
    e = SonzaiContextEngine()
    e._client = MagicMock()
    e._client.agents.get_context.return_value = {}
    e._agent_id, e._user_id = "a", "u"
    e._config = SonzaiConfig(api_key="x")
    e.context_length = 8000

    e.compress([])
    e.compress([])
    assert e.compression_count == 2
```

- [ ] **Step 2: Implement** the 3-call chain + rebuild. Use a configurable
`recency_tail_n` (default 6 messages). Cap the system block to
`context_token_budget` via `format_enriched_context`.

- [ ] **Step 3: Add a separate test for the `sessions.end(wait=True)`
alternative path** behind an opt-in config key
`compress_via_session_boundary: bool = False`. Document in the spec under
"alternative one-call path".

- [ ] **Step 4: Commit**

---

### Task 22: `get_status`

**Files:**
- Modify: `engine.py:get_status`
- Create: `tests/test_context_engine/test_status.py`

- [ ] Test returns `{"engine": "sonzai", "compressions": ..., "last_prompt_tokens": ...}`.
Implement. Commit.

---

## Phase 4 — Wiring, integration, release

### Task 23: End-to-end integration test (single plugin)

**Files:**
- Create: `tests/test_integration/test_e2e_memory.py`

- [ ] Behind `pytest.mark.integration` so it's opt-in. Hits a live Sonzai
test tenant via `SONZAI_API_KEY` from env. Walks:
   `initialize → prefetch (cold) → sync_turn → prefetch (warm) → on_session_end`.
   Asserts that the fact written in `sync_turn` is recallable in the second
   `prefetch` (uses `recent_turns`, 2h TTL).

### Task 24: End-to-end integration test (both plugins together)

**Files:**
- Create: `tests/test_integration/test_e2e_both.py`

- [ ] Both plugins active. Drive 30 turns to exceed
`threshold_tokens` (configure a small `context_length`). Assert:
  - `on_pre_compress` fired exactly once before `compress`
  - `consolidate` called by Context Engine, *not* by Memory Provider
  - First message of compressed list is `role=system` and contains a
    `<sonzai-context>` block
  - Recency tail preserved verbatim

### Task 25: Packaging + release dry-run

**Files:**
- Modify: `pyproject.toml`
- Create: `.github/workflows/release.yml`

- [ ] `python -m build` produces a single wheel containing `sonzai_common`,
`plugins/memory/sonzai`, `plugins/context_engine/sonzai`. Verify
`pip install dist/*.whl` in a fresh venv makes all three importable.
Tag-driven release workflow publishes to PyPI on `v*.*.*` tags.

### Task 26: Confirm against live Hermes build

**Files:**
- Modify: `SPEC.md` (resolve open questions)
- Modify: `plugins/*/sonzai/__init__.py` (correct ABC import path)

- [ ] Open against the live Hermes Python build:
  1. Exact import path of `ContextEngine` ABC (docs say package-level export
     — confirm module).
  2. Whether Hermes passes a per-turn `user_id` / multi-user session shape,
     or only 1:1 CLI — determines how much `parseSessionKey` logic is
     actually exercised.
  Update `SPEC.md` open questions section with answers.

---

## Self-review checklist

- [ ] **Spec coverage:** every method in `SPEC.md` is covered by a task above.
- [ ] **No placeholders:** every step has the actual code or command shown.
- [ ] **Type consistency:** method signatures here match `SPEC.md` exactly
      (no drift between `prefetch(query)` vs `prefetch(self, query, ...)`,
      etc.).
- [ ] **Behaviour invariants** repeated near the top apply to every task —
      if a task adds a code path that can raise into Hermes, the task is wrong.
