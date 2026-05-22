"""onboarding — trial issuance + claim-link HTTP helpers."""

from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest

from plugins.memory.sonzai._common import (
    ClaimLinkResult,
    TrialCapReachedError,
    TrialResult,
    generate_blurb,
    request_claim_link,
    request_trial_key,
)


def test_generate_blurb_basic() -> None:
    out = generate_blurb("hermes-agent", role="cli")
    assert "hermes-agent" in out
    assert "cli" in out


def _fake_urlopen(*, status: int = 201, body: dict | None = None,
                  raise_429: bool = False):
    """Return a ``urlopen`` stand-in that returns the given payload."""
    from urllib.error import HTTPError

    def opener(req, timeout=None):
        if raise_429:
            raise HTTPError(req.full_url, 429, "Too Many Requests", {}, io.BytesIO(b'{"error":"trial_cap_reached"}'))

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

            def read(self):
                return json.dumps(body or {}).encode()

        return Resp()

    return opener


def test_request_trial_key_success() -> None:
    payload = {
        "api_key": "sk_trial_123",
        "agent_id": "agent_trial",
        "tenant_id": "tenant_trial",
        "trial_expires_at": "2026-06-05T00:00:00Z",
        "claim_url": "https://platform.sonz.ai/onboarding/claim/tok-abc",
    }
    with patch("plugins.memory.sonzai._common.onboarding.urllib.request.urlopen", _fake_urlopen(body=payload)):
        result = request_trial_key(
            "https://api.sonz.ai",
            agent_name="hermes-agent",
            blurb="test blurb",
        )

    assert isinstance(result, TrialResult)
    assert result.api_key == "sk_trial_123"
    assert result.agent_id == "agent_trial"
    assert result.claim_url.endswith("/tok-abc")


def test_request_trial_key_raises_on_429() -> None:
    with patch(
        "plugins.memory.sonzai._common.onboarding.urllib.request.urlopen",
        _fake_urlopen(raise_429=True),
    ):
        with pytest.raises(TrialCapReachedError):
            request_trial_key("https://api.sonz.ai", agent_name="hermes-agent")


def test_request_trial_key_propagates_other_errors() -> None:
    from urllib.error import HTTPError

    def boom(req, timeout=None):
        raise HTTPError(req.full_url, 500, "Server Error", {}, io.BytesIO(b'{"error":"boom"}'))

    with patch("plugins.memory.sonzai._common.onboarding.urllib.request.urlopen", boom):
        with pytest.raises(RuntimeError, match="trial issuance failed"):
            request_trial_key("https://api.sonz.ai")


def test_request_claim_link_success() -> None:
    payload = {
        "claim_url": "https://platform.sonz.ai/onboarding/claim/tok-xyz",
        "expires_at": "2026-07-05T00:00:00Z",
    }
    captured: dict = {}

    def opener(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

            def read(self):
                return json.dumps(payload).encode()

        return Resp()

    with patch("plugins.memory.sonzai._common.onboarding.urllib.request.urlopen", opener):
        result = request_claim_link("https://api.sonz.ai", "sk_trial_abc")

    assert isinstance(result, ClaimLinkResult)
    assert result.claim_url.endswith("/tok-xyz")
    assert captured["url"].endswith("/api/v1/onboarding/claim-link")
    assert captured["auth"] == "Bearer sk_trial_abc"
