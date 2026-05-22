# Sonzai Memory Provider (Hermes)

Hermes `MemoryProvider` that routes recall, per-turn persist, and session
lifecycle through Sonzai.

## Install

```bash
pip install sonzai-hermes
export SONZAI_API_KEY=sk_...
hermes sonzai setup
```

Then in `~/.hermes/config.yaml`:

```yaml
memory:
  provider: sonzai
```

## What it does

| Hook | Sonzai call |
|---|---|
| `prefetch(query)` | `client.agents.get_context(query=...)` — formatted as a `<sonzai-context>` block, trimmed to `context_token_budget` |
| `sync_turn(user, assistant)` | `client.agents.process(messages=[...])` on a daemon thread — fires fact extraction, writes the 2h `recent_turns` buffer |
| `on_session_end(messages)` | `client.agents.sessions.end(...)` |
| `on_pre_compress(messages)` | safety-net flush via `process()` for any unextracted turns. Consolidation lives in the Context Engine plugin |
| `get_tool_schemas()` | exposes `sonzai_memory_search` + `sonzai_memory_write` to the model |

## Config

See [`SPEC.md`](../../../SPEC.md#shared-config) — same keys for both plugins.

Memory mode:
- `sync` — `prefetch` blocks for completeness (default)
- `async` — `prefetch` races a short (0.6 s) deadline; returns what's ready

Opt-in keys (written to `<hermes_home>/sonzai.json`):
- `also_consolidate: true` — also call `consolidate()` in `on_pre_compress`.
  Enable when pairing this provider with a **non-Sonzai** context engine; the
  Sonzai context engine plugin already owns consolidation, so leave this off
  when both plugins are installed.

## Contract

[`SPEC.md` §Plugin 1 — Memory Provider](../../../SPEC.md#plugin-1--memory-provider-pluginsmemorysonzai).
