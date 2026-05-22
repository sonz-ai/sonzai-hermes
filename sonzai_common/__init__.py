"""Shared helpers used by both the Memory Provider and Context Engine plugins.

One source of truth for: client construction, config resolution + env
overrides, agent-id resolution/provisioning, user-id parsing, and the
``EnrichedContextResponse → str`` formatter.

Neither plugin imports from the other — they both import from here.
"""

__version__ = "0.1.0"

from sonzai_common.client import build_client, close_client
from sonzai_common.config import (
    DEFAULT_AGENT_NAME,
    DEFAULT_BASE_URL,
    DEFAULT_CONTEXT_TOKEN_BUDGET,
    DEFAULT_MEMORY_MODE,
    DEFAULT_USER_ID,
    SonzaiConfig,
    load_config,
    save_config,
)
from sonzai_common.format import format_enriched_context
from sonzai_common.identity import resolve_agent_id, resolve_user_id
from sonzai_common.onboarding import (
    ClaimLinkResult,
    TrialCapReachedError,
    TrialResult,
    generate_blurb,
    request_claim_link,
    request_trial_key,
)

__all__ = [
    "ClaimLinkResult",
    "DEFAULT_AGENT_NAME",
    "DEFAULT_BASE_URL",
    "DEFAULT_CONTEXT_TOKEN_BUDGET",
    "DEFAULT_MEMORY_MODE",
    "DEFAULT_USER_ID",
    "SonzaiConfig",
    "TrialCapReachedError",
    "TrialResult",
    "__version__",
    "build_client",
    "close_client",
    "format_enriched_context",
    "generate_blurb",
    "load_config",
    "request_claim_link",
    "request_trial_key",
    "resolve_agent_id",
    "resolve_user_id",
    "save_config",
]
