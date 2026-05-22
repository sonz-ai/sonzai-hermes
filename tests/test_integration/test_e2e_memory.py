"""End-to-end smoke test for the Memory Provider against a live Sonzai tenant.

Opt-in via ``pytest -m integration``. Requires:
- ``SONZAI_API_KEY`` set in the environment.

Walks: ``initialize → prefetch (cold) → sync_turn → prefetch (warm) →
on_session_end``. Asserts that the fact written via ``sync_turn`` is
recallable in the second ``prefetch`` (uses ``recent_turns``, 2h TTL).
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

from plugins.memory.sonzai import SonzaiMemoryProvider

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("SONZAI_API_KEY"),
        reason="SONZAI_API_KEY not set — skipping live integration test",
    ),
]


def test_e2e_recall_after_sync_turn(hermes_home) -> None:
    session_id = f"hermes-it-mem-{uuid.uuid4().hex[:8]}"
    p = SonzaiMemoryProvider()
    p.initialize(session_id=session_id, hermes_home=str(hermes_home))
    assert p._degraded is False, "live initialize should succeed"

    try:
        # Cold prefetch — facts about this query likely don't exist yet.
        cold = p.prefetch("my favourite test fact")
        assert isinstance(cold, str)

        # Persist a turn — this writes recent_turns (2h TTL) on the server.
        marker = f"the test marker for this run is {session_id}"
        p.sync_turn(
            user_content=f"please remember: {marker}",
            assistant_content="noted — I'll remember.",
        )

        # Wait for the background daemon thread to land the write.
        for _ in range(50):
            time.sleep(0.2)
            warm = p.prefetch(marker)
            if marker in warm:
                break

        assert marker in warm, f"expected marker in warm prefetch; got: {warm[:200]}"
    finally:
        p.on_session_end([])
        p.shutdown()
