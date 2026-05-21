# Hermes Agent — Sonzai Plugin Spec

Goal: make [Hermes agent](https://hermes-agent.nousresearch.com) (Nous Research)
use Sonzai as its **memory** and its **context engine**, the same way
`@sonzai-labs/openclaw-context` does for OpenClaw.

Hermes splits into two plugin types that OpenClaw conflates into one
`ContextEngine`. We ship **both**, independently installable:

| Hermes plugin | Sonzai role | Analogous OpenClaw hook |
|---|---|---|
| **Memory Provider** (`plugins/memory/sonzai/`) | recall prior knowledge + persist turns + extract facts | `assemble` + `afterTurn` + `bootstrap`/`dispose` |
| **Context Engine** (`plugins/context_engine/sonzai/`) | compress an over-budget window via Sonzai consolidation instead of naive summarisation | `compact` |

Both are **Python** (Hermes plugins are Python; the existing OpenClaw plugin is
TypeScript — none of that code is reusable, only the API contract is). Both
talk to Sonzai through the official **`sonzai` Python SDK** (`pip install sonzai`,
`from sonzai import Sonzai`) — do **not** hand-roll REST.

---

## Shared foundation

### Sonzai SDK surface used by both plugins

```python
from sonzai import Sonzai
client = Sonzai(api_key=...)                      # or env SONZAI_API_KEY

client.agents.create(name=..., ...)               # idempotent provision — SHA1(tenant+name)
client.agents.get(agent_id)                       # metadata
client.agents.get_context(agent_id, user_id=, session_id=, query=)
                                                  # → EnrichedContextResponse:
                                                  #   memory, mood, personality, relationships,
                                                  #   goals, interests, habits, recent_turns
client.agents.process(agent_id, user_id=, messages=, session_id=)
                                                  # sync RPC. Runs the full CE pipeline on the
                                                  # given turn(s) — fact extraction, behavioural
                                                  # side-effects, recent_turns buffer write.
client.agents.consolidate(agent_id, period="daily", user_id=)
                                                  # sync RPC. On-demand trigger of the
                                                  # consolidation pipeline — NOT only a daily job.
                                                  # Returns when consolidation completes.
client.agents.sessions.start(agent_id, user_id=, session_id=)
client.agents.sessions.end(agent_id, user_id=, session_id=, wait=True|False)
                                                  # wait=True runs the full CE pipeline
                                                  # synchronously inline before responding
                                                  # (bounded by inlineWaitSoftCap on the
                                                  # server). Heaviest sync path — equivalent
                                                  # to `process` + `consolidate` in one call,
                                                  # with session-end semantics.
client.agents.memory.search(...) / create_fact(...)   # explicit tool ops
```

**All three of `process`, `consolidate`, and `sessions.end(wait=True)` are
synchronous RPCs.** The Context Engine plugin uses them inside `compress()`;
the Memory Provider plugin uses `process` inside a daemon thread for fire-
and-forget turn persistence.

### Shared config (both plugins read the same keys)

| Key | Secret | Env override | Default | Notes |
|---|---|---|---|---|
| `api_key` | yes | `SONZAI_API_KEY` | *required* | |
| `agent_id` | no | `SONZAI_AGENT_ID` | auto-provision | |
| `agent_name` | no | `SONZAI_AGENT_NAME` | `hermes-agent` | stable name → deterministic agent UUID |
| `base_url` | no | `SONZAI_BASE_URL` | `https://api.sonz.ai` | |
| `default_user_id` | no | — | `owner` | 1:1 CLI sessions |
| `memory_mode` | no | `SONZAI_MEMORY_MODE` | `sync` | `sync` blocks recall for completeness; `async` races a deadline |
| `context_token_budget` | no | — | `2000` | cap on injected context |

Persist non-secret keys via the provider's `save_config(values, hermes_home)`;
secrets go to `.env` per Hermes' `get_config_schema()` `secret: True` contract.
**All storage paths derive from the `hermes_home` kwarg** passed to
`initialize()` — never hardcode.

### User-identity resolution

Hermes `session_id` → Sonzai `user_id`, mirroring the OpenClaw plugin's
`parseSessionKey`:
- 1:1 CLI session → `default_user_id` (`"owner"`)
- multi-user transports (if Hermes exposes them) → parse the stable user handle
out of the session id; fall back to `"owner"`.

A shared `sonzai_common/` module (sibling of both plugin dirs, or vendored into
each) should hold: client construction, config resolution + env overrides,
agent-id resolution/provisioning, user-id parsing, and the
`EnrichedContextResponse → str` formatter. Keep it one source of truth.

---

## Plugin 1 — Memory Provider (`plugins/memory/sonzai/`)

Implements the `MemoryProvider` ABC (`agent/memory_provider.py`).

### File layout
```
plugins/memory/sonzai/
├── __init__.py        # MemoryProvider subclass + register(ctx)
├── plugin.yaml
├── cli.py             # `hermes sonzai setup` wizard
├── provider.py        # the implementation
└── README.md
```

### `plugin.yaml`
```yaml
name: sonzai
version: 1.0.0
description: "Sonzai Mind Layer — persistent memory, personality, mood, relationships"
hooks:
  - prefetch
  - queue_prefetch
  - sync_turn
  - on_session_end
  - on_pre_compress
  - system_prompt_block
```

### Method contract

| Method | Implementation |
|---|---|
| `name` (property) | `"sonzai"` |
| `is_available()` | `True` if `SONZAI_API_KEY` (or saved config key) is present. **No network call** — just check the value exists. |
| `initialize(session_id, **kwargs)` | Read `hermes_home` from kwargs. Build `Sonzai` client. Resolve `agent_id` (use configured, else `client.agents.create(name=agent_name)` — idempotent). `client.agents.sessions.start(agent_id, user_id, session_id)`. Cache `(agent_id, user_id, session_id)` on the instance. Wrap in try/except — a failure must not crash the agent; log + mark degraded. |
| `get_config_schema()` | Return field descriptors for the shared config table above. `api_key` → `secret: True, env_var: SONZAI_API_KEY, required: True, url: https://sonz.ai`. Others non-secret with defaults. |
| `save_config(values, hermes_home)` | Write non-secret keys to `<hermes_home>/sonzai.json` (or Hermes' profile config). Secrets are handled by Hermes' `.env` flow. |
| `prefetch(query)` | The recall path (≙ OpenClaw `assemble`). `client.agents.get_context(agent_id, user_id=, session_id=, query=query)`. Format the `EnrichedContextResponse` into a `<sonzai-context>` text block (personality/Big5, mood, relationship, semantically-relevant memories, goals, interests, habits, `recent_turns`), trimmed to `context_token_budget`. Return the string. On any error return `""` — never raise. Honour `memory_mode`: `sync` = await fully; `async` = race a short deadline, return what's ready. |
| `queue_prefetch(query)` | Optional warm-ahead: kick `get_context` on a daemon thread, stash the result so the next `prefetch` returns instantly. |
| `sync_turn(user_content, assistant_content)` | The persist path (≙ OpenClaw `afterTurn`). **Must be non-blocking** — spawn a daemon thread that calls `client.agents.process(agent_id, user_id=, messages=[{role:"user",...},{role:"assistant",...}], session_id=)`. This drives fact extraction + writes `recent_turns` (2h TTL) so a fact stated this turn is recallable next turn. |
| `on_session_end(messages)` | `client.agents.sessions.end(agent_id, user_id, session_id)`. Best-effort. |
| `on_pre_compress(messages)` | Belt-and-suspenders flush: `client.agents.process(agent_id, user_id=, messages=messages_slice)` for any unextracted turns in the about-to-discard window. `sync_turn` already runs per-turn, so this is a safety net. **Consolidation lives in the Context Engine's `compress()` (below), not here** — keeps responsibilities clean when both plugins are installed. If a *non-Sonzai* context engine is paired with this provider, also call `consolidate()` here. |
| `system_prompt_block()` | Static line telling the model it has a Sonzai-backed long-term memory and that recalled context arrives inline. |
| `get_tool_schemas()` | Expose two tools: `sonzai_memory_search` (`{query: string}`) and `sonzai_memory_write` (`{content: string}`) so the model can explicitly query/append. |
| `handle_tool_call(name, args)` | `sonzai_memory_search` → `client.agents.memory.search(...)` → JSON. `sonzai_memory_write` → `client.agents.memory.create_fact(...)` → JSON ack. |
| `shutdown()` | `client.close()`; join daemon threads with a short timeout. |

### `register()` (in `__init__.py`)
```python
def register(ctx) -> None:
    ctx.register_memory_provider(SonzaiMemoryProvider())
```

### `cli.py` — `hermes sonzai setup`
`register_cli(subparser)` builds a `sonzai` command group. `setup` runs the
wizard: prompt for API key (or detect `SONZAI_API_KEY`), ask for an existing
`agent_id` or provision one, ask `sync`/`async` memory mode, write config via
`save_config` + the secret to `.env`. Mirror `src/setup.ts` /
`src/cli.ts` from this repo for the UX. Add a `health` subcommand:
`GET {base_url}/health`.

---

## Plugin 2 — Context Engine (`plugins/context_engine/sonzai/`)

Implements the `ContextEngine` ABC. This is **token-budget compression**, not
recall — when the window approaches the model limit, hand the history to
Sonzai's consolidation pipeline and rebuild a compact window from canonical
facts + `recent_turns` + the live tail, instead of a naive LLM summary.

### File layout
```
plugins/context_engine/sonzai/
├── __init__.py        # exports the ContextEngine subclass (auto-discovered)
├── plugin.yaml
└── engine.py
```

### `plugin.yaml`
```yaml
name: sonzai
description: Sonzai consolidation-backed context compression
version: 1.0.0
```

### Required attributes (set in `__init__`)
```python
last_prompt_tokens = 0
last_completion_tokens = 0
last_total_tokens = 0
threshold_tokens = 0        # set from context_length * trigger ratio (e.g. 0.75)
context_length = 0          # model window; updated via update_model()
compression_count = 0
```

### Method contract

| Method | Implementation |
|---|---|
| `name` (property) | `"sonzai"` |
| `update_from_response(usage)` | Store `usage["prompt_tokens"]`, `["completion_tokens"]`, `["total_tokens"]` into `last_*`. |
| `should_compress(prompt_tokens=None)` | `True` when `(prompt_tokens or last_prompt_tokens) >= threshold_tokens`. |
| `compress(messages, current_tokens=None, focus_topic=None)` | Three sync RPCs, then rebuild. **(1)** `client.agents.process(agent_id, user_id=, messages=messages_slice, session_id=)` — extract any in-flight facts from the about-to-be-discarded window. **(2)** `client.agents.consolidate(agent_id, user_id=)` — fold them into canonical facts. **(3)** `client.agents.get_context(agent_id, user_id=, query=focus_topic or last_user_msg)` — pull the freshly-consolidated state. **(4)** Rebuild the message list: one `system` message holding the formatted enriched-context block (capped to `context_token_budget`), then keep the last N raw turns verbatim (recency tail) so nothing in-flight is lost. **(5)** `compression_count += 1`. Return a valid OpenAI-format `list[{"role","content"}]`, under `context_length`. **Alternative one-call path:** `client.agents.sessions.end(agent_id, ..., wait=True)` then `sessions.start(new_id)` + `get_context` — heaviest, but works if the deployment prefers session-boundary semantics over mid-session compression. Use `force_sync=True` query param if the server's `ENABLE_ASYNC_SESSION_END` is on and you want to bypass it (bench/test harnesses). |
| `on_session_start(session_id, **kwargs)` | Build client, resolve agent, `sessions.start`. Reuse `sonzai_common`. |
| `on_session_end(session_id, messages)` | `sessions.end`; `client.close()`. |
| `on_session_reset()` | Drop per-session cache; keep the client. |
| `update_model(model, context_length, ...)` | Set `context_length`; recompute `threshold_tokens`. |
| `get_status()` | `{"engine": "sonzai", "compressions": compression_count, "last_prompt_tokens": ...}`. |

### Registration
Auto-discovered: `__init__.py` exports the `ContextEngine` subclass at package
level. Users activate with `context: engine: "sonzai"` in Hermes config.

---

## Behaviour rules (both plugins)

1. **Never block the agent.** Every Sonzai call is wrapped; failures log and
   degrade (empty context / skipped persist), never raise into Hermes.
2. **`sync_turn` and `queue_prefetch` are non-blocking** — daemon threads for
   all I/O, per the Hermes contract.
3. **One agent identity.** Both plugins resolve the *same* `agent_id` from the
   shared config so memory written by the provider is visible to the context
   engine's `get_context`, and vice-versa.
4. **`recent_turns` closes the latency gap** — `process()` writes them (2h TTL),
   `get_context()` surfaces them, so a fact stated this turn is recallable next
   turn before consolidation runs. Don't add a client-side cache that hides this.
5. **Config precedence:** env var > saved config file > default. Resolve once at
   `initialize()` / `on_session_start()`.

## Build / packaging

- Target Python 3.11+ (matches the `sonzai` SDK).
- Depend on `sonzai>=1.5.6`.
- Each plugin dir is self-contained and separately installable; `sonzai_common`
  is vendored into both (or published as a tiny shared package) so neither
  plugin hard-depends on the other.
- Ship `README.md` per plugin with the one-shot install + manual config, mirror
  this repo's README structure.

## Confirmed against the live Hermes build

- `MemoryProvider` lives at `agent.memory_provider` — required methods:
  `name`, `is_available`, `initialize`, `get_tool_schemas`, `handle_tool_call`,
  `get_config_schema`, `save_config`. Optional hooks: `system_prompt_block`,
  `prefetch`, `queue_prefetch`, `sync_turn`, `on_session_end`,
  `on_pre_compress`, `on_memory_write`, `shutdown`.
- `ContextEngine` lives at `agent.context_engine` — required methods:
  `name`, `update_from_response(usage)`, `should_compress(prompt_tokens=None)`,
  `compress(messages, current_tokens=None, focus_topic=None)`. Required
  attributes: `last_prompt_tokens`, `last_completion_tokens`, `last_total_tokens`,
  `threshold_tokens`, `context_length`, `compression_count`. Optional:
  `on_session_start`, `on_session_end`, `on_session_reset`, `update_model`,
  `get_tool_schemas`, `handle_tool_call`, `get_status`.
- Plugin entry point for both: `def register(ctx) -> None` calling
  `ctx.register_memory_provider(...)` / `ctx.register_context_engine(...)`.
- Hermes currently surfaces `session_id` as the multi-user discriminant.
  The `user:<handle>/...` prefix in `resolve_user_id` is the agreed wire
  format for downstream transports; 1:1 CLI sessions still fall back to
  `default_user_id`.
