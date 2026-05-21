"""Config resolution for both plugins.

Precedence (per SPEC.md §Shared foundation):
    env var > saved config file > default

Secrets (``api_key``) come from ``SONZAI_API_KEY`` / Hermes' ``.env`` flow.
Non-secret keys persist in ``<hermes_home>/sonzai.json`` via ``save_config``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

DEFAULT_AGENT_NAME = "hermes-agent"
DEFAULT_BASE_URL = "https://api.sonz.ai"
DEFAULT_USER_ID = "owner"
DEFAULT_MEMORY_MODE = "sync"  # "sync" | "async"
DEFAULT_CONTEXT_TOKEN_BUDGET = 2000

CONFIG_FILENAME = "sonzai.json"

# Map ``SonzaiConfig`` field → environment variable name.
ENV_OVERRIDES: dict[str, str] = {
    "api_key": "SONZAI_API_KEY",
    "agent_id": "SONZAI_AGENT_ID",
    "agent_name": "SONZAI_AGENT_NAME",
    "base_url": "SONZAI_BASE_URL",
    "memory_mode": "SONZAI_MEMORY_MODE",
}

# Fields that must never be persisted to disk — they live in Hermes' ``.env``.
SECRET_FIELDS: frozenset[str] = frozenset({"api_key"})


@dataclass
class SonzaiConfig:
    api_key: str | None = None
    agent_id: str | None = None
    agent_name: str = DEFAULT_AGENT_NAME
    base_url: str = DEFAULT_BASE_URL
    default_user_id: str = DEFAULT_USER_ID
    memory_mode: str = DEFAULT_MEMORY_MODE
    context_token_budget: int = DEFAULT_CONTEXT_TOKEN_BUDGET


def _config_path(hermes_home: str | os.PathLike[str]) -> Path:
    return Path(hermes_home) / CONFIG_FILENAME


def load_config(hermes_home: str | os.PathLike[str] | None) -> SonzaiConfig:
    """Resolve config: env > saved file > defaults.

    ``hermes_home`` may be ``None`` (env + defaults only). Unknown keys in
    the saved file are ignored so config schema additions stay forward-compat.
    """
    cfg = SonzaiConfig()

    if hermes_home is not None:
        path = _config_path(hermes_home)
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                data = {}
            valid_keys = {f.name for f in fields(SonzaiConfig)}
            for key, value in data.items():
                if key in valid_keys and key not in SECRET_FIELDS:
                    setattr(cfg, key, value)

    for field_name, env_var in ENV_OVERRIDES.items():
        raw = os.environ.get(env_var)
        if raw is None or raw == "":
            continue
        if field_name == "context_token_budget":
            try:
                setattr(cfg, field_name, int(raw))
            except ValueError:
                continue
        else:
            setattr(cfg, field_name, raw)

    return cfg


def save_config(values: dict[str, Any], hermes_home: str | os.PathLike[str]) -> None:
    """Persist non-secret keys to ``<hermes_home>/sonzai.json`` (atomic).

    Secrets (``api_key``) are NOT written here — Hermes' ``.env`` flow handles them.
    """
    home = Path(hermes_home)
    home.mkdir(parents=True, exist_ok=True)
    path = _config_path(home)

    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            existing = {}

    valid_keys = {f.name for f in fields(SonzaiConfig)}
    for key, value in values.items():
        if key in SECRET_FIELDS:
            continue
        if key not in valid_keys:
            continue
        existing[key] = value

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(existing, indent=2, sort_keys=True))
    os.replace(tmp, path)
