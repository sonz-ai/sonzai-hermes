"""``SonzaiContextEngine`` — implements Hermes' ``ContextEngine`` ABC.

Token-budget compression, NOT recall. When the window approaches the model
limit, hand the history to Sonzai's consolidation pipeline and rebuild a
compact window from canonical facts + ``recent_turns`` + the live tail.

See ``SPEC.md`` §Plugin 2 for the per-method contract.
"""

from __future__ import annotations

from typing import Any


class SonzaiContextEngine:
    """Routes Hermes context-engine hooks to Sonzai's consolidation pipeline."""

    name = "sonzai"

    # Trigger ratio: compress when prompt usage exceeds this fraction of
    # ``context_length``. Kept conservative to leave headroom for the
    # completion plus the rebuilt system block.
    DEFAULT_TRIGGER_RATIO = 0.75

    def __init__(self) -> None:
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self.threshold_tokens = 0
        self.context_length = 0
        self.compression_count = 0

        self._client = None
        self._config = None
        self._agent_id: str | None = None
        self._user_id: str | None = None
        self._session_id: str | None = None

    # ── token bookkeeping ──────────────────────────────────────────────────

    def update_from_response(self, usage: dict[str, int]) -> None:
        """Store usage from the latest LLM response."""
        raise NotImplementedError("Task 18 in PLAN.md.")

    def update_model(self, model: str, context_length: int, **kwargs: Any) -> None:
        """Set ``context_length``; recompute ``threshold_tokens``."""
        raise NotImplementedError("Task 19 in PLAN.md.")

    def should_compress(self, prompt_tokens: int | None = None) -> bool:
        """``True`` when (prompt_tokens or last_prompt_tokens) ≥ ``threshold_tokens``."""
        raise NotImplementedError("Task 19 in PLAN.md.")

    # ── lifecycle ──────────────────────────────────────────────────────────

    def on_session_start(self, session_id: str, **kwargs: Any) -> None:
        """Build client, resolve agent, ``sessions.start``."""
        raise NotImplementedError("Task 20 in PLAN.md.")

    def on_session_end(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        """``sessions.end``; ``client.close()``."""
        raise NotImplementedError("Task 20 in PLAN.md.")

    def on_session_reset(self) -> None:
        """Drop per-session cache; keep the client."""
        raise NotImplementedError("Task 20 in PLAN.md.")

    # ── compression ────────────────────────────────────────────────────────

    def compress(
        self,
        messages: list[dict[str, Any]],
        current_tokens: int | None = None,
        focus_topic: str | None = None,
    ) -> list[dict[str, Any]]:
        """Three sync RPCs, then rebuild.

        (1) ``client.agents.process(messages=messages_slice)`` — extract any
            in-flight facts from the window we're about to discard.
        (2) ``client.agents.consolidate()`` — fold them into canonical facts.
        (3) ``client.agents.get_context(query=focus_topic or last_user_msg)``
            — pull the freshly-consolidated state.
        (4) Rebuild: one ``system`` message with the formatted enriched-context
            block (capped to ``context_token_budget``), then the last N raw
            turns verbatim (recency tail).
        (5) ``compression_count += 1``.

        Returns a valid OpenAI-format ``list[{"role","content"}]`` under
        ``context_length``.

        Alternative one-call path (heaviest, session-boundary semantics):
            ``sessions.end(wait=True)`` → ``sessions.start(new)`` →
            ``get_context``. Use ``force_sync=True`` to bypass the server's
            ``ENABLE_ASYNC_SESSION_END`` toggle.
        """
        raise NotImplementedError("Task 21 in PLAN.md.")

    # ── status ─────────────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        """Engine health snapshot for ``hermes status``."""
        raise NotImplementedError("Task 22 in PLAN.md.")
