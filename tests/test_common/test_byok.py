"""BYOK detection + registration."""

from __future__ import annotations

from unittest.mock import MagicMock

from plugins.memory.sonzai._common import (
    BYOK_PROVIDERS,
    SonzaiConfig,
    detect_byok_keys,
    register_byok_keys,
    resolve_project_id,
)


# ─── detect_byok_keys ──────────────────────────────────────────────────────


def test_detect_finds_standard_env_names() -> None:
    env = {
        "OPENAI_API_KEY": "sk-openai",
        "GEMINI_API_KEY": "google-key",
        "XAI_API_KEY": "xai-key",
        "OPENROUTER_API_KEY": "or-key",
        "UNRELATED": "ignore-me",
    }
    out = detect_byok_keys(env)
    assert out["openai"] == ("sk-openai", "OPENAI_API_KEY")
    assert out["gemini"] == ("google-key", "GEMINI_API_KEY")
    assert out["xai"] == ("xai-key", "XAI_API_KEY")
    assert out["openrouter"] == ("or-key", "OPENROUTER_API_KEY")


def test_detect_namespaced_overrides_standard() -> None:
    env = {
        "OPENAI_API_KEY": "standard-key",
        "SONZAI_BYOK_OPENAI_KEY": "namespaced-key",
    }
    out = detect_byok_keys(env)
    assert out["openai"] == ("namespaced-key", "SONZAI_BYOK_OPENAI_KEY")


def test_detect_gemini_accepts_google_api_key() -> None:
    env = {"GOOGLE_API_KEY": "google-key"}
    out = detect_byok_keys(env)
    assert out["gemini"] == ("google-key", "GOOGLE_API_KEY")


def test_detect_empty_when_no_keys() -> None:
    assert detect_byok_keys({}) == {}
    # blank keys are ignored
    assert detect_byok_keys({"OPENAI_API_KEY": ""}) == {}
    assert detect_byok_keys({"OPENAI_API_KEY": "   "}) == {}


def test_byok_providers_constant() -> None:
    assert set(BYOK_PROVIDERS) == {"openai", "gemini", "xai", "openrouter"}


# ─── resolve_project_id ────────────────────────────────────────────────────


def test_resolve_uses_configured_project_id() -> None:
    cfg = SonzaiConfig(api_key="x", project_id="proj-explicit")
    client = MagicMock()
    assert resolve_project_id(client, cfg) == "proj-explicit"
    client.projects.list.assert_not_called()


def _project(project_id: str, name: str) -> MagicMock:
    # MagicMock(name=...) is reserved for the mock's repr — set it explicitly.
    m = MagicMock(project_id=project_id)
    m.name = name
    return m


def test_resolve_picks_default_project() -> None:
    cfg = SonzaiConfig(api_key="x")
    client = MagicMock()
    listing = MagicMock()
    listing.items = [_project("proj-other", "Other"), _project("proj-default", "Default")]
    client.projects.list.return_value = listing
    assert resolve_project_id(client, cfg) == "proj-default"


def test_resolve_picks_only_project() -> None:
    cfg = SonzaiConfig(api_key="x")
    client = MagicMock()
    listing = MagicMock()
    listing.items = [_project("proj-only", "Anything")]
    client.projects.list.return_value = listing
    assert resolve_project_id(client, cfg) == "proj-only"


def test_resolve_returns_none_when_ambiguous() -> None:
    cfg = SonzaiConfig(api_key="x")
    client = MagicMock()
    listing = MagicMock()
    listing.items = [_project("a", "Alpha"), _project("b", "Beta")]
    client.projects.list.return_value = listing
    assert resolve_project_id(client, cfg) is None


def test_resolve_handles_dict_response() -> None:
    cfg = SonzaiConfig(api_key="x")
    client = MagicMock()
    client.projects.list.return_value = {"items": [{"project_id": "p1", "name": "Default"}]}
    assert resolve_project_id(client, cfg) == "p1"


# ─── register_byok_keys ────────────────────────────────────────────────────


def test_register_skips_when_no_detected_keys() -> None:
    cfg = SonzaiConfig(api_key="x", project_id="p")
    client = MagicMock()
    assert register_byok_keys(client, cfg, env={}) == []
    client.byok.set.assert_not_called()


def test_register_skips_when_no_project_id_resolvable() -> None:
    cfg = SonzaiConfig(api_key="x", project_id=None)
    client = MagicMock()
    listing = MagicMock()
    listing.items = []  # no projects
    client.projects.list.return_value = listing
    out = register_byok_keys(client, cfg, env={"OPENAI_API_KEY": "sk"})
    assert out == []
    client.byok.set.assert_not_called()


def test_register_calls_byok_set_for_each_detected_key() -> None:
    cfg = SonzaiConfig(api_key="x", project_id="proj-1")
    client = MagicMock()
    env = {
        "OPENAI_API_KEY": "sk-openai",
        "GEMINI_API_KEY": "google-k",
    }
    result = register_byok_keys(client, cfg, env=env)
    providers = {r.provider for r in result}
    assert providers == {"openai", "gemini"}
    # Two PUT calls — one per provider.
    assert client.byok.set.call_count == 2
    # Check the call kwargs.
    calls = {call.args[1]: call for call in client.byok.set.call_args_list}
    assert calls["openai"].kwargs["api_key"] == "sk-openai"
    assert calls["gemini"].kwargs["api_key"] == "google-k"


def test_register_continues_when_one_provider_fails() -> None:
    cfg = SonzaiConfig(api_key="x", project_id="proj-1")
    client = MagicMock()

    def fail_openai(project_id, provider, *, api_key):
        if provider == "openai":
            raise RuntimeError("auth failed")
        return MagicMock()

    client.byok.set.side_effect = fail_openai
    env = {"OPENAI_API_KEY": "bad", "GEMINI_API_KEY": "good"}
    result = register_byok_keys(client, cfg, env=env)
    # Only gemini registered; openai failure was logged but didn't raise.
    providers = {r.provider for r in result}
    assert providers == {"gemini"}


def test_register_never_raises_on_total_failure() -> None:
    cfg = SonzaiConfig(api_key="x", project_id=None)
    client = MagicMock()
    client.projects.list.side_effect = RuntimeError("network")
    # Must NOT raise.
    result = register_byok_keys(client, cfg, env={"OPENAI_API_KEY": "sk"})
    assert result == []
