"""``SonzaiMemoryProvider`` — implements Hermes' ``MemoryProvider`` ABC.

See ``SPEC.md`` §Plugin 1 for the per-method contract. Every method here
mirrors a row in that table.

Behaviour invariants (do not violate):
- Never block the agent. Every Sonzai call is wrapped; failures log and
  degrade (empty context / skipped persist), never raise into Hermes.
- ``sync_turn`` and ``queue_prefetch`` are non-blocking — daemon threads
  for all I/O.
- ``on_pre_compress`` is a *safety net* flush via ``process()`` only. The
  Context Engine plugin owns ``consolidate()``. If a non-Sonzai context
  engine is paired with this provider, also call ``consolidate()`` here.
"""

from __future__ import annotations

import threading
from typing import Any


class SonzaiMemoryProvider:
    """Routes Hermes memory hooks to the Sonzai SDK."""

    name = "sonzai"

    def __init__(self) -> None:
        self._client = None  # set in initialize()
        self._config = None  # set in initialize()
        self._agent_id: str | None = None
        self._user_id: str | None = None
        self._session_id: str | None = None
        self._degraded = False
        self._prefetch_cache: dict[str, str] = {}
        self._threads: list[threading.Thread] = []

    # ── availability / lifecycle ───────────────────────────────────────────

    def is_available(self) -> bool:
        """``True`` if ``SONZAI_API_KEY`` (or saved config key) is present.

        Value-check only — no network call.
        """
        raise NotImplementedError("Task 7 in PLAN.md.")

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        """Build client, resolve agent, start Sonzai session.

        kwargs must include ``hermes_home`` — all storage paths derive from it.
        Failure must NOT crash the agent — log + ``self._degraded = True``.
        """
        raise NotImplementedError("Task 8 in PLAN.md.")

    def shutdown(self) -> None:
        """Close client; join daemon threads with a short timeout."""
        raise NotImplementedError("Task 8 in PLAN.md.")

    # ── config ─────────────────────────────────────────────────────────────

    def get_config_schema(self) -> list[dict[str, Any]]:
        """Field descriptors per ``SPEC.md`` §Shared config.

        ``api_key`` → ``secret: True, env_var: "SONZAI_API_KEY", required: True``.
        """
        raise NotImplementedError("Task 9 in PLAN.md.")

    def save_config(self, values: dict[str, Any], hermes_home: str) -> None:
        """Persist non-secret keys. Secrets go to Hermes' .env flow."""
        raise NotImplementedError("Task 9 in PLAN.md.")

    # ── recall path ────────────────────────────────────────────────────────

    def prefetch(self, query: str) -> str:
        """Recall — analogue of OpenClaw ``assemble``.

        Calls ``client.agents.get_context(...)``, formats the
        ``EnrichedContextResponse`` as a single text block, trims to
        ``context_token_budget``. Returns ``""`` on any error.

        Honours ``memory_mode``:
        - ``sync``  → block until full response.
        - ``async`` → race a short deadline, return what's ready.
        """
        raise NotImplementedError("Task 10 in PLAN.md.")

    def queue_prefetch(self, query: str) -> None:
        """Optional warm-ahead — daemon thread; result stashed for next ``prefetch``."""
        raise NotImplementedError("Task 11 in PLAN.md.")

    # ── persist path ───────────────────────────────────────────────────────

    def sync_turn(self, user_content: str, assistant_content: str) -> None:
        """Per-turn persist — analogue of OpenClaw ``afterTurn``. MUST NOT BLOCK.

        Spawns a daemon thread that calls ``client.agents.process(...)`` with
        the user+assistant pair, driving fact extraction and the 2h
        ``recent_turns`` buffer.
        """
        raise NotImplementedError("Task 12 in PLAN.md.")

    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        """Best-effort ``client.agents.sessions.end(...)``."""
        raise NotImplementedError("Task 13 in PLAN.md.")

    def on_pre_compress(self, messages: list[dict[str, Any]]) -> None:
        """Safety-net flush for the about-to-discard window.

        Calls ``client.agents.process(...)`` only — consolidation lives in the
        Context Engine plugin. If running alongside a non-Sonzai context
        engine, also call ``consolidate()`` here (see SPEC.md row).
        """
        raise NotImplementedError("Task 14 in PLAN.md.")

    # ── prompt / tools ─────────────────────────────────────────────────────

    def system_prompt_block(self) -> str:
        """Static block telling the model it has a Sonzai-backed long-term memory."""
        raise NotImplementedError("Task 15 in PLAN.md.")

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Expose ``sonzai_memory_search`` and ``sonzai_memory_write`` to the model."""
        raise NotImplementedError("Task 16 in PLAN.md.")

    def handle_tool_call(self, name: str, args: dict[str, Any]) -> str:
        """Route to ``client.agents.memory.search`` / ``create_fact``. Returns JSON."""
        raise NotImplementedError("Task 16 in PLAN.md.")
