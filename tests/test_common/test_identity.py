"""resolve_agent_id + resolve_user_id."""

from __future__ import annotations

from unittest.mock import MagicMock

from plugins.memory.sonzai._common import SonzaiConfig, resolve_agent_id, resolve_user_id


def test_resolve_agent_id_uses_configured() -> None:
    client = MagicMock()
    cfg = SonzaiConfig(api_key="x", agent_id="agent_already_set")
    assert resolve_agent_id(client, cfg) == "agent_already_set"
    client.agents.create.assert_not_called()


def test_resolve_agent_id_provisions_when_missing() -> None:
    client = MagicMock()
    # The real SDK returns an ``Agent`` with ``.agent_id``.
    client.agents.create.return_value.agent_id = "agent_new"
    cfg = SonzaiConfig(api_key="x", agent_id=None, agent_name="hermes-agent")
    assert resolve_agent_id(client, cfg) == "agent_new"
    client.agents.create.assert_called_once_with(name="hermes-agent")


def test_resolve_agent_id_tolerates_dict_response() -> None:
    client = MagicMock()
    client.agents.create.return_value = {"agent_id": "agent_dict"}
    cfg = SonzaiConfig(api_key="x", agent_id=None)
    assert resolve_agent_id(client, cfg) == "agent_dict"


def test_resolve_user_id_cli_session() -> None:
    cfg = SonzaiConfig(api_key="x", default_user_id="owner")
    assert resolve_user_id(None, cfg) == "owner"
    assert resolve_user_id("", cfg) == "owner"
    assert resolve_user_id("cli-session-abc", cfg) == "owner"


def test_resolve_user_id_parses_handle_when_present() -> None:
    cfg = SonzaiConfig(api_key="x", default_user_id="owner")
    assert resolve_user_id("user:nas@sonz.ai/session-xyz", cfg) == "nas@sonz.ai"
    assert resolve_user_id("user:alice/sess-1", cfg) == "alice"


def test_resolve_user_id_empty_handle_falls_back_to_default() -> None:
    cfg = SonzaiConfig(api_key="x", default_user_id="owner")
    assert resolve_user_id("user:/abc", cfg) == "owner"
