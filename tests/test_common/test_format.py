"""format_enriched_context — the 7-layer formatter."""

from __future__ import annotations

from sonzai_common import format_enriched_context


def test_empty_response_returns_empty_string() -> None:
    assert format_enriched_context(None, token_budget=2000) == ""
    assert format_enriched_context({}, token_budget=2000) == ""


def test_renders_sonzai_context_block() -> None:
    response = {
        "personality_prompt": "playful and curious",
        "primary_traits": ["witty", "kind"],
        "loaded_facts": [{"atomic_text": "user lives in SG"}],
        "current_mood": {"valence": 0.7, "label": "curious"},
        "recent_turns": [{"role": "user", "content": "hi", "timestamp": "t"}],
    }
    out = format_enriched_context(response, token_budget=2000)
    assert out.startswith("<sonzai-context>")
    assert out.endswith("</sonzai-context>")
    assert "user lives in SG" in out
    assert "Personality" in out


def test_handles_string_facts() -> None:
    response = {"memory": {"facts": ["user lives in SG"]}}
    out = format_enriched_context(response, token_budget=2000)
    assert "user lives in SG" in out


def test_trims_to_token_budget() -> None:
    huge = {"loaded_facts": [{"atomic_text": "x" * 100} for _ in range(1000)]}
    out = format_enriched_context(huge, token_budget=200)
    # 1 token ≈ 4 chars, plus the wrapper tags
    assert len(out) <= 200 * 4 + len("<sonzai-context>") + len("</sonzai-context>") + 64


def test_drops_low_priority_sections_first() -> None:
    response = {
        "personality_prompt": "PERSONALITY_MARKER",
        "habits": [{"name": "early riser"}],
    }
    # Tight budget should keep personality (priority 7), drop habits (priority 1).
    out = format_enriched_context(response, token_budget=60)
    # personality wins
    assert "PERSONALITY_MARKER" in out


def test_recent_turns_rendered() -> None:
    response = {
        "recent_turns": [
            {"role": "user", "content": "hello there"},
            {"role": "assistant", "content": "hi back"},
        ]
    }
    out = format_enriched_context(response, token_budget=2000)
    assert "hello there" in out
    assert "hi back" in out


def test_pydantic_like_object_works() -> None:
    class FakeResp:
        personality_prompt = "from object"
        primary_traits = ["a", "b"]

        def model_dump(self, exclude_none: bool = True) -> dict:
            return {"personality_prompt": "from object", "primary_traits": ["a", "b"]}

    out = format_enriched_context(FakeResp(), token_budget=2000)
    assert "from object" in out
