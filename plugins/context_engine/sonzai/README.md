# Sonzai Context Engine (Hermes)

Hermes `ContextEngine` that compresses an over-budget window through Sonzai's
consolidation pipeline instead of naive LLM summarisation.

## Install

```bash
pip install sonzai-hermes
export SONZAI_API_KEY=sk_...
sonzai-hermes install --engine-only     # drops into Hermes' bundled tree
```

In `~/.hermes/config.yaml`:

```yaml
context:
  engine: sonzai
```

**Note on installation:** Hermes' loader scans **only its bundled
`plugins/context_engine/<name>/` directory** for context engines — there is
no `$HERMES_HOME` user-install path for engines today (it exists for memory
providers but not engines). `sonzai-hermes install` handles this for you
by dropping the plugin into the located Hermes install. Re-run after
`pip install --upgrade hermes-agent` since the upgrade overwrites the
bundled tree.

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

All three RPCs are **synchronous**. The alternative path
(`sessions.end(wait=True) → sessions.start(<rotated>) → get_context`) is
opt-in via `compress_via_session_boundary: true` in
`<hermes_home>/sonzai.json` — heavier, but uses the server's session-boundary
semantics for tenants that prefer it.

## Pairs with

The Memory Provider plugin — both resolve the same `agent_id` so memory
written by `sync_turn` is visible to the engine's `get_context`, and vice
versa. They can also run independently.

## Contract

[`SPEC.md` §Plugin 2 — Context Engine](../../../SPEC.md#plugin-2--context-engine-pluginscontext_enginesonzai).
