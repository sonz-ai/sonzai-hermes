"""BYOK (Bring-Your-Own-Key) bootstrap.

Python port of ``sonzai-openclaw/src/byok.ts``. On plugin load, detects
provider keys the user has already set (``OPENAI_API_KEY`` etc.) and
registers them with the Sonzai platform via
``PUT /api/v1/projects/{projectId}/byok-keys/{provider}``.

Once registered, the Sonzai platform routes LLM calls through the
customer's own provider account, charging only the 25% service fee
(vs the ~125% markup on the platform's keys). Net effect: users
already paying for OpenAI/Gemini/xAI/OpenRouter pay almost nothing
extra to use Sonzai's memory layer.

Registration is idempotent (PUT semantics) so we can call it on every
startup without worrying about duplicate state.

Precedence per provider (matches openclaw):
  1. ``SONZAI_BYOK_<PROVIDER>_KEY`` (namespaced — explicit BYOK opt-in)
  2. Standard provider env var (``OPENAI_API_KEY``, ``GEMINI_API_KEY`` …)
  3. Not registered.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sonzai import Sonzai

    from .config import SonzaiConfig

logger = logging.getLogger("sonzai.hermes.byok")

# Providers Sonzai's platform supports as BYOK targets. Keep in sync with
# the platform's ``BYOKProvider`` enum.
BYOK_PROVIDERS: tuple[str, ...] = ("openai", "gemini", "xai", "openrouter")

# Standard env-var names per provider. The first hit wins. Gemini accepts
# both ``GEMINI_API_KEY`` (Google AI Studio convention) and ``GOOGLE_API_KEY``
# (broader Google ecosystem) so users don't have to rename anything.
_STANDARD_ENV_NAMES: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "xai": ("XAI_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
}


@dataclass
class ByokRegistration:
    provider: str
    project_id: str
    source_env: str  # which env var the key came from


def detect_byok_keys(env: dict[str, str] | None = None) -> dict[str, tuple[str, str]]:
    """Return ``{provider: (key, source_env_name)}`` for keys present in env.

    Empty dict if none found. ``env`` defaults to ``os.environ``.
    """
    e = env if env is not None else os.environ
    out: dict[str, tuple[str, str]] = {}
    for provider in BYOK_PROVIDERS:
        # Namespaced override wins.
        ns_name = f"SONZAI_BYOK_{provider.upper()}_KEY"
        ns_value = e.get(ns_name, "").strip()
        if ns_value:
            out[provider] = (ns_value, ns_name)
            continue
        # Fall back to standard provider env names.
        for name in _STANDARD_ENV_NAMES[provider]:
            value = e.get(name, "").strip()
            if value:
                out[provider] = (value, name)
                break
    return out


def _listing_to_items(listing: Any) -> list[Any]:
    """Coerce ``client.projects.list()`` output into a flat ``list[project]``.

    The SDK currently returns a ``sonzai._pagination.Page`` iterator. Older
    shapes returned a ``{"items": [...]}`` dict or an object with ``.items``
    as a list attribute. Handle all three so we don't break on SDK upgrades.
    """
    if listing is None:
        return []
    if isinstance(listing, list):
        return listing
    if isinstance(listing, dict):
        items = listing.get("items")
        return list(items) if items else []
    # Object exposing ``.items`` as a list (older SDK shape, also how our
    # test mocks pose). Check this BEFORE first_page() so MagicMock's
    # auto-created ``first_page`` callable doesn't shadow real test data.
    items = getattr(listing, "items", None)
    if isinstance(items, list):
        return items
    # Real SDK ``Page`` exposes ``first_page()`` — single round-trip.
    first_page = getattr(listing, "first_page", None)
    if callable(first_page):
        try:
            result = first_page()
            return list(result) if result else []
        except Exception:
            pass
    # Last resort: try to iterate (Page is an iterator). Bounded to avoid
    # walking unbounded tenants — first 100 is plenty for "Default" lookup.
    try:
        out: list[Any] = []
        for i, item in enumerate(listing):
            out.append(item)
            if i >= 99:
                break
        return out
    except TypeError:
        return []


def resolve_project_id(client: "Sonzai", config: "SonzaiConfig") -> str | None:
    """Return ``config.project_id`` if set; else find a default project.

    Strategy mirrors openclaw:
      1. ``config.project_id``
      2. First project named ``"Default"``
      3. Only project if the tenant has exactly one
      4. ``None`` — caller skips registration with a warning.
    """
    if config.project_id:
        return config.project_id
    try:
        # ``client.projects.list()`` shape: typed via the SDK's pydantic model.
        # Defensive duck-typing here in case the SDK list shape evolves.
        listing = client.projects.list(page_size=100)
    except Exception as err:
        logger.warning("sonzai BYOK: projects.list() failed: %s", err)
        return None

    items = _listing_to_items(listing)
    if not items:
        return None

    def _pid(p: Any) -> str | None:
        if isinstance(p, dict):
            return p.get("project_id") or p.get("id")
        return getattr(p, "project_id", None) or getattr(p, "id", None)

    def _name(p: Any) -> str:
        if isinstance(p, dict):
            return p.get("name") or ""
        return getattr(p, "name", "") or ""

    for p in items:
        if _name(p) == "Default":
            pid = _pid(p)
            if pid:
                return pid

    if len(items) == 1:
        return _pid(items[0])

    return None


def register_byok_keys(
    client: "Sonzai",
    config: "SonzaiConfig",
    *,
    env: dict[str, str] | None = None,
) -> list[ByokRegistration]:
    """Detect + register every available BYOK key. Never raises.

    Returns the list of successfully registered providers. Failures log
    and continue — a bad/expired key for one provider must not block the
    others, and must never break Hermes startup.
    """
    detected = detect_byok_keys(env)
    if not detected:
        return []

    project_id = resolve_project_id(client, config)
    if not project_id:
        logger.info(
            "sonzai BYOK: %d provider key(s) detected (%s) but no project_id "
            "resolved — set SONZAI_PROJECT_ID or ensure a 'Default' project exists.",
            len(detected),
            ", ".join(sorted(detected)),
        )
        return []

    registered: list[ByokRegistration] = []
    for provider, (api_key, source_env) in detected.items():
        try:
            client.byok.set(project_id, provider, api_key=api_key)
            registered.append(
                ByokRegistration(
                    provider=provider, project_id=project_id, source_env=source_env
                )
            )
            logger.info(
                "sonzai BYOK: registered %s (from %s) on project %s",
                provider,
                source_env,
                project_id,
            )
        except Exception as err:
            logger.warning(
                "sonzai BYOK: failed to register %s (from %s): %s",
                provider,
                source_env,
                err,
            )
    return registered


def register_byok_keys_async(
    client: "Sonzai",
    config: "SonzaiConfig",
) -> threading.Thread:
    """Fire-and-forget BYOK registration on a daemon thread.

    Plugins call this from their lifecycle hook so the 1–4 HTTP round-trips
    don't add latency to Hermes startup. Idempotent on the platform side.
    """

    def _run() -> None:
        try:
            register_byok_keys(client, config)
        except Exception as err:
            # register_byok_keys is itself never-raise; this is belt + braces.
            logger.warning("sonzai BYOK background register failed: %s", err)

    t = threading.Thread(
        target=_run, name="sonzai-byok-register", daemon=True
    )
    t.start()
    return t
