"""Sonzai SDK client construction shared by both plugins."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sonzai import Sonzai

    from sonzai_common.config import SonzaiConfig


def build_client(config: "SonzaiConfig") -> "Sonzai":
    """Return a configured ``Sonzai`` client. Raises if ``api_key`` is unset."""
    raise NotImplementedError("Implement per SPEC.md §SDK surface — Task 4 in PLAN.md.")


def close_client(client: "Sonzai | None") -> None:
    """Best-effort close. Never raises."""
    raise NotImplementedError("Implement per SPEC.md §SDK surface — Task 4 in PLAN.md.")
