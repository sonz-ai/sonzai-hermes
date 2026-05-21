"""Sonzai SDK client construction shared by both plugins."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sonzai import Sonzai

    from sonzai_common.config import SonzaiConfig


def build_client(config: "SonzaiConfig") -> "Sonzai":
    """Return a configured ``Sonzai`` client. Raises ``ValueError`` if ``api_key`` is unset."""
    if not config.api_key:
        raise ValueError(
            "api_key is required; set SONZAI_API_KEY in the environment "
            "or run `hermes sonzai setup`."
        )

    from sonzai import Sonzai

    return Sonzai(api_key=config.api_key, base_url=config.base_url)


def close_client(client: "Sonzai | None") -> None:
    """Best-effort close. Never raises into Hermes."""
    if client is None:
        return
    try:
        client.close()
    except Exception:
        pass
