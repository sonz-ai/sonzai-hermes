"""Agent-id and user-id resolution.

- ``agent_id``: use configured if set; otherwise ``client.agents.create(name=...)``
  which is idempotent on the Sonzai side (deterministic UUID from
  ``SHA1(tenant + "/" + lowercase(agent_name))``).

- ``user_id``: maps from Hermes ``session_id``. 1:1 CLI sessions → ``default_user_id``.
  Multi-user transports use the ``user:HANDLE/...`` prefix to embed a stable
  user handle; the parser extracts it. Mirrors openclaw's ``parseSessionKey``
  in spirit; the wire format differs because Hermes' session ids look
  different from OpenClaw's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sonzai import Sonzai

    from .config import SonzaiConfig


def resolve_agent_id(client: "Sonzai", config: "SonzaiConfig") -> str:
    """Return existing ``config.agent_id`` or provision one via idempotent create.

    The Sonzai SDK's ``agents.create`` is idempotent on ``(tenant, name)`` —
    same key + same name yields the same agent UUID, so this is safe to call
    on every startup.
    """
    if config.agent_id:
        return config.agent_id
    agent = client.agents.create(name=config.agent_name)
    return _extract_agent_id(agent)


def _extract_agent_id(agent: Any) -> str:
    """Pull the agent id off the SDK response, tolerating both shapes.

    The real Python SDK returns ``Agent`` with ``agent_id``. Older / mocked
    shapes may expose ``.id``. Dict responses surface ``"agent_id"`` first.
    """
    if isinstance(agent, dict):
        for key in ("agent_id", "id"):
            if agent.get(key):
                return str(agent[key])
    for attr in ("agent_id", "id"):
        value = getattr(agent, attr, None)
        if value:
            return str(value)
    raise RuntimeError("Sonzai agents.create returned no agent id")


def resolve_user_id(session_id: str | None, config: "SonzaiConfig") -> str:
    """Map Hermes session id → Sonzai user id.

    Conventions:
    - ``None`` or unrecognized → ``config.default_user_id`` (1:1 CLI).
    - ``user:<handle>/...`` → extract ``<handle>`` for multi-user transports.
    """
    if not session_id:
        return config.default_user_id

    if session_id.startswith("user:"):
        remainder = session_id[len("user:") :]
        handle, _, _ = remainder.partition("/")
        handle = handle.strip()
        if handle:
            return handle

    return config.default_user_id
