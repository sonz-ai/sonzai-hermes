"""Self-service trial onboarding via the Sonzai platform API.

Python port of ``sonzai-openclaw/src/onboarding.ts``. Keeps the wire
format identical so the platform doesn't need any changes — calls hit
the same ``POST /onboarding/trial`` and ``POST /api/v1/onboarding/claim-link``
endpoints openclaw uses.

The trial endpoint is **unauthenticated**: zero-touch provisioning. Cap
is global (~100/day); on 429 the caller falls back to manual paste.

Used by ``sonzai-hermes setup`` to onboard a user with no Sonzai account:

    $ sonzai-hermes setup
    No API key found — provisioning a 14-day trial...
    ✓ 14-day trial active (expires 2026-06-05). Memory is live.
      Claim before then to keep it permanent:
        https://platform.sonz.ai/onboarding/claim/<token>
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass

# Cloudflare in front of api.sonz.ai blocks the default
# ``Python-urllib/X.Y`` User-Agent (error 1010). Identify ourselves
# clearly so the request lands AND the platform team can see traffic
# coming from this plugin in their logs.
_USER_AGENT = "sonzai-hermes/0.1 (python urllib)"


class TrialCapReachedError(Exception):
    """Daily trial-key cap exhausted on the platform (HTTP 429)."""


@dataclass
class TrialResult:
    api_key: str
    agent_id: str
    tenant_id: str
    trial_expires_at: str  # RFC3339 from the platform
    claim_url: str


@dataclass
class ClaimLinkResult:
    claim_url: str
    expires_at: str  # RFC3339


def _hostname() -> str | None:
    try:
        return socket.gethostname() or None
    except Exception:
        return None


def generate_blurb(agent_name: str, role: str | None = None) -> str:
    """Build a short ``<agent> [— <role>] [on <host>]`` line for the claim card."""
    parts: list[str] = [agent_name]
    if role and role.strip():
        parts.append("— " + role.strip())
    host = _hostname()
    if host:
        parts.append("on " + host)
    return " ".join(parts)


def request_trial_key(
    base_url: str,
    *,
    agent_name: str | None = None,
    role: str | None = None,
    blurb: str | None = None,
    hostname: str | None = None,
    timeout: float = 15.0,
) -> TrialResult:
    """POST /onboarding/trial — request a zero-touch 14-day trial.

    Raises ``TrialCapReachedError`` on HTTP 429 so callers can fall back
    to manual paste. Raises ``RuntimeError`` on any other non-2xx.
    """
    url = base_url.rstrip("/") + "/onboarding/trial"
    payload = {
        "client": {
            "hostname": hostname if hostname is not None else (_hostname() or ""),
            "agent_name": agent_name or "",
            "role": role or "",
            "blurb": blurb or generate_blurb(agent_name or "hermes-agent", role),
        }
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        if err.code == 429:
            raise TrialCapReachedError() from err
        detail = err.read().decode("utf-8", errors="replace") if err.fp else ""
        raise RuntimeError(f"trial issuance failed ({err.code}): {detail}") from err

    return TrialResult(
        api_key=body["api_key"],
        agent_id=body["agent_id"],
        tenant_id=body["tenant_id"],
        trial_expires_at=body["trial_expires_at"],
        claim_url=body["claim_url"],
    )


def request_claim_link(
    base_url: str,
    api_key: str,
    *,
    timeout: float = 10.0,
) -> ClaimLinkResult:
    """POST /api/v1/onboarding/claim-link — get a fresh claim URL for the trial.

    Authenticated via the trial API key as Bearer. Used by ``sonzai-hermes claim``
    so a user can re-print the claim URL after losing the original.
    """
    url = base_url.rstrip("/") + "/api/v1/onboarding/claim-link"
    req = urllib.request.Request(
        url,
        data=b"{}",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace") if err.fp else ""
        raise RuntimeError(f"claim-link failed ({err.code}): {detail}") from err

    return ClaimLinkResult(
        claim_url=body["claim_url"],
        expires_at=body["expires_at"],
    )
