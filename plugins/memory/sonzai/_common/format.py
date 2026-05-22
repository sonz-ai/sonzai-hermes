"""``EnrichedContextResponse → str`` formatter.

Renders Sonzai's 7-layer enriched context (agent profile, Big5/personality,
evolution/goals/habits/breakthroughs, relationship, mood, memory tree,
supplementary search, recent_turns) into a single ``<sonzai-context>`` text
block, trimmed to ``context_token_budget``.

The same formatter is used by:
- the Memory Provider's ``prefetch`` (returned to Hermes as the recall block)
- the Context Engine's ``compress`` (embedded into the rebuilt ``system`` message)

The output is intentionally token-bounded — caller passes ``token_budget``
(approx. ``len(text) / 4``). Sections are dropped lowest-priority first
when over budget. If a single section is still too long, the result is
hard-truncated with a ``[...truncated]`` marker.
"""

from __future__ import annotations

import json
from typing import Any

OPEN_TAG = "<sonzai-context>"
CLOSE_TAG = "</sonzai-context>"

# Rough char-per-token (matches openclaw's ``estimateTokens``).
CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def _as_dict(obj: Any) -> dict[str, Any]:
    """Coerce a pydantic model / dict / object into a plain dict."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    # Pydantic v2
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        try:
            return dump(exclude_none=True)
        except TypeError:
            return dump()
    # Generic attribute scrape
    return {k: v for k, v in vars(obj).items() if not k.startswith("_")}


def format_enriched_context(response: Any, token_budget: int) -> str:
    """Format Sonzai's enriched-context payload as a single text block.

    Returns ``""`` if ``response`` is None or empty.
    """
    if response is None:
        return ""

    data = _as_dict(response)
    if not data:
        return ""

    sections: list[tuple[int, str, str]] = []  # (priority, key, text)

    # ── personality (priority 7) ─────────────────────────────────────────
    personality = _format_personality(data)
    if personality:
        sections.append((7, "personality", personality))

    # ── memory tree (priority 6) ─────────────────────────────────────────
    memories = _format_memories(data)
    if memories:
        sections.append((6, "memories", memories))

    # ── recent_turns — pre-extraction raw (priority 6) ───────────────────
    recent = _format_recent_turns(data)
    if recent:
        sections.append((6, "recent_turns", recent))

    # ── current mood (priority 5) ────────────────────────────────────────
    mood = _format_mood(data)
    if mood:
        sections.append((5, "mood", mood))

    # ── relationship (priority 4) ────────────────────────────────────────
    relationship = _format_relationship(data)
    if relationship:
        sections.append((4, "relationship", relationship))

    # ── goals (priority 3) ───────────────────────────────────────────────
    goals = _format_goals(data)
    if goals:
        sections.append((3, "goals", goals))

    # ── interests (priority 2) ───────────────────────────────────────────
    interests = _format_interests(data)
    if interests:
        sections.append((2, "interests", interests))

    # ── habits (priority 1) ──────────────────────────────────────────────
    habits = _format_habits(data)
    if habits:
        sections.append((1, "habits", habits))

    # Also surface a generic ``agent`` block when a test stub passes one.
    agent_block = _format_agent(data.get("agent"))
    if agent_block:
        sections.insert(0, (8, "agent", agent_block))

    if not sections:
        return ""

    sections.sort(key=lambda s: s[0], reverse=True)
    body = "\n\n".join(text for _, _, text in sections)
    result = f"{OPEN_TAG}\n{body}\n{CLOSE_TAG}"

    while _estimate_tokens(result) > token_budget and len(sections) > 1:
        # drop lowest-priority section
        sections.pop()
        body = "\n\n".join(text for _, _, text in sections)
        result = f"{OPEN_TAG}\n{body}\n{CLOSE_TAG}"

    if _estimate_tokens(result) > token_budget:
        # Single section still too long — hard truncate.
        max_chars = token_budget * CHARS_PER_TOKEN
        head = OPEN_TAG + "\n"
        tail = "\n[...truncated]\n" + CLOSE_TAG
        budget_for_body = max(0, max_chars - len(head) - len(tail))
        result = head + body[:budget_for_body] + tail

    return result


# ─── section formatters ────────────────────────────────────────────────────


def _format_agent(agent: Any) -> str | None:
    if not agent:
        return None
    data = _as_dict(agent)
    if not data:
        return None
    lines = ["## Agent"]
    if data.get("name"):
        lines.append(f"Name: {data['name']}")
    if data.get("bio"):
        lines.append(f"Bio: {data['bio']}")
    personality = data.get("personality")
    if isinstance(personality, dict):
        big5 = personality.get("big5")
        if isinstance(big5, dict):
            traits = " ".join(f"{k[:1].upper()}:{v}" for k, v in big5.items())
            if traits:
                lines.append(f"Big5: {traits}")
    return "\n".join(lines) if len(lines) > 1 else None


def _format_personality(data: dict[str, Any]) -> str | None:
    has_any = any(
        data.get(k)
        for k in ("personality_prompt", "primary_traits", "speech_patterns", "big5")
    )
    if not has_any:
        return None

    lines = ["## Personality"]
    if data.get("personality_prompt"):
        lines.append(f"Character: {data['personality_prompt']}")
    traits = data.get("primary_traits") or []
    if traits:
        lines.append(f"Traits: {', '.join(traits)}")
    patterns = data.get("speech_patterns") or []
    if patterns:
        lines.append(f"Speech patterns: {', '.join(patterns)}")

    big5 = data.get("big5")
    if isinstance(big5, dict) and big5:
        parts: list[str] = []
        for key, label in (
            ("openness", "O"),
            ("conscientiousness", "C"),
            ("extraversion", "E"),
            ("agreeableness", "A"),
            ("neuroticism", "N"),
        ):
            val = big5.get(key)
            if isinstance(val, dict):
                val = val.get("score")
            if val is not None:
                parts.append(f"{label}:{val}")
        if parts:
            lines.append("Big5: " + " ".join(parts))

    return "\n".join(lines) if len(lines) > 1 else None


def _format_memories(data: dict[str, Any]) -> str | None:
    facts: list[Any] = []

    raw_facts = data.get("loaded_facts")
    if isinstance(raw_facts, list):
        facts.extend(raw_facts)

    memory_block = data.get("memory")
    if isinstance(memory_block, dict):
        nested = memory_block.get("facts")
        if isinstance(nested, list):
            facts.extend(nested)

    if not facts:
        return None

    lines = ["## Relevant Memories"]
    for fact in facts[:10]:
        if isinstance(fact, str):
            lines.append(f"- {fact}")
        elif isinstance(fact, dict):
            text = fact.get("atomic_text") or fact.get("content") or json.dumps(fact)
            lines.append(f"- {text}")
        else:
            lines.append(f"- {fact}")
    return "\n".join(lines)


def _format_recent_turns(data: dict[str, Any]) -> str | None:
    turns = data.get("recent_turns")
    if not isinstance(turns, list) or not turns:
        return None
    lines = ["## Recent Context (this session, not yet consolidated)"]
    for turn in turns[-6:]:
        turn = _as_dict(turn) if not isinstance(turn, dict) else turn
        role = turn.get("role") or "user"
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        # collapse whitespace
        content = " ".join(content.split())
        lines.append(f"- {role}: {content}")
    return "\n".join(lines) if len(lines) > 1 else None


def _format_mood(data: dict[str, Any]) -> str | None:
    mood = data.get("current_mood")
    if mood is None:
        mood = data.get("mood")
    if mood is None:
        return None
    if isinstance(mood, str):
        return f"## Current Mood\n{mood}"
    mood_dict = _as_dict(mood)
    if not mood_dict:
        return None
    lines = ["## Current Mood"]
    for key, value in mood_dict.items():
        if key.startswith("_") or value is None:
            continue
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        lines.append(f"{key}: {value}")
    return "\n".join(lines) if len(lines) > 1 else None


def _format_relationship(data: dict[str, Any]) -> str | None:
    fields_with_values: list[tuple[str, Any]] = []
    if data.get("relationship_narrative"):
        fields_with_values.append(("narrative", data["relationship_narrative"]))
    for key in ("love_from_agent", "love_from_user", "chemistry_score", "relationship_status"):
        if data.get(key) is not None:
            fields_with_values.append((key, data[key]))
    if not fields_with_values:
        return None
    lines = ["## Relationship"]
    for key, value in fields_with_values:
        lines.append(f"{key}: {value}")
    return "\n".join(lines)


def _format_goals(data: dict[str, Any]) -> str | None:
    goals = data.get("active_goals") or data.get("goals")
    if not isinstance(goals, list) or not goals:
        return None
    lines = ["## Goals"]
    for goal in goals[:5]:
        g = _as_dict(goal) if not isinstance(goal, dict) else goal
        title = g.get("title", "")
        desc = g.get("description", "")
        gtype = g.get("type", "")
        lines.append(f"- {title}: {desc} [{gtype}]".rstrip(" []"))
    return "\n".join(lines) if len(lines) > 1 else None


def _format_interests(data: dict[str, Any]) -> str | None:
    interests = data.get("true_interests") or data.get("interests")
    if not interests:
        return None
    if isinstance(interests, list):
        return f"## Interests\n{', '.join(str(i) for i in interests)}"
    if isinstance(interests, dict):
        lines = ["## Interests"]
        for key, value in interests.items():
            if value is None:
                continue
            lines.append(f"{key}: {value}")
        return "\n".join(lines) if len(lines) > 1 else None
    return None


def _format_habits(data: dict[str, Any]) -> str | None:
    habits = data.get("habits")
    if not isinstance(habits, list) or not habits:
        return None
    lines = ["## Habits"]
    for habit in habits[:5]:
        h = _as_dict(habit) if not isinstance(habit, dict) else habit
        name = h.get("name", "")
        desc = h.get("description") or h.get("category") or ""
        lines.append(f"- {name}: {desc}".rstrip(": "))
    return "\n".join(lines) if len(lines) > 1 else None
