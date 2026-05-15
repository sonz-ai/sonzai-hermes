"""Shared pytest fixtures.

The plan (``PLAN.md``) builds on these — see Task 1.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def hermes_home(tmp_path: Path) -> Path:
    """A fresh ``hermes_home`` directory per test."""
    home = tmp_path / "hermes"
    home.mkdir()
    return home


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Strip all ``SONZAI_*`` env vars so tests start from a clean slate."""
    for key in list(os.environ):
        if key.startswith("SONZAI_"):
            monkeypatch.delenv(key, raising=False)
    yield
