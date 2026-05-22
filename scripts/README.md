# Verification scripts

Four layers of audit, cheapest first. Run from the repo root.

## 1. ABC parity (no network, no Sonzai key)

```bash
git clone --depth 1 https://github.com/NousResearch/hermes-agent.git ../hermes-upstream
python3 scripts/verify_abc_parity.py
```

Imports `agent.memory_provider.MemoryProvider` and
`agent.context_engine.ContextEngine` from upstream Hermes, walks every
abstract method + required attribute, and compares signatures against our
`SonzaiMemoryProvider` and `SonzaiContextEngine`. Exits non-zero on any
drift. ~1 second.

Override the Hermes location with `HERMES_SRC=/path/to/hermes-agent`.

## 2. Fake-Hermes lifecycle harness (no network)

```bash
python3 scripts/verify_lifecycle.py
```

Drives both plugins through the documented Hermes lifecycle with the
Sonzai SDK fully mocked. Asserts:

- `is_available → initialize → prefetch → queue_prefetch (warm) → sync_turn →
  handle_tool_call → on_pre_compress → on_session_end → shutdown` ordering
  and payload shapes for the **memory provider**.
- `on_session_start → update_model → update_from_response → should_compress
  → compress (3-call chain) → get_status → on_session_reset → on_session_end`
  ordering and rebuild shape for the **context engine**.
- Both plugins together: same agent identity, memory's `on_pre_compress`
  does NOT consolidate, engine's `compress` DOES consolidate.

Exits non-zero on any failure. ~2 seconds.

## 3. Hermes discovery end-to-end (no network, no Sonzai key)

```bash
python3 scripts/verify_hermes_discovery.py
```

Stages our memory plugin into a temp `$HERMES_HOME/plugins/sonzai/`,
stages the context engine into Hermes' bundled
`plugins/context_engine/sonzai/`, then calls Hermes' OWN
`discover_memory_providers` / `discover_context_engines` /
`load_memory_provider` / `load_context_engine` to confirm they:

- find our plugin by name,
- successfully run `register(ctx)`,
- return instances that pass `isinstance(plugin, MemoryProvider)` and
  `isinstance(engine, ContextEngine)`.

Cleans up the bundled-tree mutation when done. This is the strongest
offline proof that the install path actually works.

## 4. Live integration tests (needs SONZAI_API_KEY)

```bash
set -a && source /path/to/.env && set +a
python3 -m pytest tests/test_integration/ -m integration -v --timeout=120
```

Hits the live Sonzai API.

- `test_e2e_memory.py` — initialize → prefetch (cold) → sync_turn →
  prefetch (warm) → assert the fact landed in `recent_turns` within 2h TTL.
- `test_e2e_both.py` — both plugins, shared `agent_id`, compress fires the
  3-call chain against a real tenant.

Skipped by default (`addopts = -m 'not integration'` in `pyproject.toml`).
Requires `pytest-timeout` (`pip install pytest-timeout`).

## Recommended pre-merge gauntlet

```bash
python3 scripts/verify_abc_parity.py && \
python3 scripts/verify_lifecycle.py && \
python3 scripts/verify_hermes_discovery.py && \
python3 -m pytest tests/ && \
python3 -m ruff check sonzai_common plugins tests scripts
```

All five must pass.
