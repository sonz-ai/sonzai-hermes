"""build_client + close_client."""

from __future__ import annotations

import pytest

from plugins.memory.sonzai._common import SonzaiConfig, build_client, close_client


def test_build_client_requires_api_key() -> None:
    with pytest.raises(ValueError, match="api_key"):
        build_client(SonzaiConfig())


def test_build_client_returns_client() -> None:
    cfg = SonzaiConfig(api_key="sk_test", base_url="https://api.sonz.ai")
    client = build_client(cfg)
    assert client is not None
    close_client(client)


def test_close_client_handles_none() -> None:
    close_client(None)


def test_close_client_swallows_errors() -> None:
    class Bad:
        def close(self) -> None:
            raise RuntimeError("boom")

    close_client(Bad())  # must not raise
