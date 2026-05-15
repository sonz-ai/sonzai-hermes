"""Agent-id and user-id resolution.

- ``agent_id``: use configured if set; otherwise ``client.agents.create(name=...)``
  which is idempotent on the Sonzai side (deterministic UUID from
  ``SHA1(tenant + "/" + lowercase(agent_name))``).

- ``user_id``: maps from Hermes ``session_id``. 1:1 CLI sessions → ``default_user_id``.
  Multi-user transports (if Hermes exposes them) → parse a stable user handle out
  of the session id; fall back to ``default_user_id``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sonzai import Sonzai

    from sonzai_common.config import SonzaiConfig


def resolve_agent_id(client: "Sonzai", config: "SonzaiConfig") -> str:
    """Return existing ``config.agent_id`` or provision one via idempotent create."""
    raise NotImplementedError("Implement per SPEC.md §Method contract — Task 5 in PLAN.md.")


def resolve_user_id(session_id: str | None, config: "SonzaiConfig") -> str:
    """Map Hermes session id → Sonzai user id; mirror openclaw ``parseSessionKey``."""
    raise NotImplementedError("Implement per SPEC.md §User-identity resolution — Task 5 in PLAN.md.")
