"""Plugin-local Sonzai helpers.

Inlined here so the plugin directory is self-contained and Hermes can
load it from ``$HERMES_HOME/plugins/sonzai/`` (or its bundled tree) with
no external Python deps beyond what ``plugin.yaml`` declares.

Mirrors the bundled-plugin convention used by Hermes' own memory
providers (honcho, hindsight, mem0): each plugin owns its helpers,
imports are relative, and external deps are listed under
``pip_dependencies`` in ``plugin.yaml``.

If you change anything in here, mirror the equivalent file in
``plugins/context_engine/sonzai/_common/`` to keep the two plugins in
sync. The file-level docstring of each module flags which ones are
shared and which are memory-only.
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
from .onboarding import (
    ClaimLinkResult,
    TrialCapReachedError,
    TrialResult,
    generate_blurb,
    request_claim_link,
    request_trial_key,
)

__all__ = [
    "BYOK_PROVIDERS",
    "ByokRegistration",
    "ClaimLinkResult",
    "DEFAULT_AGENT_NAME",
    "DEFAULT_BASE_URL",
    "DEFAULT_CONTEXT_TOKEN_BUDGET",
    "DEFAULT_MEMORY_MODE",
    "DEFAULT_USER_ID",
    "SonzaiConfig",
    "TrialCapReachedError",
    "TrialResult",
    "build_client",
    "close_client",
    "detect_byok_keys",
    "format_enriched_context",
    "generate_blurb",
    "load_config",
    "register_byok_keys",
    "register_byok_keys_async",
    "request_claim_link",
    "request_trial_key",
    "resolve_agent_id",
    "resolve_project_id",
    "resolve_user_id",
    "save_config",
]
