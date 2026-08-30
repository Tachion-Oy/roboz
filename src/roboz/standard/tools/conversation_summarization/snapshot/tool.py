"""Create markdown summary snapshots for eligible conversation logs."""

import logging
from datetime import datetime

from roboz.exceptions import ExternalCallCancelledError, LLMProviderRequestError
from roboz.models import All, Message, Str
from roboz.runtime.persistence.schema import (
    ConversationRun,
    LoggedMessageRow,
    RunStatus,
    logged_row_to_message,
)
from roboz.llm import get_truncated_messages_for_context
from roboz.models import NO_MESSAGE
from roboz.agent import get_active_agent_stack
from roboz import factory
from roboz.llm import endpoint_resource
from roboz.llm import estimate_conversation_tokens

from roboz.standard.identifiers import SNAPSHOT_CONVERSATIONS_TOOL_NAME

from roboz.standard.tools.agent_runtime.conversation_logs import load_conversation_run
from ..artifacts import latest_timestamped_file, write_timestamped_file
from ..errors import LibrarianProviderRequestFailure
from ..summarize import summarize_conversation_segment
from .normalize import normalize_messages_for_snapshot
from .prompts import (
    SNAPSHOT_CONVERSATION_INSTRUCTIONS,
    SNAPSHOT_CONVERSATION_SYSTEM_PROMPT,
)
from .types import SnapshotConversationsCtx

logger = logging.getLogger(__name__)
TERMINAL_SYNCABLE_STATUSES = {RunStatus.COMPLETED, RunStatus.FAILED}


def _uncovered_rows(
    rows: list[LoggedMessageRow], latest_snapshot_time: datetime | None
) -> list[LoggedMessageRow]:
    """Persisted rows newer than the latest snapshot, in sequence order.

    Keeping rows intact until after snapshot normalization preserves the sequence and
    timestamp metadata needed to make temporal precedence explicit to the recorder.
    """
    if latest_snapshot_time is None:
        return rows
    for index, row in enumerate(rows):
        try:
            created_at = datetime.fromisoformat(row.created_at.replace("Z", "+00:00"))
        except ValueError:
            return rows[index:]
        if created_at > latest_snapshot_time:
            return rows[index:]
    return []


def _normalize_temporal_messages(
    rows: list[LoggedMessageRow], *, agent_name: str, agent_description: str | None
) -> list[Message]:
    """Normalize rows and retain their chronological identity in prompt-visible text."""
    normalized: list[Message] = []
    for row in rows:
        messages = normalize_messages_for_snapshot(
            [logged_row_to_message(row)],
            agent_name=agent_name,
            agent_description=agent_description,
        )
        for message in messages:
            normalized.append(
                message.model_copy(
                    update={
                        "content": (
                            f"[sequence={row.sequence} created_at={row.created_at}]\n"
                            f"{message.content}"
                        )
                    }
                )
            )
    return normalized


def _conversation_text(messages: list[Message]) -> str:
    return "\n\n".join(f"## {m.role.value.upper()}\n{m.content}" for m in messages)


def _snapshot_mode(status: RunStatus) -> str:
    if status is RunStatus.COMPLETED:
        return "terminal-completed"
    if status is RunStatus.FAILED:
        return "terminal-failed"
    return "incremental"


def _summary_input(
    *,
    run: ConversationRun,
    previous_snapshot: str | None,
    previous_snapshot_time: datetime | None,
    uncovered: list[Message],
) -> str:
    ended_at = run.ended_at or "not ended"
    source = (
        "## Source conversation\n\n"
        f"- Agent: {run.agent_name}\n"
        f"- Conversation: {run.conversation_id}\n"
        f"- Run status: {run.status.value}\n"
        f"- Snapshot mode: {_snapshot_mode(run.status)}\n"
        f"- Run started at: {run.started_at}\n"
        f"- Run ended at: {ended_at}\n"
        "- Message ordering: ascending sequence; created_at is UTC context.\n\n"
    )
    if previous_snapshot is None:
        return (
            source + "## New conversation segment\n\n" + _conversation_text(uncovered)
        )
    recorded_at = (
        previous_snapshot_time.isoformat()
        if previous_snapshot_time is not None
        else "unknown"
    )
    return (
        source + f"## Previous snapshot (recorded_at={recorded_at})\n\n"
        f"{previous_snapshot.strip()}\n\n"
        "## New conversation segment\n\n"
        f"{_conversation_text(uncovered)}"
    )


def _should_snapshot(*, run: ConversationRun, threshold: int, new_tokens: int) -> bool:
    if run.status is RunStatus.CANCELLED:
        return False
    return new_tokens >= threshold or (
        run.status in TERMINAL_SYNCABLE_STATUSES and new_tokens > 0
    )


@factory
def snapshot_conversations(
    input: All, messages: list[Message], ctx: SnapshotConversationsCtx
) -> Str:
    if ctx.pipe is not None:
        ctx.pipe.raise_if_cancelled()
    conversation_root = ctx.conversation_root
    snapshot_root = ctx.snapshot_root
    threshold = max(0, ctx.token_growth_threshold)
    agent_names = ctx.agent_names
    source_files = list(conversation_root.rglob("*.json"))

    scanned = len(source_files)
    parsed = 0
    created = 0
    skipped_invalid = 0
    skipped_agent = 0

    for file in source_files:
        if ctx.pipe is not None:
            ctx.pipe.raise_if_cancelled()
        try:
            if file.resolve().is_relative_to(snapshot_root.resolve()):
                continue
        except OSError:
            continue

        run = load_conversation_run(file)
        if run is None:
            skipped_invalid += 1
            continue
        parsed += 1

        if run.agent_name not in agent_names:
            skipped_agent += 1
            continue

        snapshot_time, snapshot_location = latest_timestamped_file(
            snapshot_root / run.conversation_id, suffix=".md"
        )
        uncovered = _uncovered_rows(list(run.messages), snapshot_time)
        normalized = _normalize_temporal_messages(
            uncovered,
            agent_name=run.agent_name,
            agent_description=run.metadata.agent_description,
        )
        seen = get_truncated_messages_for_context(normalized)
        new_tokens = estimate_conversation_tokens(seen)
        if not _should_snapshot(run=run, threshold=threshold, new_tokens=new_tokens):
            continue

        agent_stack = get_active_agent_stack()
        logger.info(
            "Calling llm (agent=%s, tool=%s)",
            agent_stack[-1] if agent_stack else None,
            SNAPSHOT_CONVERSATIONS_TOOL_NAME,
        )
        try:
            summary = summarize_conversation_segment(
                endpoint=endpoint_resource(ctx.endpoint),
                system_prompt=SNAPSHOT_CONVERSATION_SYSTEM_PROMPT,
                instructions=SNAPSHOT_CONVERSATION_INSTRUCTIONS,
                max_chars=ctx.max_chars,
                conversation=_summary_input(
                    run=run,
                    previous_snapshot=snapshot_location.read_text()
                    if snapshot_location
                    else None,
                    previous_snapshot_time=snapshot_time,
                    uncovered=seen,
                ),
                timeout_s=ctx.timeout_s,
                pipe=ctx.pipe,
            )
        except ExternalCallCancelledError:
            raise
        except LLMProviderRequestError as error:
            raise LibrarianProviderRequestFailure(
                "Librarian conversation snapshot request failed"
            ) from error
        if ctx.pipe is not None:
            ctx.pipe.raise_if_cancelled()
        # Another librarian may have snapshotted this same conversation segment
        # while we were summarizing. Re-read this conversation's latest snapshot
        # and skip if it advanced past the point we started from.
        current_time, _ = latest_timestamped_file(
            snapshot_root / run.conversation_id, suffix=".md"
        )
        if current_time is not None and (
            snapshot_time is None or current_time > snapshot_time
        ):
            continue

        if ctx.pipe is not None:
            ctx.pipe.raise_if_cancelled()
        snapshot_path = write_timestamped_file(
            snapshot_root / run.conversation_id,
            f"# Conversation Snapshot: {run.agent_name}\n\n{summary.strip()}\n",
            suffix=".md",
            replace=None,
            pipe=ctx.pipe,
        )
        event_data = {
            "tool": SNAPSHOT_CONVERSATIONS_TOOL_NAME,
            "source_agent": run.agent_name,
            "conversation_id": run.conversation_id,
            "artifact_file": snapshot_path.name,
            "new_tokens": new_tokens,
        }
        logger.info("Librarian snapshot created (data=%s)", event_data)
        created += 1

    return Str(
        value=(
            "snapshot_conversations: "
            f"scanned={scanned}, parsed={parsed}, created={created}, "
            f"skipped_invalid={skipped_invalid}, skipped_agent={skipped_agent}"
        ),
        truncation=NO_MESSAGE,
    )


__all__ = ["snapshot_conversations"]
