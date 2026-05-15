"""Config resolution for both plugins.

Precedence (per SPEC.md §Shared foundation):
    env var > saved config file > default

Secrets (``api_key``) come from ``SONZAI_API_KEY`` / Hermes' ``.env`` flow.
Non-secret keys persist in ``<hermes_home>/sonzai.json`` via ``save_config``.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

DEFAULT_AGENT_NAME = "hermes-agent"
DEFAULT_BASE_URL = "https://api.sonz.ai"
DEFAULT_USER_ID = "owner"
DEFAULT_MEMORY_MODE = "sync"  # "sync" | "async"
DEFAULT_CONTEXT_TOKEN_BUDGET = 2000

CONFIG_FILENAME = "sonzai.json"


@dataclass
class SonzaiConfig:
    api_key: str | None = None
    agent_id: str | None = None
    agent_name: str = DEFAULT_AGENT_NAME
    base_url: str = DEFAULT_BASE_URL
    default_user_id: str = DEFAULT_USER_ID
    memory_mode: str = DEFAULT_MEMORY_MODE
    context_token_budget: int = DEFAULT_CONTEXT_TOKEN_BUDGET


def load_config(hermes_home: str | os.PathLike[str] | None) -> SonzaiConfig:
    """Resolve config: env > saved file > defaults."""
    raise NotImplementedError("Implement per SPEC.md §Shared config — Task 3 in PLAN.md.")


def save_config(values: dict[str, Any], hermes_home: str | os.PathLike[str]) -> None:
    """Persist non-secret keys to ``<hermes_home>/sonzai.json``.

    Secrets (``api_key``) are NOT written here — Hermes' ``.env`` flow handles them.
    """
    raise NotImplementedError("Implement per SPEC.md §Shared config — Task 3 in PLAN.md.")
