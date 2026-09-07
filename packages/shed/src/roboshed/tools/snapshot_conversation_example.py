"""Reference example output for conversation snapshots."""

from typing import Final

SNAPSHOT_CONVERSATION_EXAMPLE: Final[str] = """## Goals and intent
- User wants a non-agentic Librarian that runs in the background and maintains conversation memory without user involvement.
- User stated the snapshots exist to feed a later consolidation step, "the initial raw phase of information gathering".

## State of work
- `snapshot_conversations` implemented: scans one conversation root, filters by allowed agent names, writes one markdown snapshot per `conversation_id`.
- Existing snapshots are read as the prior record, merged with new transcript content, then replaced by the updated snapshot.
- `projects/brandstrategy/05-credibility/` was observed empty earlier; a later successful `apply_patch` created `credibility.md`. Current state: the file exists with the approved draft.
- Summarization wired through `summarize_conversation_segment` in `packages/shed/src/roboshed/tools/compactification/summarize.py`; segment ended with unit tests passing.
- In progress at segment end: agent was drafting the consolidation trigger thresholds; not yet reviewed by the user.

## Decisions
- User decided snapshots are plain markdown with coverage tracked by filename timestamp, rejecting metadata and state files as "extra machinery".
- Agent proposed a manifest index; user rejected it, preferring the filesystem as the source of truth.
- User decided a completed run is always summarized once if any uncovered messages remain.

## Preferences and corrections
- User: "I want lean, inspectable, professional code" - rejected speculative abstractions and compatibility shims when the agent offered them.
- User corrected tool naming twice: tool names must be action-oriented (`snapshot_conversations`), and agent role names must not leak into tool namespaces.
- User pushed back on the agent writing tests before review.

## Open loose ends
- User asked "what happens when one conversation runs for days?" - agent suggested an age-based flush; user said to revisit later.
- Threshold values for consolidation were left as placeholders pending user review.
- Credibility drafting is not listed here because the later successful write resolved it.

## Facts and references
- Librarian must never snapshot itself; it only processes agents in its allow-list.
- Key files: `packages/shed/src/roboshed/agents/librarian.py`, `packages/shed/src/roboshed/tools/snapshot_conversations.py`, `packages/shed/src/roboshed/tools/memory_files.py`.
- Prompt examples live in separate modules from prompt constants.
- User asked to remember: orchestrator consumes memory through `Agent` `initial_messages` pointing at the memory folder.
"""

__all__ = ["SNAPSHOT_CONVERSATION_EXAMPLE"]
