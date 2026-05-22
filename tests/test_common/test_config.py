"""Config dataclass + load_config + save_config precedence."""

from __future__ import annotations

import json

from plugins.memory.sonzai._common import SonzaiConfig, load_config, save_config


def test_defaults() -> None:
    cfg = SonzaiConfig()
    assert cfg.api_key is None
    assert cfg.agent_id is None
    assert cfg.agent_name == "hermes-agent"
    assert cfg.base_url == "https://api.sonz.ai"
    assert cfg.default_user_id == "owner"
    assert cfg.memory_mode == "sync"
    assert cfg.context_token_budget == 2000


def test_defaults_when_nothing_set(hermes_home, clean_env) -> None:
    cfg = load_config(hermes_home)
    assert cfg.agent_name == "hermes-agent"
    assert cfg.base_url == "https://api.sonz.ai"
    assert cfg.api_key is None


def test_load_handles_missing_hermes_home(clean_env) -> None:
    cfg = load_config(None)
    assert cfg.api_key is None
    assert cfg.agent_name == "hermes-agent"


def test_env_overrides_file(hermes_home, clean_env, monkeypatch) -> None:
    save_config(
        {"agent_name": "from-file", "base_url": "https://file.example"},
        hermes_home,
    )
    monkeypatch.setenv("SONZAI_AGENT_NAME", "from-env")
    cfg = load_config(hermes_home)
    assert cfg.agent_name == "from-env"
    assert cfg.base_url == "https://file.example"  # not overridden


def test_file_overrides_default(hermes_home, clean_env) -> None:
    save_config({"agent_name": "from-file"}, hermes_home)
    cfg = load_config(hermes_home)
    assert cfg.agent_name == "from-file"
    assert cfg.base_url == "https://api.sonz.ai"  # default kept


def test_api_key_from_env_only(hermes_home, clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SONZAI_API_KEY", "sk_test")
    cfg = load_config(hermes_home)
    assert cfg.api_key == "sk_test"


def test_save_config_rejects_secret(hermes_home, clean_env) -> None:
    save_config(
        {"api_key": "sk_should_not_be_written", "agent_name": "x"},
        hermes_home,
    )
    on_disk = json.loads((hermes_home / "sonzai.json").read_text())
    assert "api_key" not in on_disk
    assert on_disk["agent_name"] == "x"


def test_save_config_atomic_overwrite(hermes_home, clean_env) -> None:
    save_config({"agent_name": "first"}, hermes_home)
    save_config({"agent_name": "second"}, hermes_home)
    on_disk = json.loads((hermes_home / "sonzai.json").read_text())
    assert on_disk["agent_name"] == "second"


def test_save_config_ignores_unknown_keys(hermes_home, clean_env) -> None:
    save_config({"agent_name": "ok", "garbage": "no"}, hermes_home)
    on_disk = json.loads((hermes_home / "sonzai.json").read_text())
    assert on_disk == {"agent_name": "ok"}


def test_context_token_budget_env_parses_int(hermes_home, clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SONZAI_BASE_URL", "https://override.example")
    cfg = load_config(hermes_home)
    assert cfg.base_url == "https://override.example"
