"""Condense conversation snapshots into bounded persistent memory."""

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from roboshed.identifiers import CONSOLIDATE_MEMORY_TOOL_NAME
from roboshed.tools._snapshot_metadata import parse_snapshot_document
from roboshed.tools.compactification import (
    summarize_conversation_segment,
)
from roboshed.tools.consolidate_memory_prompts import (
    CONSOLIDATE_MEMORY_INSTRUCTIONS,
    CONSOLIDATE_MEMORY_SYSTEM_PROMPT,
)
from roboshed.tools.librarian_errors import LibrarianProviderRequestFailure
from roboshed.tools.memory_files import (
    MARKDOWN_SUFFIX,
    UTF8_ENCODING,
    latest_timestamped_file,
    parse_timestamp_stem,
    relative_or_absolute,
    utc_now,
    write_timestamped_file,
)
from roboz.exceptions import ExternalCallCancelledError, LLMProviderRequestError
from roboz.models import NO_MESSAGE, All, Message, Str
from roboz.runtime import log_with_data
from roboz.runtime.persistence import active_marker_paths
from roboshed.tools.contexts import ConsolidateMemoryContext
from roboz.tooling.decorators import factory

logger = logging.getLogger(__name__)

type TimestampedFile = tuple[datetime, Path]

PROVENANCE_MARKER: Final[str] = "<!-- librarian:provenance v1 -->"
PERSISTENT_MEMORY_TITLE: Final[str] = "# Persistent Memory"

_SNAPSHOT_TITLE_PREFIX: Final[str] = "# Conversation Snapshot: "
_TOOL_KEY: Final[str] = "tool"
_PENDING_SNAPSHOTS_KEY: Final[str] = "pending_snapshots"
_ARTIFACT_FILE_KEY: Final[str] = "artifact_file"
_MIN_PENDING_SNAPSHOTS: Final[int] = 1


@dataclass(frozen=True)
class SnapshotDescriptor:
    """Prompt-independent provenance extracted from a snapshot artifact."""

    path: str
    conversation_id: str
    agent_name: str | None = None


def _pending_snapshots(
    snapshot_root: Path, watermark: datetime | None
) -> list[TimestampedFile]:
    """Return snapshots newer than the memory watermark, oldest first."""
    return sorted(
        (stamp, path)
        for path in snapshot_root.rglob(f"*{MARKDOWN_SUFFIX}")
        if (stamp := parse_timestamp_stem(path)) is not None
        and (watermark is None or stamp > watermark)
    )


def _should_consolidate(
    *,
    pending: list[TimestampedFile],
    min_pending: int,
    max_age_seconds: float,
    now: datetime,
    project_idle: bool,
) -> bool:
    if not pending:
        return False
    if project_idle or len(pending) >= min_pending:
        return True
    oldest, _ = pending[0]
    return (now - oldest).total_seconds() >= max_age_seconds


def _snapshot_text(snapshot_root: Path, pending: list[TimestampedFile]) -> str:
    return "\n\n".join(
        f"### Snapshot: {path.parent.relative_to(snapshot_root).as_posix()}\n\n"
        f"{parse_snapshot_document(path.read_text(encoding=UTF8_ENCODING)).content}"
        for _, path in pending
    )


def _summary_input(
    *,
    snapshot_root: Path,
    previous_memory: str | None,
    pending: list[TimestampedFile],
) -> str:
    snapshots = "## New conversation snapshots\n\n" + _snapshot_text(
        snapshot_root, pending
    )
    if previous_memory is None:
        return snapshots
    return f"## Previous memory\n\n{previous_memory.strip()}\n\n{snapshots}"


def strip_provenance(text: str) -> str:
    """Remove machine-owned provenance before sending memory back to the model."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == PROVENANCE_MARKER:
            return "\n".join(lines[:index]).rstrip()
    return text.rstrip()


def strip_leading_memory_title(text: str) -> str:
    """Remove a model-supplied title because the file writer owns that heading."""
    lines = text.strip().splitlines()
    if lines and lines[0].strip() == PERSISTENT_MEMORY_TITLE:
        lines = lines[1:]
    return "\n".join(lines).lstrip()


def _snapshot_descriptor(
    snapshot_root: Path, snapshot_path: Path
) -> SnapshotDescriptor:
    agent_name: str | None = None
    try:
        first_line = snapshot_path.read_text(encoding=UTF8_ENCODING).splitlines()[0]
    except (OSError, IndexError):
        first_line = ""
    if first_line.startswith(_SNAPSHOT_TITLE_PREFIX):
        agent_name = first_line.removeprefix(_SNAPSHOT_TITLE_PREFIX).strip() or None
    return SnapshotDescriptor(
        path=relative_or_absolute(snapshot_path, base=snapshot_root.parent),
        conversation_id=snapshot_path.parent.name,
        agent_name=agent_name,
    )


def build_provenance_block(
    *,
    snapshot_root: Path,
    memory_root: Path,
    pending: list[TimestampedFile],
    previous_memory_path: Path | None,
) -> str:
    """Build the machine-owned source trail appended to persistent memory."""
    lines = [PROVENANCE_MARKER, "## Sources"]
    for _, path in pending:
        descriptor = _snapshot_descriptor(snapshot_root, path)
        if descriptor.agent_name:
            lines.append(
                f"- `{descriptor.path}` - {descriptor.agent_name}, "
                f"conversation `{descriptor.conversation_id}`"
            )
        else:
            lines.append(
                f"- `{descriptor.path}` - conversation `{descriptor.conversation_id}`"
            )
    if previous_memory_path is not None:
        previous_path = relative_or_absolute(
            previous_memory_path, base=memory_root.parent
        )
        lines.append(f"Previous memory: `{previous_path}`")
    return "\n".join(lines)


def _not_consolidated_result(pending_count: int, *, superseded: bool = False) -> Str:
    state = ", superseded" if superseded else ""
    return Str(
        value=(
            f"{CONSOLIDATE_MEMORY_TOOL_NAME}: pending={pending_count}{state}, "
            "consolidated=0"
        ),
        truncation=NO_MESSAGE,
    )


@factory
def consolidate_memory(
    input: All, messages: list[Message], ctx: ConsolidateMemoryContext
) -> Str:
    """Fold eligible snapshots into a new append-only persistent memory file."""
    del input, messages
    if ctx.pipe is not None:
        ctx.pipe.raise_if_cancelled()
    watermark, memory_location = latest_timestamped_file(
        ctx.memory_root, suffix=MARKDOWN_SUFFIX
    )
    pending = _pending_snapshots(ctx.snapshot_root, watermark)
    project_idle = not active_marker_paths(ctx.conversation_root, ctx.agent_names)
    if not _should_consolidate(
        pending=pending,
        min_pending=max(_MIN_PENDING_SNAPSHOTS, ctx.min_pending_snapshots),
        max_age_seconds=ctx.max_pending_age_seconds,
        now=utc_now(),
        project_idle=project_idle,
    ):
        return _not_consolidated_result(len(pending))

    try:
        summary = summarize_conversation_segment(
            endpoint=ctx.endpoint,
            system_prompt=CONSOLIDATE_MEMORY_SYSTEM_PROMPT,
            instructions=CONSOLIDATE_MEMORY_INSTRUCTIONS,
            max_chars=ctx.max_chars,
            max_chars_tolerance_percent=ctx.max_chars_tolerance_percent,
            conversation=_summary_input(
                snapshot_root=ctx.snapshot_root,
                previous_memory=(
                    strip_provenance(memory_location.read_text(encoding=UTF8_ENCODING))
                    if memory_location is not None
                    else None
                ),
                pending=pending,
            ),
            timeout_s=ctx.timeout_s,
            pipe=ctx.pipe,
        )
    except ExternalCallCancelledError:
        raise
    except LLMProviderRequestError as error:
        raise LibrarianProviderRequestFailure(
            "Librarian memory consolidation request failed"
        ) from error

    if ctx.pipe is not None:
        ctx.pipe.raise_if_cancelled()
    current_watermark, _ = latest_timestamped_file(
        ctx.memory_root, suffix=MARKDOWN_SUFFIX
    )
    if current_watermark is not None and (
        watermark is None or current_watermark > watermark
    ):
        return _not_consolidated_result(len(pending), superseded=True)

    provenance = build_provenance_block(
        snapshot_root=ctx.snapshot_root,
        memory_root=ctx.memory_root,
        pending=pending,
        previous_memory_path=memory_location,
    )
    memory_path = write_timestamped_file(
        ctx.memory_root,
        (
            f"{PERSISTENT_MEMORY_TITLE}\n\n"
            f"{strip_leading_memory_title(summary)}\n\n"
            f"{provenance}\n"
        ),
        suffix=MARKDOWN_SUFFIX,
        replace=None,
        pipe=ctx.pipe,
    )
    event_data = {
        _TOOL_KEY: CONSOLIDATE_MEMORY_TOOL_NAME,
        _PENDING_SNAPSHOTS_KEY: len(pending),
        _ARTIFACT_FILE_KEY: memory_path.name,
    }
    log_with_data(
        logger,
        logging.INFO,
        (
            "Librarian memory consolidated "
            f"(pending={len(pending)}, artifact={memory_path.name})"
        ),
        data=event_data,
    )
    return Str(
        value=(
            f"{CONSOLIDATE_MEMORY_TOOL_NAME}: pending={len(pending)}, consolidated=1"
        ),
        truncation=NO_MESSAGE,
    )


__all__ = [
    "ConsolidateMemoryContext",
    "PERSISTENT_MEMORY_TITLE",
    "PROVENANCE_MARKER",
    "SnapshotDescriptor",
    "build_provenance_block",
    "consolidate_memory",
    "strip_leading_memory_title",
    "strip_provenance",
]
