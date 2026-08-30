"""Reference output for the memory consolidation tool."""

CONSOLIDATE_MEMORY_EXAMPLE = """## Standing instructions
- Never refer to items in user-facing messages with only a code/ID etc. that is not informative; always show at least some description.
- Do not start with a greeting, get to the point immediately without wasting a message on ceremony.
- If the user rejects or doubts a selected external item, discard that candidate and re-query the source of truth before any further mutation.

## User profile and preferences
- Strongly prefers lean, inspectable, professional code; rejects speculative abstractions and compatibility shims.
- Tool names are imperative (`snapshot_conversations`); agent role names stay out of tool namespaces.
- Wants background tooling that "just works" without user involvement.

## Active work
- CLI permission rules: adding ask/allow/deny rule matching in `src/roboz/standard/tools/utils.py`; glob semantics agreed, implementation in review.
- Librarian pipeline: conversation logs -> per-conversation snapshots -> one persistent memory file; stable, untouched this update.
- Orchestrator consumes the memory file through `Agent` `initial_messages` pointing at the memory folder.
- Brand strategy credibility draft: earlier snapshot reported the folder empty; a newer snapshot recorded a successful write. Current state: `05-credibility/credibility.md` exists.

## Decisions and rationale
- All durable files are timestamp-named markdown; coverage is tracked by filename timestamp, not metadata or state files.
- Each tool replaces its previous output file instead of appending, so `purge_files` is only a safety net.
- Relative permission patterns require an absolute `base_path`; absolute patterns ignore it (avoids silent mismatches).

## Open loose ends
- Long-running conversations: user asked what happens when one conversation runs for days; age-based flush sketched, user said revisit later.
- Consolidation trigger thresholds were left as placeholders pending user review.

## Lessons and warnings
- A single long-running conversation only ever contributes one pending snapshot; the age-based flush exists for that case.
- Never snapshot the librarian itself; it only processes agents in its allow-list.

## Recent changes
- Added the CLI permission rules thread from new snapshots; librarian threads kept unchanged since snapshots did not touch them.
- Replaced the obsolete "credibility folder is empty" state after a newer snapshot established the successful file write.
- Compressed the finished snapshot-folder-layout discussion into the timestamp-naming decision bullet; evicted the resolved note about markdown lint settings.
"""

__all__ = ["CONSOLIDATE_MEMORY_EXAMPLE"]
