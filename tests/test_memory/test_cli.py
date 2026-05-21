"""hermes sonzai setup + health."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from plugins.memory.sonzai.cli import run_health, run_setup


def test_setup_writes_non_secret_config(hermes_home, clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_from_env")

    answers = iter(["", "agent_pre_set", "", ""])  # name, agent_id, base_url, mode
    out_lines: list[str] = []

    with patch("plugins.memory.sonzai.cli.build_client") as bc:
        client = MagicMock()
        bc.return_value = client
        rc = run_setup(
            hermes_home=hermes_home,
            prompt=lambda _: next(answers),
            secret_prompt=lambda _: "should_not_be_asked",
            out=lambda s: out_lines.append(s),
        )

    assert rc == 0
    on_disk = json.loads((hermes_home / "sonzai.json").read_text())
    assert "api_key" not in on_disk
    assert on_disk["agent_id"] == "agent_pre_set"
    assert on_disk["agent_name"] == "hermes-agent"
    assert on_disk["memory_mode"] == "sync"


def test_setup_provisions_when_no_agent_id(hermes_home, clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_from_env")
    answers = iter(["my-agent", "", "", "async"])

    with patch("plugins.memory.sonzai.cli.build_client") as bc, patch(
        "plugins.memory.sonzai.cli.resolve_agent_id", return_value="agent_provisioned"
    ):
        bc.return_value = MagicMock()
        rc = run_setup(
            hermes_home=hermes_home,
            prompt=lambda _: next(answers),
            secret_prompt=lambda _: "x",
            out=lambda s: None,
        )

    assert rc == 0
    on_disk = json.loads((hermes_home / "sonzai.json").read_text())
    assert on_disk["agent_id"] == "agent_provisioned"
    assert on_disk["agent_name"] == "my-agent"
    assert on_disk["memory_mode"] == "async"


def test_setup_returns_error_when_no_api_key(hermes_home, clean_env) -> None:
    answers = iter(["", "", "", ""])
    out_lines: list[str] = []
    rc = run_setup(
        hermes_home=hermes_home,
        prompt=lambda _: next(answers),
        secret_prompt=lambda _: "",  # user accepts no key
        out=lambda s: out_lines.append(s),
    )
    assert rc == 1


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
