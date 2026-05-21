"""Hermes Context Engine backed by Sonzai consolidation.

Auto-discovered: the ``ContextEngine`` subclass is exported at package level.
Users activate with ``context: engine: "sonzai"`` in Hermes config.

See ``../../../SPEC.md`` §Plugin 2 — Context Engine.
"""

from plugins.context_engine.sonzai.engine import SonzaiContextEngine

__all__ = ["SonzaiContextEngine", "register"]


def register(ctx) -> None:
    """Hermes plugin entry point. Called once at agent startup."""
    ctx.register_context_engine(SonzaiContextEngine())
