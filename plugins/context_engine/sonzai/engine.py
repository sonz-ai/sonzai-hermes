"""``SonzaiContextEngine`` — implements Hermes' ``ContextEngine`` ABC.

Token-budget compression, NOT recall. When the window approaches the model
limit, hand the history to Sonzai's consolidation pipeline and rebuild a
compact window from canonical facts + ``recent_turns`` + the live tail.

See ``SPEC.md`` §Plugin 2 for the per-method contract.

Behaviour invariants:
- Never raise into Hermes — every Sonzai call is wrapped.
- One agent identity across both plugins (shared ``sonzai_common``).
- Three-call chain on compress: ``process`` → ``consolidate`` → ``get_context``.
"""

from __future__ import annotations

import logging
from typing import Any

from sonzai_common import (
    SonzaiConfig,
    build_client,
    close_client,
    format_enriched_context,
    load_config,
    resolve_agent_id,
    resolve_user_id,
)

logger = logging.getLogger("sonzai.hermes.context_engine")

# Default recency tail — number of raw turns preserved verbatim after the
# rebuilt system block. Kept small so the freshly-injected context dominates
# the rebuilt window.
DEFAULT_RECENCY_TAIL_N = 6


class SonzaiContextEngine:
    """Routes Hermes context-engine hooks to Sonzai's consolidation pipeline."""

    name = "sonzai"

    DEFAULT_TRIGGER_RATIO = 0.75

    def __init__(self) -> None:
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self.threshold_tokens = 0
        self.context_length = 0
        self.compression_count = 0

        self._client = None
        self._config: SonzaiConfig | None = None
        self._agent_id: str | None = None
        self._user_id: str | None = None
        self._session_id: str | None = None
        self._hermes_home: Any = None
        self._degraded = False
        self._recency_tail_n = DEFAULT_RECENCY_TAIL_N

    # ── token bookkeeping ──────────────────────────────────────────────────

    def update_from_response(self, usage: dict[str, int]) -> None:
        """Store usage counters from the latest LLM response."""
        if not usage:
            return
        self.last_prompt_tokens = int(usage.get("prompt_tokens", self.last_prompt_tokens))
        self.last_completion_tokens = int(
            usage.get("completion_tokens", self.last_completion_tokens)
        )
        self.last_total_tokens = int(usage.get("total_tokens", self.last_total_tokens))

    def update_model(self, model: str, context_length: int, **kwargs: Any) -> None:
        """Set ``context_length``; recompute ``threshold_tokens``."""
        self.context_length = int(context_length)
        self.threshold_tokens = int(self.context_length * self.DEFAULT_TRIGGER_RATIO)

    def should_compress(self, prompt_tokens: int | None = None) -> bool:
        """``True`` when (prompt_tokens or last_prompt_tokens) ≥ threshold."""
        if self.threshold_tokens <= 0:
            return False
        observed = prompt_tokens if prompt_tokens is not None else self.last_prompt_tokens
        return observed >= self.threshold_tokens

    # ── lifecycle ──────────────────────────────────────────────────────────

    def on_session_start(self, session_id: str, **kwargs: Any) -> None:
        """Build client, resolve agent, ``sessions.start``. Never raises."""
        self._session_id = session_id
        self._hermes_home = kwargs.get("hermes_home")
        try:
            self._config = load_config(self._hermes_home)
            self._client = build_client(self._config)
            self._agent_id = resolve_agent_id(self._client, self._config)
            self._user_id = resolve_user_id(session_id, self._config)
            self._client.agents.sessions.start(
                self._agent_id,
                user_id=self._user_id,
                session_id=session_id,
            )
            self._degraded = False
        except Exception as err:
            logger.warning("sonzai context engine degraded: %s", err)
            self._degraded = True

    def on_session_end(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        """End the Sonzai session and close the client."""
        if not self._degraded and self._client is not None and self._agent_id is not None:
            try:
                self._client.agents.sessions.end(
                    self._agent_id,
                    user_id=self._user_id,
                    session_id=session_id,
                )
            except Exception as err:
                logger.warning("sonzai sessions.end failed: %s", err)
        close_client(self._client)
        self._client = None
        self._agent_id = None
        self._user_id = None
        self._session_id = None

    def on_session_reset(self) -> None:
        """Drop per-session counters; keep the client."""
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self.compression_count = 0

    # ── compression ────────────────────────────────────────────────────────

    def compress(
        self,
        messages: list[dict[str, Any]],
        current_tokens: int | None = None,
        focus_topic: str | None = None,
    ) -> list[dict[str, Any]]:
        """Three sync RPCs, then rebuild.

        (1) ``process(messages_slice)`` extract in-flight facts.
        (2) ``consolidate()`` fold them into canonical memory.
        (3) ``get_context(query=focus_topic or last_user_msg)`` pull
            freshly-consolidated state.
        (4) Rebuild: one ``system`` message with the formatted context
            block, then the last N raw turns verbatim.
        (5) ``compression_count += 1``.

        Always returns a valid OpenAI-format ``list[{"role","content"}]``.
        If Sonzai is unavailable, returns ``messages`` unchanged — Hermes
        will fall back to its own compaction.
        """
        self.compression_count += 1

        if (
            self._degraded
            or self._client is None
            or self._agent_id is None
            or self._user_id is None
        ):
            return list(messages)

        last_user_msg = focus_topic or _last_user_content(messages)
        slice_for_process = _slice_for_process(messages, self._recency_tail_n)

        # (1) process — extract facts from the about-to-discard window.
        try:
            if slice_for_process:
                self._client.agents.process(
                    self._agent_id,
                    user_id=self._user_id,
                    messages=slice_for_process,
                    session_id=self._session_id,
                )
        except Exception as err:
            logger.warning("sonzai compress.process failed: %s", err)

        # (2) consolidate.
        try:
            self._client.agents.consolidate(self._agent_id, user_id=self._user_id)
        except Exception as err:
            logger.warning("sonzai compress.consolidate failed: %s", err)

        # (3) get_context — pull the freshly-consolidated state.
        try:
            context = self._client.agents.get_context(
                self._agent_id,
                user_id=self._user_id,
                session_id=self._session_id,
                query=last_user_msg or "",
            )
        except Exception as err:
            logger.warning("sonzai compress.get_context failed: %s", err)
            context = None

        budget = self._config.context_token_budget if self._config else 2000
        system_block = format_enriched_context(context, budget)

        # (4) Rebuild: system + recency tail.
        rebuilt: list[dict[str, Any]] = []
        if system_block:
            rebuilt.append({"role": "system", "content": system_block})
        rebuilt.extend(_recency_tail(messages, self._recency_tail_n))
        return rebuilt

    # ── status / tools ─────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        """Engine health snapshot for ``hermes status``."""
        return {
            "engine": "sonzai",
            "compressions": self.compression_count,
            "last_prompt_tokens": self.last_prompt_tokens,
            "last_completion_tokens": self.last_completion_tokens,
            "last_total_tokens": self.last_total_tokens,
            "threshold_tokens": self.threshold_tokens,
            "context_length": self.context_length,
            "degraded": self._degraded,
        }

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return []

    def handle_tool_call(self, name: str, args: dict[str, Any], **kwargs: Any) -> str:
        return ""


# ─── helpers ───────────────────────────────────────────────────────────────


def _last_user_content(messages: list[dict[str, Any]]) -> str | None:
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str):
                return content
    return None


def _slice_for_process(
    messages: list[dict[str, Any]], recency_tail_n: int
) -> list[dict[str, str]]:
    """Return messages we're about to discard, as plain dicts the SDK accepts."""
    if not messages:
        return []
    end = max(0, len(messages) - recency_tail_n)
    out: list[dict[str, str]] = []
    for msg in messages[:end]:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if not isinstance(role, str) or not isinstance(content, str):
            continue
        out.append({"role": role, "content": content})
    return out


def _recency_tail(
    messages: list[dict[str, Any]], recency_tail_n: int
) -> list[dict[str, Any]]:
    if not messages:
        return []
    return list(messages[-recency_tail_n:])
