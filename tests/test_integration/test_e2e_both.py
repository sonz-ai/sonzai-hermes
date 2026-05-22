"""End-to-end smoke test for the Memory Provider + Context Engine together.

Opt-in via ``pytest -m integration``. Requires ``SONZAI_API_KEY``.

Drives a small synthetic conversation to force the context engine over
its threshold, then asserts the rebuild shape: one ``system`` message
containing ``<sonzai-context>`` followed by the verbatim recency tail.
"""

from __future__ import annotations

import os
import uuid

import pytest

from plugins.context_engine.sonzai import SonzaiContextEngine
from plugins.memory.sonzai import SonzaiMemoryProvider

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("SONZAI_API_KEY"),
        reason="SONZAI_API_KEY not set — skipping live integration test",
    ),
]


def test_e2e_both_plugins_cooperate(hermes_home) -> None:
    session_id = f"hermes-it-both-{uuid.uuid4().hex[:8]}"

    memory = SonzaiMemoryProvider()
    engine = SonzaiContextEngine()

    memory.initialize(session_id=session_id, hermes_home=str(hermes_home))
    engine.on_session_start(session_id, hermes_home=str(hermes_home))

    # Tiny "context window" so should_compress fires after a few turns.
    engine.update_model("synthetic", context_length=400)

    try:
        assert memory._degraded is False
        assert engine._degraded is False
        # Same identity across both plugins.
        assert memory._agent_id == engine._agent_id

        # Drive a synthetic 12-turn conversation.
        messages: list[dict[str, str]] = []
        for i in range(12):
            u = f"user msg {i}: please retain marker-{session_id}-{i}"
            a = f"assistant ack {i}"
            messages.append({"role": "user", "content": u})
            messages.append({"role": "assistant", "content": a})
            memory.sync_turn(u, a)

        # Force-trigger compress regardless of actual token math.
        engine.last_prompt_tokens = engine.threshold_tokens + 1
        assert engine.should_compress() is True

        compressed = engine.compress(messages, focus_topic="marker")

        assert isinstance(compressed, list)
        assert compressed[0]["role"] == "system"
        assert "<sonzai-context>" in compressed[0]["content"]
        # Recency tail preserved verbatim.
        assert compressed[-1] == messages[-1]
        assert engine.compression_count >= 1
    finally:
        memory.on_session_end([])
        memory.shutdown()
        engine.on_session_end(session_id, [])
