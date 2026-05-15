# Sonzai Context Engine (Hermes)

Hermes `ContextEngine` that compresses an over-budget window through Sonzai's
consolidation pipeline instead of naive LLM summarisation.

## Install

```bash
pip install sonzai-hermes
export SONZAI_API_KEY=sk_...
```

In `~/.hermes/config.yaml`:

```yaml
context:
  engine: sonzai
```

## How compression works

Triggered when `prompt_tokens ≥ threshold_tokens` (default `0.75 × context_length`):

1. `client.agents.process(messages=slice)` — extract any in-flight facts from
   the window about to be discarded.
2. `client.agents.consolidate()` — fold them into canonical facts.
3. `client.agents.get_context(query=focus_topic)` — pull the
   freshly-consolidated state.
4. Rebuild the message list: one `system` message holding the formatted
   enriched-context block (capped to `context_token_budget`), plus the last N
   raw turns verbatim (recency tail).

All three RPCs are **synchronous**. The alternative one-call path
(`sessions.end(wait=True) → sessions.start(new) → get_context`) is supported
for deployments preferring session-boundary semantics.

## Pairs with

The Memory Provider plugin — both resolve the same `agent_id` so memory
written by `sync_turn` is visible to the engine's `get_context`, and vice
versa. They can also run independently.

## Contract

[`SPEC.md` §Plugin 2 — Context Engine](../../../SPEC.md#plugin-2--context-engine-pluginscontext_enginesonzai).
