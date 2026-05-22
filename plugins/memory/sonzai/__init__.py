"""Hermes Memory Provider backed by Sonzai.

Auto-discovered by Hermes via the ``register(ctx)`` hook below.
See ``../../../SPEC.md`` §Plugin 1 — Memory Provider for the contract.
"""

from .provider import SonzaiMemoryProvider

__all__ = ["SonzaiMemoryProvider", "register"]


def register(ctx) -> None:
    """Hermes plugin entry point. Called once at agent startup."""
    ctx.register_memory_provider(SonzaiMemoryProvider())
