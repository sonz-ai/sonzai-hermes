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

**Working.** Both plugins implement the live Hermes ABCs
(`agent.memory_provider.MemoryProvider`, `agent.context_engine.ContextEngine`),
discover end-to-end through Hermes' own loader, and pass live integration
tests against the Sonzai API.

Verification layers (see [`scripts/README.md`](./scripts/README.md)):

| Script | What it proves | Network? |
|---|---|---|
| `verify_abc_parity.py`     | Every abstract method + required attr matches the live ABC | no |
| `verify_lifecycle.py`      | Both plugins drive cleanly through the full Hermes lifecycle (mocked SDK) | no |
| `verify_hermes_discovery.py` | Hermes' own loader finds + instantiates both plugins from their install paths | no |
| `pytest tests/`            | 82 unit tests | no |
| `pytest -m integration`    | 2 live tests against the real Sonzai API | yes |

The contract lives in [`SPEC.md`](./SPEC.md). Read it before changing any
public method on either plugin.

---

## Install

Requires Python 3.11+. **No Sonzai account required to start** — `setup` will
provision a 14-day trial on first run via the public `/onboarding/trial`
endpoint (mirrors `sonzai-openclaw`'s zero-touch onboarding).

```bash
pip install sonzai-hermes
sonzai-hermes install            # stages both plugins into Hermes' discovery paths
sonzai-hermes setup              # if no SONZAI_API_KEY: provisions a 14-day trial,
                                 # opens a browser to the claim URL, writes the
                                 # key to $HERMES_HOME/.env. Done in ~5 seconds.
```

Already have an API key? Set `SONZAI_API_KEY` before `setup` and it skips the trial flow.

Convert a trial to a permanent account any time before expiry:

```bash
sonzai-hermes claim              # prints + opens a fresh claim URL
```

### BYOK — bring your own LLM provider keys

If `OPENAI_API_KEY`, `GEMINI_API_KEY` (or `GOOGLE_API_KEY`), `XAI_API_KEY`,
or `OPENROUTER_API_KEY` are set in your environment, both plugins
automatically register them with the Sonzai platform on first startup.

Sonzai then routes LLM calls through **your** provider account, charging
only the 25% service fee instead of the ~125% platform-key markup. The
PUT is idempotent; subsequent startups are no-ops if nothing changed.

Override per provider with `SONZAI_BYOK_<PROVIDER>_KEY` (takes precedence
over the standard env var name). Set `SONZAI_PROJECT_ID` if your tenant
has multiple projects and none is named `Default`.

Then in your Hermes profile (`~/.hermes/config.yaml`):

```yaml
memory:
  provider: sonzai
context:
  engine: sonzai
```

### What `sonzai-hermes install` does

Two plugins, two discovery paths — Hermes uses different rules for each:

| Plugin | Destination | Why |
|---|---|---|
| Memory provider | `$HERMES_HOME/plugins/sonzai/` | Hermes' supported user-install path for memory providers. |
| Context engine | `<hermes-install>/plugins/context_engine/sonzai/` | Hermes' loader scans **only its bundled tree** for engines — there is no user-install path today. |

The CLI is idempotent: re-run safely after `pip install --upgrade hermes-agent` (the upgrade overwrites the bundled tree, so the context engine drop needs re-staging).

Flags:
- `--memory-only` / `--engine-only` — install one without the other
- `--symlink` — symlink instead of copy (best for dev/editable installs)
- `--hermes-home`, `--hermes-src` — override auto-detected locations
- `sonzai-hermes status` shows what's currently staged
- `sonzai-hermes uninstall` reverses both

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
