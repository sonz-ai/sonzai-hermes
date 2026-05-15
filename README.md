# sonzai-hermes

[Hermes Agent](https://hermes-agent.nousresearch.com) plugins that route memory
and context-window compression through [Sonzai](https://sonz.ai).

Two plugins, separately installable:

| Plugin | Hermes role | What it does |
|---|---|---|
| **Memory Provider** (`plugins/memory/sonzai/`) | `MemoryProvider` ABC | per-turn recall + persist, fact extraction, session lifecycle |
| **Context Engine** (`plugins/context_engine/sonzai/`) | `ContextEngine` ABC | window compression via Sonzai consolidation (not naive summary) |

Both talk to Sonzai through the official `sonzai` Python SDK — never hand-rolled REST.

---

## Status

**v0 — scaffold + spec.** Directory layout, plugin manifests, class skeletons,
and the full design doc are in. Live integration to fill in next; see
[`PLAN.md`](./PLAN.md) for the task-by-task implementation plan.

The contract lives in [`SPEC.md`](./SPEC.md). Read it before changing any
public method on either plugin.

---

## Install (once implemented)

Requires Python 3.11+ and a Sonzai API key (`https://sonz.ai`).

```bash
pip install sonzai-hermes
export SONZAI_API_KEY=sk_...
hermes sonzai setup            # interactive wizard, one-time
```

Then in your Hermes profile (`~/.hermes/config.yaml`):

```yaml
memory:
  provider: sonzai
context:
  engine: sonzai
```

---

## Layout

```
sonzai-hermes/
├── SPEC.md                       contract — read this first
├── PLAN.md                       implementation plan (TDD, task-by-task)
├── README.md                     you are here
├── pyproject.toml                ships sonzai_common + both plugins
├── sonzai_common/                shared: client, config, agent-id, formatter
├── plugins/
│   ├── memory/sonzai/            MemoryProvider
│   └── context_engine/sonzai/    ContextEngine
└── tests/
```

---

## Why two plugins

Hermes splits responsibilities OpenClaw conflates:

- **Memory Provider** owns *what the model knows* — recall on prefetch, persist on
  `sync_turn`, session start/end. Runs every turn.
- **Context Engine** owns *the size of the window* — only fires when token usage
  crosses a threshold. Compresses by handing history to Sonzai's consolidation
  pipeline and rebuilding from canonical facts + `recent_turns` + the live tail.

You can install either one alone. They cooperate cleanly when both are installed
(same agent identity, no double-consolidation — see `SPEC.md`).

---

## Origin

Sister project to [`sonzai-openclaw`](https://github.com/sonz-ai/sonzai-openclaw)
(TypeScript, single `ContextEngine` plugin for OpenClaw). The contract is
similar; only the API shape and language differ.

Built for the [Eragon](https://eragon.ai) partnership — Eragon runs Hermes,
Sonzai powers the memory + context.

## License

MIT — see [`LICENSE`](./LICENSE).
