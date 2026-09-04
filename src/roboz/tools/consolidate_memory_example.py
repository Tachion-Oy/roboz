"""Worked example for persistent memory consolidation."""

from typing import Final

CONSOLIDATE_MEMORY_EXAMPLE: Final[str] = """Example user message: conversation content
==========================================

## Previous memory

# Persistent Memory

## Standing instructions
- Never show an opaque ID without a useful description.
- Do not start with a greeting; get to the point.

## User profile and preferences
- Prefers lean implementations over speculative abstractions.

## Active work
- CLI permission matching is being implemented; glob behavior is in review.
- Librarian memory currently preserves every old thread by default.

## Open loose ends
- Revisit the blue status-icon idea someday.

## New conversation snapshots

### Snapshot: memory-policy-1

# Conversation Snapshot: orchestrator

## Goals and intent
- User said, "For this task, create `memory_policy.md` and only edit prompts." This is the current task's scope, not an instruction for future conversations.

## State of work
- A direct file check reported that `memory_policy.md` did not exist.
- No write had occurred by the end of the segment; creating the file and revising the prompts remained pending.

## Decisions
- Agent chose to inspect the existing consolidation prompts before editing them.

## Preferences and corrections
- No new enduring user preference or correction was expressed in this segment.

## Open loose ends
- Create `memory_policy.md` and update the consolidation prompts without changing runtime code.

## Facts and references
- An agent inferred that the user might prefer an exhaustive historical archive; no user-authored statement supported that inference.

### Snapshot: memory-policy-2

# Conversation Snapshot: orchestrator

## Goals and intent
- User made memory-consolidation prompt behavior the current task and said persistent memory should be a bounded working set, not an exhaustive archive.

## State of work
- The file-writing tool reported that `memory_policy.md` was created successfully.
- Prompt work now focuses on making new snapshots prominent, resolving mutable facts chronologically, and allowing non-standing memories to decay.

## Decisions
- User selected bounded, ranked retention so old context cannot crowd out current work.

## Preferences and corrections
- User said, "From now on, bare IDs are fine in developer logs, but user-facing references still need a description."
- User said, "Always avoid compatibility shims unless I explicitly ask for one."

## Open loose ends
- Validate the revised consolidation prompt against representative long memories.

## Facts and references
- `memory_policy.md` now exists; the later successful write supersedes the earlier missing-file observation.

Example assistant response: decoded value
=========================================

## Standing instructions
- In user-facing references, accompany opaque IDs with a useful description; bare IDs are acceptable in developer logs.
- Do not start with a greeting; get to the point.
- Avoid compatibility shims unless the user explicitly requests one.

## User profile and preferences
- Prefers lean implementations over speculative abstractions.

## Active work
- Memory consolidation prompts: make current snapshots prominent, resolve mutable facts chronologically, and let non-standing memories decay by ranked position.
- `memory_policy.md` now exists; the current task is limited to prompt changes.
- CLI permission matching: glob behavior remains in review.

## Decisions and rationale
- Persistent memory is a bounded working set rather than an exhaustive archive, preventing old context from crowding out current work.

## Open loose ends
- Validate the revised consolidation prompt against representative long memories.

## Lessons and warnings
- Snapshot order carries state: a later reliable observation replaces an earlier observation about the same mutable fact.

Explanation: not part of the assistant response
===============================================

The current-task command to create the file did not become a standing instruction. The \
later tool result replaced the earlier missing-file observation. The agent's archive \
inference did not override the user's direct preference. The dormant status-icon loose end \
was already terse and disappeared; the older CLI thread survived only as a lower-ranked, \
compressed bullet. None of those changes is recorded inside the updated memory as an \
eviction ledger."""

__all__ = ["CONSOLIDATE_MEMORY_EXAMPLE"]
