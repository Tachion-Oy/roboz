"""Condense conversation snapshots into one persistent memory file."""

import logging
from datetime import datetime
from pathlib import Path

from roboz.exceptions import ExternalCallCancelledError, LLMProviderRequestError
from roboz.models import All, Message, Str
from roboz.runtime.persistence import active_marker_paths
from roboz.models import NO_MESSAGE
from roboz.agent import get_active_agent_stack
from roboz import factory
from roboz.llm import endpoint_resource

from roboz.standard.identifiers import CONSOLIDATE_MEMORY_TOOL_NAME

from roboz.standard.tools.agent_runtime.conversation_logs import utc_now
from ..artifacts import (
    latest_timestamped_file,
    parse_timestamp_stem,
    relative_or_absolute,
    write_timestamped_file,
)
from ..errors import LibrarianProviderRequestFailure
from ..summarize import summarize_conversation_segment
from .prompts import (
    CONSOLIDATE_MEMORY_INSTRUCTIONS,
    CONSOLIDATE_MEMORY_SYSTEM_PROMPT,
)
from .types import ConsolidateMemoryCtx

logger = logging.getLogger(__name__)
PROVENANCE_MARKER = "<!-- librarian:provenance v1 -->"


def _pending_snapshots(
    snapshot_root: Path, watermark: datetime | None
) -> list[tuple[datetime, Path]]:
    """Snapshots newer than the latest memory file, oldest first."""
    return sorted(
        (stamp, path)
        for path in snapshot_root.rglob("*.md")
        if (stamp := parse_timestamp_stem(path)) is not None
        and (watermark is None or stamp > watermark)
    )


def _should_consolidate(
    *,
    pending: list[tuple[datetime, Path]],
    min_pending: int,
    max_age_seconds: float,
    now: datetime,
    project_idle: bool,
) -> bool:
    if not pending:
        return False
    if project_idle:
        return True
    if len(pending) >= min_pending:
        return True
    oldest, _ = pending[0]
    return (now - oldest).total_seconds() >= max_age_seconds


def _snapshot_text(snapshot_root: Path, pending: list[tuple[datetime, Path]]) -> str:
    return "\n\n".join(
        f"### Snapshot {index} (recorded_at={stamp.isoformat()}): "
        f"{path.parent.relative_to(snapshot_root).as_posix()}\n\n"
        f"{path.read_text().strip()}"
        for index, (stamp, path) in enumerate(pending, start=1)
    )


def _summary_input(
    *,
    snapshot_root: Path,
    previous_memory: str | None,
    pending: list[tuple[datetime, Path]],
) -> str:
    snapshots = (
        "## New conversation snapshots\n\n"
        "Order: oldest to newest.\n\n" + _snapshot_text(snapshot_root, pending)
    )
    if previous_memory is None:
        return snapshots
    return f"## Previous memory\n\n{previous_memory.strip()}\n\n{snapshots}"


def strip_provenance(text: str) -> str:
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if line.strip() == PROVENANCE_MARKER:
            return "\n".join(lines[:idx]).rstrip()
    return text.rstrip()


def build_provenance_block(
    *,
    snapshot_root: Path,
    memory_root: Path,
    pending: list[tuple[datetime, Path]],
    previous_memory_path: Path | None,
) -> str:
    lines = [PROVENANCE_MARKER, "## Sources"]
    for _, path in pending:
        descriptor = _snapshot_descriptor(snapshot_root, path)
        snapshot_path = descriptor["path"]
        conversation_id = descriptor["conversation_id"]
        agent_name = descriptor.get("agent_name")
        if isinstance(agent_name, str) and agent_name:
            lines.append(
                f"- `{snapshot_path}` - {agent_name}, conversation `{conversation_id}`"
            )
        else:
            lines.append(f"- `{snapshot_path}` - conversation `{conversation_id}`")
    if previous_memory_path is not None:
        previous_path = relative_or_absolute(
            previous_memory_path, base=memory_root.parent
        )
        lines.append(f"Previous memory: `{previous_path}`")
    return "\n".join(lines)


def _snapshot_descriptor(snapshot_root: Path, snapshot_path: Path) -> dict[str, str]:
    descriptor: dict[str, str] = {
        "path": relative_or_absolute(snapshot_path, base=snapshot_root.parent),
        "conversation_id": snapshot_path.parent.name,
    }
    try:
        first_line = snapshot_path.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError):
        return descriptor
    prefix = "# Conversation Snapshot: "
    if first_line.startswith(prefix):
        descriptor["agent_name"] = first_line.removeprefix(prefix).strip()
    return descriptor


@factory
def consolidate_memory(
    input: All, messages: list[Message], ctx: ConsolidateMemoryCtx
) -> Str:
    if ctx.pipe is not None:
        ctx.pipe.raise_if_cancelled()
    snapshot_root = ctx.snapshot_root
    memory_root = ctx.memory_root
    watermark, memory_location = latest_timestamped_file(memory_root, suffix=".md")
    pending = _pending_snapshots(snapshot_root, watermark)
    project_idle = not active_marker_paths(ctx.conversation_root, ctx.agent_names)

    if not _should_consolidate(
        pending=pending,
        min_pending=max(1, ctx.min_pending_snapshots),
        max_age_seconds=ctx.max_pending_age_seconds,
        now=utc_now(),
        project_idle=project_idle,
    ):
        return Str(
            value=f"consolidate_memory: pending={len(pending)}, consolidated=0",
            truncation=NO_MESSAGE,
        )

    agent_stack = get_active_agent_stack()
    logger.info(
        "Calling llm (agent=%s, tool=%s)",
        agent_stack[-1] if agent_stack else None,
        CONSOLIDATE_MEMORY_TOOL_NAME,
    )
    try:
        summary = summarize_conversation_segment(
            endpoint=endpoint_resource(ctx.endpoint),
            system_prompt=CONSOLIDATE_MEMORY_SYSTEM_PROMPT,
            instructions=CONSOLIDATE_MEMORY_INSTRUCTIONS,
            max_chars=ctx.max_chars,
            conversation=_summary_input(
                snapshot_root=snapshot_root,
                previous_memory=strip_provenance(memory_location.read_text())
                if memory_location
                else None,
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
    # Another librarian may have consolidated this same batch while we were
    # summarizing (the slow LLM call above). Re-read the watermark and bail if it
    # advanced — whoever wrote first wins, so we don't add a redundant twin.
    current_watermark, _ = latest_timestamped_file(memory_root, suffix=".md")
    if current_watermark is not None and (
        watermark is None or current_watermark > watermark
    ):
        return Str(
            value=f"consolidate_memory: pending={len(pending)}, superseded, consolidated=0",
            truncation=NO_MESSAGE,
        )

    if ctx.pipe is not None:
        ctx.pipe.raise_if_cancelled()
    memory_path = write_timestamped_file(
        memory_root,
        (
            f"# Persistent Memory\n\n{summary.strip()}\n\n"
            f"{
                build_provenance_block(
                    snapshot_root=snapshot_root,
                    memory_root=memory_root,
                    pending=pending,
                    previous_memory_path=memory_location,
                )
            }\n"
        ),
        suffix=".md",
        replace=None,
        pipe=ctx.pipe,
        timestamp=pending[-1][0],
    )
    event_data = {
        "tool": CONSOLIDATE_MEMORY_TOOL_NAME,
        "pending_snapshots": len(pending),
        "artifact_file": memory_path.name,
    }
    logger.info("Librarian memory consolidated (data=%s)", event_data)
    return Str(
        value=f"consolidate_memory: pending={len(pending)}, consolidated=1",
        truncation=NO_MESSAGE,
    )


__all__ = ["consolidate_memory"]
