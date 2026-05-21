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

import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path
from typing import Any

from sonzai_common import (
    SonzaiConfig,
    build_client,
    close_client,
    format_enriched_context,
    load_config,
    resolve_agent_id,
    resolve_user_id,
    save_config as common_save_config,
)

logger = logging.getLogger("sonzai.hermes.memory")

# Async-prefetch deadline. Picked to match a human-perceptible blink so
# Hermes never stalls on Sonzai when ``memory_mode=async``.
ASYNC_PREFETCH_DEADLINE_S = 0.6

# Bounded background worker pool — keeps sync_turn cheap and lets shutdown
# join everything in flight on a single short timeout.
_BG_WORKERS = 4


class SonzaiMemoryProvider:
    """Routes Hermes memory hooks to the Sonzai SDK."""

    name = "sonzai"

    def __init__(self) -> None:
        self._client = None
        self._config: SonzaiConfig | None = None
        self._agent_id: str | None = None
        self._user_id: str | None = None
        self._session_id: str | None = None
        self._hermes_home: str | None = None
        self._degraded = False
        self._prefetch_cache: dict[str, str] = {}
        self._cache_lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._threads: list[threading.Thread] = []

    # ── availability / lifecycle ───────────────────────────────────────────

    def is_available(self) -> bool:
        """``True`` if ``SONZAI_API_KEY`` (env or saved config) is present.

        Value-check only — no network.
        """
        if os.environ.get("SONZAI_API_KEY"):
            return True
        # Saved config never stores api_key (secret) — fall back to env only.
        # Returning False when env is unset matches Hermes' is_available contract.
        return False

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        """Build client, resolve agent, start Sonzai session. Never raises."""
        self._session_id = session_id
        self._hermes_home = kwargs.get("hermes_home")
        self._executor = ThreadPoolExecutor(
            max_workers=_BG_WORKERS, thread_name_prefix="sonzai-memory"
        )

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
        except Exception as err:
            logger.warning("sonzai memory provider degraded: %s", err)
            self._degraded = True

    def shutdown(self) -> None:
        """Close client; join background work with a short timeout."""
        if self._executor is not None:
            # Don't wait forever — daemon threads, best-effort.
            self._executor.shutdown(wait=True, cancel_futures=False)
            self._executor = None
        close_client(self._client)
        self._client = None

    # ── config ─────────────────────────────────────────────────────────────

    def get_config_schema(self) -> list[dict[str, Any]]:
        """Field descriptors per ``SPEC.md`` §Shared config."""
        return [
            {
                "key": "api_key",
                "label": "Sonzai API key",
                "secret": True,
                "required": True,
                "env_var": "SONZAI_API_KEY",
                "url": "https://sonz.ai",
                "description": "Project API key from https://sonz.ai/dashboard.",
            },
            {
                "key": "agent_id",
                "label": "Sonzai agent ID",
                "secret": False,
                "required": False,
                "env_var": "SONZAI_AGENT_ID",
                "description": "Leave blank to auto-provision one named `agent_name`.",
            },
            {
                "key": "agent_name",
                "label": "Sonzai agent name",
                "secret": False,
                "required": False,
                "env_var": "SONZAI_AGENT_NAME",
                "default": "hermes-agent",
                "description": "Stable name → deterministic agent UUID (idempotent create).",
            },
            {
                "key": "base_url",
                "label": "Sonzai API base URL",
                "secret": False,
                "required": False,
                "env_var": "SONZAI_BASE_URL",
                "default": "https://api.sonz.ai",
            },
            {
                "key": "default_user_id",
                "label": "Default user id (1:1 CLI)",
                "secret": False,
                "required": False,
                "default": "owner",
            },
            {
                "key": "memory_mode",
                "label": "Memory prefetch mode",
                "secret": False,
                "required": False,
                "env_var": "SONZAI_MEMORY_MODE",
                "default": "sync",
                "choices": ["sync", "async"],
                "description": "`sync` waits for full context; `async` races a deadline.",
            },
            {
                "key": "context_token_budget",
                "label": "Context token budget",
                "secret": False,
                "required": False,
                "default": 2000,
                "description": "Cap on injected `<sonzai-context>` block size.",
            },
            {
                "key": "also_consolidate",
                "label": "Also consolidate in on_pre_compress",
                "secret": False,
                "required": False,
                "default": False,
                "description": (
                    "Enable when paired with a non-Sonzai context engine — the Sonzai "
                    "context engine already owns consolidation."
                ),
            },
        ]

    def save_config(self, values: dict[str, Any], hermes_home: str) -> None:
        """Persist non-secret keys. Secrets go to Hermes' .env flow."""
        common_save_config(values, hermes_home)

    # ── recall path ────────────────────────────────────────────────────────

    def prefetch(self, query: str) -> str:
        """Recall path. Returns formatted ``<sonzai-context>`` block, or ``""``."""
        if self._degraded or self._client is None or self._agent_id is None:
            return ""

        cached = self._take_cache(query)
        if cached is not None:
            return cached

        mode = (self._config.memory_mode if self._config else "sync").lower()
        if mode == "async":
            return self._prefetch_async(query)
        return self._prefetch_sync(query)

    def _prefetch_sync(self, query: str) -> str:
        try:
            response = self._client.agents.get_context(  # type: ignore[union-attr]
                self._agent_id,
                user_id=self._user_id,
                session_id=self._session_id,
                query=query,
            )
            budget = self._config.context_token_budget if self._config else 2000
            return format_enriched_context(response, budget)
        except Exception as err:
            logger.warning("sonzai prefetch failed: %s", err)
            return ""

    def _prefetch_async(self, query: str) -> str:
        if self._executor is None:
            return ""
        try:
            fut = self._executor.submit(self._prefetch_sync, query)
            return fut.result(timeout=ASYNC_PREFETCH_DEADLINE_S)
        except FuturesTimeout:
            return ""
        except Exception as err:
            logger.warning("sonzai async prefetch failed: %s", err)
            return ""

    def queue_prefetch(self, query: str) -> None:
        """Warm the cache so the next ``prefetch(query)`` is instant."""
        if self._degraded or self._executor is None:
            return

        def _warm() -> None:
            try:
                result = self._prefetch_sync(query)
                self._put_cache(query, result)
            except Exception:
                pass

        try:
            self._executor.submit(_warm)
        except RuntimeError:
            # Executor was shut down — silently drop.
            pass

    def _take_cache(self, query: str) -> str | None:
        with self._cache_lock:
            return self._prefetch_cache.pop(query, None)

    def _put_cache(self, query: str, value: str) -> None:
        with self._cache_lock:
            self._prefetch_cache[query] = value

    # ── persist path ───────────────────────────────────────────────────────

    def sync_turn(self, user_content: str, assistant_content: str) -> None:
        """Per-turn persist. MUST NOT BLOCK — spawns a daemon thread."""
        if self._degraded or self._client is None or self._agent_id is None:
            return
        if self._executor is None:
            return

        messages = [
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": assistant_content},
        ]

        def _persist() -> None:
            try:
                self._client.agents.process(  # type: ignore[union-attr]
                    self._agent_id,
                    user_id=self._user_id,
                    messages=messages,
                    session_id=self._session_id,
                )
            except Exception as err:
                logger.warning("sonzai sync_turn failed: %s", err)

        try:
            self._executor.submit(_persist)
        except RuntimeError:
            pass

    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        """Best-effort session end."""
        if self._degraded or self._client is None or self._agent_id is None:
            return
        try:
            self._client.agents.sessions.end(
                self._agent_id,
                user_id=self._user_id,
                session_id=self._session_id,
            )
        except Exception as err:
            logger.warning("sonzai sessions.end failed: %s", err)

    def on_pre_compress(self, messages: list[dict[str, Any]]) -> None:
        """Safety-net flush before window compression.

        Only flushes via ``process()`` — the Context Engine plugin owns
        ``consolidate()``. Set ``also_consolidate=true`` in the saved
        config to additionally trigger consolidation when this provider
        is paired with a non-Sonzai context engine.
        """
        if self._degraded or self._client is None or self._agent_id is None:
            return
        if not messages:
            return
        try:
            self._client.agents.process(
                self._agent_id,
                user_id=self._user_id,
                messages=messages,
                session_id=self._session_id,
            )
        except Exception as err:
            logger.warning("sonzai on_pre_compress process failed: %s", err)

        also = self._extra_consolidate()
        if also:
            try:
                self._client.agents.consolidate(self._agent_id, user_id=self._user_id)
            except Exception as err:
                logger.warning("sonzai on_pre_compress consolidate failed: %s", err)

    def _extra_consolidate(self) -> bool:
        """Read the ``also_consolidate`` flag from the saved config file."""
        if not self._hermes_home:
            return False
        try:
            path = Path(self._hermes_home) / "sonzai.json"
            if not path.exists():
                return False
            data = json.loads(path.read_text())
            return bool(data.get("also_consolidate", False))
        except Exception:
            return False

    # ── prompt / tools ─────────────────────────────────────────────────────

    def system_prompt_block(self) -> str:
        """Tell the model it has Sonzai-backed long-term memory."""
        return (
            "You have a persistent long-term memory powered by Sonzai. "
            "Relevant memories, personality, mood, and recent context are "
            "injected automatically inside a `<sonzai-context>` block on "
            "each turn — read it, but don't echo or summarize it back to "
            "the user. Use `sonzai_memory_search` to query explicitly and "
            "`sonzai_memory_write` to record a fact you want preserved."
        )

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Expose explicit search + write tools to the model."""
        return [
            {
                "name": "sonzai_memory_search",
                "description": (
                    "Search the Sonzai memory layer for facts relevant to a query. "
                    "Returns matching memories with relevance scores."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural language query.",
                        }
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "sonzai_memory_write",
                "description": (
                    "Record a new atomic fact in Sonzai's long-term memory. "
                    "Use sparingly — for facts you want to recall in future sessions."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "The fact to remember.",
                        }
                    },
                    "required": ["content"],
                },
            },
        ]

    def handle_tool_call(self, name: str, args: dict[str, Any]) -> str:
        """Dispatch ``sonzai_memory_*`` tool calls. Returns JSON string."""
        if self._degraded or self._client is None or self._agent_id is None:
            return json.dumps({"error": "sonzai memory unavailable"})

        try:
            if name == "sonzai_memory_search":
                query = str(args.get("query", "")).strip()
                if not query:
                    return json.dumps({"error": "query is required"})
                result = self._client.agents.memory.search(
                    self._agent_id,
                    query=query,
                    user_id=self._user_id,
                )
                return _to_json(result)

            if name == "sonzai_memory_write":
                content = str(args.get("content", "")).strip()
                if not content:
                    return json.dumps({"error": "content is required"})
                result = self._client.agents.memory.create_fact(
                    self._agent_id,
                    content=content,
                    user_id=self._user_id,
                )
                return _to_json(result)

            return json.dumps({"error": f"unknown tool: {name}"})
        except Exception as err:
            logger.warning("sonzai handle_tool_call(%s) failed: %s", name, err)
            return json.dumps({"error": str(err)})


def _to_json(obj: Any) -> str:
    """Best-effort JSON serialization that tolerates pydantic models."""
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        try:
            return json.dumps(dump(exclude_none=True), default=str)
        except TypeError:
            return json.dumps(dump(), default=str)
    if isinstance(obj, (dict, list, str, int, float, bool)) or obj is None:
        return json.dumps(obj, default=str)
    # Fallback — let json default kick in.
    return json.dumps(obj, default=str)
