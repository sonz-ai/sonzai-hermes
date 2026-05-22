"""sonzai setup / health / claim CLI."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from plugins.memory.sonzai.cli import run_claim, run_health, run_setup
from plugins.memory.sonzai._common import ClaimLinkResult, TrialCapReachedError, TrialResult

# Prompt order in run_setup:
#   1. Sonzai base URL
#   2. Agent name
#   3. Memory mode
#   4. (only when no key found) "Provision trial? [Y/n]"
#   5. (only when no agent_id) "Existing agent id [auto-provision]: "


def test_setup_writes_non_secret_config_with_env_key(
    hermes_home, clean_env, monkeypatch
) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_from_env")

    # base_url, agent_name, mode, agent_id
    answers = iter(["", "", "", "agent_pre_set"])
    out_lines: list[str] = []

    with patch("plugins.memory.sonzai.cli.build_client") as bc:
        bc.return_value = MagicMock()
        rc = run_setup(
            hermes_home=hermes_home,
            prompt=lambda _: next(answers),
            secret_prompt=lambda _: "should_not_be_asked",
            out=lambda s: out_lines.append(s),
            open_browser=False,
        )

    assert rc == 0
    on_disk = json.loads((hermes_home / "sonzai.json").read_text())
    assert "api_key" not in on_disk
    assert on_disk["agent_id"] == "agent_pre_set"
    assert on_disk["agent_name"] == "hermes-agent"
    assert on_disk["memory_mode"] == "sync"
    assert "_trial" not in on_disk

    env_file = hermes_home / ".env"
    assert "SONZAI_API_KEY=sk_from_env" in env_file.read_text()


def test_setup_provisions_when_no_agent_id(hermes_home, clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_from_env")
    # base_url, agent_name, mode, agent_id (blank → provision)
    answers = iter(["", "my-agent", "async", ""])

    with patch("plugins.memory.sonzai.cli.build_client") as bc, patch(
        "plugins.memory.sonzai.cli.resolve_agent_id", return_value="agent_provisioned"
    ):
        bc.return_value = MagicMock()
        rc = run_setup(
            hermes_home=hermes_home,
            prompt=lambda _: next(answers),
            secret_prompt=lambda _: "x",
            out=lambda s: None,
            open_browser=False,
        )

    assert rc == 0
    on_disk = json.loads((hermes_home / "sonzai.json").read_text())
    assert on_disk["agent_id"] == "agent_provisioned"
    assert on_disk["agent_name"] == "my-agent"
    assert on_disk["memory_mode"] == "async"


def test_setup_returns_error_when_no_api_key(hermes_home, clean_env) -> None:
    # base_url, agent_name, mode, trial_choice = "n", api_key="" via secret_prompt
    answers = iter(["", "", "", "n"])
    rc = run_setup(
        hermes_home=hermes_home,
        prompt=lambda _: next(answers),
        secret_prompt=lambda _: "",  # user declines manual paste
        out=lambda s: None,
        open_browser=False,
    )
    assert rc == 1


def test_setup_auto_trial_on_no_key(hermes_home, clean_env) -> None:
    # No env key, user accepts trial.
    answers = iter(["", "", "", "y", ""])  # base_url, name, mode, trial-yes, agent_id

    trial_result = TrialResult(
        api_key="sk_trial_abc",
        agent_id="agent_trial",
        tenant_id="tenant_trial",
        trial_expires_at="2026-06-05T00:00:00Z",
        claim_url="https://platform.sonz.ai/onboarding/claim/tok-xyz",
    )

    rc = run_setup(
        hermes_home=hermes_home,
        prompt=lambda _: next(answers),
        secret_prompt=lambda _: "",
        out=lambda s: None,
        open_browser=False,
        request_trial_key_fn=lambda *a, **k: trial_result,
    )
    assert rc == 0
    on_disk = json.loads((hermes_home / "sonzai.json").read_text())
    assert on_disk["agent_id"] == "agent_trial"
    # Trial metadata preserved.
    assert on_disk["_trial"]["claim_url"].endswith("/tok-xyz")
    assert on_disk["_trial"]["expires_at"] == "2026-06-05T00:00:00Z"
    # Secret never lands in sonzai.json.
    assert "api_key" not in on_disk
    # Key written to .env.
    env_text = (hermes_home / ".env").read_text()
    assert "SONZAI_API_KEY=sk_trial_abc" in env_text


def test_setup_falls_back_to_manual_on_trial_cap(hermes_home, clean_env) -> None:
    answers = iter(["", "", "", "y", ""])

    def boom(*a, **k):
        raise TrialCapReachedError()

    rc = run_setup(
        hermes_home=hermes_home,
        prompt=lambda _: next(answers),
        secret_prompt=lambda _: "sk_manual_paste",
        out=lambda s: None,
        open_browser=False,
        request_trial_key_fn=boom,
    )
    assert rc == 0
    env_text = (hermes_home / ".env").read_text()
    assert "SONZAI_API_KEY=sk_manual_paste" in env_text


def test_health_reports_status(hermes_home, clean_env, monkeypatch) -> None:
    out_lines: list[str] = []

    class FakeResp:
        status = 200
        reason = "OK"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(
        "plugins.memory.sonzai.cli.urllib.request.urlopen",
        lambda *a, **k: FakeResp(),
    )
    rc = run_health(hermes_home=hermes_home, out=lambda s: out_lines.append(s))
    assert rc == 0
    joined = "".join(out_lines)
    assert "200" in joined
    assert "/health" in joined


def test_claim_prints_url(hermes_home, clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_trial_abc")
    out_lines: list[str] = []
    result = ClaimLinkResult(
        claim_url="https://platform.sonz.ai/onboarding/claim/tok-zzz",
        expires_at="2026-07-05T00:00:00Z",
    )
    rc = run_claim(
        hermes_home=hermes_home,
        out=lambda s: out_lines.append(s),
        open_browser=False,
        request_claim_link_fn=lambda *a, **k: result,
    )
    assert rc == 0
    joined = "".join(out_lines)
    assert "/tok-zzz" in joined
    assert "2026-07-05" in joined


def test_claim_errors_without_key(hermes_home, clean_env) -> None:
    out_lines: list[str] = []
    rc = run_claim(
        hermes_home=hermes_home,
        out=lambda s: out_lines.append(s),
        open_browser=False,
        request_claim_link_fn=lambda *a, **k: ClaimLinkResult("", ""),
    )
    assert rc == 1
    assert any("SONZAI_API_KEY" in line for line in out_lines)
