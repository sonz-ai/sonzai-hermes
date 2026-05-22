"""Plugin-local Sonzai helpers.

Inlined here so the plugin directory is self-contained and Hermes can
load it from its bundled ``plugins/context_engine/<name>/`` tree with
no external Python deps beyond what ``plugin.yaml`` declares.

Mirrors ``plugins/memory/sonzai/_common/`` — keep the two in sync. The
context engine doesn't ship the ``onboarding`` module (only the setup
CLI under the memory plugin uses it).
"""

from .byok import (
    BYOK_PROVIDERS,
    ByokRegistration,
    detect_byok_keys,
    register_byok_keys,
    register_byok_keys_async,
    resolve_project_id,
)
from .client import build_client, close_client
from .config import (
    DEFAULT_AGENT_NAME,
    DEFAULT_BASE_URL,
    DEFAULT_CONTEXT_TOKEN_BUDGET,
    DEFAULT_MEMORY_MODE,
    DEFAULT_USER_ID,
    SonzaiConfig,
    load_config,
    save_config,
)
from .format import format_enriched_context
from .identity import resolve_agent_id, resolve_user_id

__all__ = [
    "BYOK_PROVIDERS",
    "ByokRegistration",
    "DEFAULT_AGENT_NAME",
    "DEFAULT_BASE_URL",
    "DEFAULT_CONTEXT_TOKEN_BUDGET",
    "DEFAULT_MEMORY_MODE",
    "DEFAULT_USER_ID",
    "SonzaiConfig",
    "build_client",
    "close_client",
    "detect_byok_keys",
    "format_enriched_context",
    "load_config",
    "register_byok_keys",
    "register_byok_keys_async",
    "resolve_agent_id",
    "resolve_project_id",
    "resolve_user_id",
    "save_config",
]
