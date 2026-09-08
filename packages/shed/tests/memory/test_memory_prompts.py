"""Regression tests for the snapshot and consolidation anti-spiral prompts."""

from roboshed.tools.consolidate_memory_example import CONSOLIDATE_MEMORY_EXAMPLE
from roboshed.tools.consolidate_memory_prompts import CONSOLIDATE_MEMORY_INSTRUCTIONS
from roboshed.tools.snapshot_conversation_prompts import (
    SNAPSHOT_CONVERSATION_INSTRUCTIONS,
)


def test_consolidate_prompt_prioritizes_user_authored_messages() -> None:
    assert "authority first, then recency" in CONSOLIDATE_MEMORY_INSTRUCTIONS
    assert "User-authored statements" in CONSOLIDATE_MEMORY_INSTRUCTIONS
    assert "Direct tool results" in CONSOLIDATE_MEMORY_INSTRUCTIONS
    assert "the newest claim wins" in CONSOLIDATE_MEMORY_INSTRUCTIONS
    assert "older than every new snapshot" in CONSOLIDATE_MEMORY_INSTRUCTIONS
    assert "ordered from oldest to newest" in CONSOLIDATE_MEMORY_INSTRUCTIONS
    assert "not an addendum or a history" in CONSOLIDATE_MEMORY_INSTRUCTIONS


def test_consolidate_prompt_keeps_standing_instructions_user_controlled() -> None:
    assert "The user alone controls this section" in CONSOLIDATE_MEMORY_INSTRUCTIONS
    assert "clearly intends it to endure across conversations" in (
        CONSOLIDATE_MEMORY_INSTRUCTIONS
    )
    assert "merely directs the current task is not a standing instruction" in (
        CONSOLIDATE_MEMORY_INSTRUCTIONS
    )
    assert "never discard an instruction" in CONSOLIDATE_MEMORY_INSTRUCTIONS


def test_consolidate_prompt_ranks_and_decays_non_standing_memory() -> None:
    assert "order bullets from highest to lowest retention priority" in (
        CONSOLIDATE_MEMORY_INSTRUCTIONS
    )
    assert "An untouched fact cannot gain priority" in CONSOLIDATE_MEMORY_INSTRUCTIONS
    assert "already-terse fact remains unreinforced" in (
        CONSOLIDATE_MEMORY_INSTRUCTIONS
    )
    assert "even when the file is below its maximum size" in (
        CONSOLIDATE_MEMORY_INSTRUCTIONS
    )
    assert "No non-standing category is immortal" in CONSOLIDATE_MEMORY_INSTRUCTIONS
    assert "## Recent changes" not in CONSOLIDATE_MEMORY_INSTRUCTIONS
    assert "## Recent changes" not in CONSOLIDATE_MEMORY_EXAMPLE
    assert "name every eviction" not in CONSOLIDATE_MEMORY_INSTRUCTIONS


def test_consolidate_example_demonstrates_temporal_and_authority_rules() -> None:
    assert "did not exist" in CONSOLIDATE_MEMORY_EXAMPLE
    assert "was created successfully" in CONSOLIDATE_MEMORY_EXAMPLE
    assert "`memory_policy.md` now exists" in CONSOLIDATE_MEMORY_EXAMPLE
    assert "inferred that the user might prefer an exhaustive" in (
        CONSOLIDATE_MEMORY_EXAMPLE
    )
    assert "Prefers lean implementations" in CONSOLIDATE_MEMORY_EXAMPLE
    assert "did not become a standing instruction" in CONSOLIDATE_MEMORY_EXAMPLE


def test_consolidate_example_has_genuine_interaction_and_snapshot_shape() -> None:
    headings = (
        "Example user message: conversation content",
        "Example assistant response: decoded value",
        "Explanation: not part of the assistant response",
    )
    for heading in headings:
        assert f"{heading}\n{'=' * len(heading)}" in CONSOLIDATE_MEMORY_EXAMPLE

    assert "## Previous memory\n\n# Persistent Memory" in CONSOLIDATE_MEMORY_EXAMPLE
    assert "## New conversation snapshots" in CONSOLIDATE_MEMORY_EXAMPLE
    assert "Updated memory\n=" not in CONSOLIDATE_MEMORY_EXAMPLE

    response_heading = "Example assistant response: decoded value"
    response_marker = f"{response_heading}\n{'=' * len(response_heading)}\n\n"
    response = CONSOLIDATE_MEMORY_EXAMPLE.split(response_marker, maxsplit=1)[1]
    assert response.startswith("## Standing instructions")

    assert (
        CONSOLIDATE_MEMORY_EXAMPLE.count("# Conversation Snapshot: orchestrator") == 2
    )
    for section in (
        "## Goals and intent",
        "## State of work",
        "## Decisions",
        "## Preferences and corrections",
        "## Open loose ends",
        "## Facts and references",
    ):
        assert CONSOLIDATE_MEMORY_EXAMPLE.count(section) >= 2


def test_snapshot_prompt_uses_previous_snapshot_only_for_grounding() -> None:
    assert "previous snapshot is context only" in SNAPSHOT_CONVERSATION_INSTRUCTIONS
    assert "must never be used to suppress, deduplicate, or omit information" in (
        SNAPSHOT_CONVERSATION_INSTRUCTIONS
    )
    assert "Every snapshot must faithfully represent the conversation segment" in (
        SNAPSHOT_CONVERSATION_INSTRUCTIONS
    )
    assert "Record only what is new in this segment" not in (
        SNAPSHOT_CONVERSATION_INSTRUCTIONS
    )
    assert "Read the input as a timeline" in SNAPSHOT_CONVERSATION_INSTRUCTIONS
    assert "When facts clash, later evidence wins" in (
        SNAPSHOT_CONVERSATION_INSTRUCTIONS
    )
    assert "Never copy an earlier state claim or loose end forward" in (
        SNAPSHOT_CONVERSATION_INSTRUCTIONS
    )
    assert "`terminal-completed`" in SNAPSHOT_CONVERSATION_INSTRUCTIONS
    assert "`terminal-failed`" in SNAPSHOT_CONVERSATION_INSTRUCTIONS


def test_snapshot_prompt_prioritizes_user_authored_messages() -> None:
    assert "User-authored messages are paramount evidence" in (
        SNAPSHOT_CONVERSATION_INSTRUCTIONS
    )
    assert "User answers to agent questions are especially important" in (
        SNAPSHOT_CONVERSATION_INSTRUCTIONS
    )
    assert "writes them to a file" in SNAPSHOT_CONVERSATION_INSTRUCTIONS


def test_snapshot_prompt_records_incident_evidence_without_emotional_noise() -> None:
    assert "preserve the evidence and action sequence" in (
        SNAPSHOT_CONVERSATION_INSTRUCTIONS
    )
    assert "Never promote an unsupported agent assertion into a fact" in (
        SNAPSHOT_CONVERSATION_INSTRUCTIONS
    )
    assert "paraphrase insults, profanity, and emotional intensity" in (
        SNAPSHOT_CONVERSATION_INSTRUCTIONS
    )
