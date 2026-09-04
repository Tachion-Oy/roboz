"""Create markdown summary snapshots for eligible persisted conversations."""

import logging
from datetime import datetime
from enum import StrEnum
from typing import Final

from roboz.exceptions import ExternalCallCancelledError, LLMProviderRequestError
from roboz.llm import (
    endpoint_resource,
    estimate_conversation_tokens,
    get_truncated_messages_for_context,
)
from roboz.models import All, Message, NO_MESSAGE, Str
from roboz.runtime import log_with_data
from roboz.runtime.persistence import (
    ConversationRun,
    LoggedMessageRow,
    RunStatus,
    logged_row_to_message,
)
from roboz.tooling.decorators import factory
from roboz.tools._identifiers import SNAPSHOT_CONVERSATIONS_TOOL_NAME
from roboz.tools.compactification import summarize_conversation_segment
from roboz.tools.librarian_errors import LibrarianProviderRequestFailure
from roboz.tools.memory_contexts import SnapshotConversationsCtx
from roboz.tools.memory_files import (
    JSON_SUFFIX,
    MARKDOWN_SUFFIX,
    UTF8_ENCODING,
    latest_timestamped_file,
    load_conversation_run,
    write_timestamped_file,
)
from roboz.tools.snapshot_conversation_prompts import (
    SNAPSHOT_CONVERSATION_INSTRUCTIONS,
    SNAPSHOT_CONVERSATION_SYSTEM_PROMPT,
)
from roboz.tools.snapshot_normalize import normalize_messages_for_snapshot

logger = logging.getLogger(__name__)

TERMINAL_SYNCABLE_STATUSES: Final[frozenset[RunStatus]] = frozenset(
    {RunStatus.COMPLETED, RunStatus.FAILED}
)

_ISO_Z_SUFFIX: Final[str] = "Z"
_ISO_UTC_OFFSET: Final[str] = "+00:00"
_NOT_ENDED: Final[str] = "not ended"
_UNKNOWN_TIME: Final[str] = "unknown"
_SOURCE_AGENT_KEY: Final[str] = "source_agent"
_CONVERSATION_ID_KEY: Final[str] = "conversation_id"
_ARTIFACT_FILE_KEY: Final[str] = "artifact_file"
_NEW_TOKENS_KEY: Final[str] = "new_tokens"
_TOOL_KEY: Final[str] = "tool"
_MIN_TOKEN_THRESHOLD: Final[int] = 0


class SnapshotMode(StrEnum):
    """Prompt-visible mode describing how thoroughly prior state is reconciled."""

    INCREMENTAL = "incremental"
    TERMINAL_COMPLETED = "terminal-completed"
    TERMINAL_FAILED = "terminal-failed"


def _uncovered_rows(
    rows: list[LoggedMessageRow], latest_snapshot_time: datetime | None
) -> list[LoggedMessageRow]:
    """Return persisted rows newer than the latest snapshot, in sequence order."""
    if latest_snapshot_time is None:
        return rows
    for index, row in enumerate(rows):
        try:
            created_at = datetime.fromisoformat(
                row.created_at.replace(_ISO_Z_SUFFIX, _ISO_UTC_OFFSET)
            )
        except ValueError:
            return rows[index:]
        if created_at > latest_snapshot_time:
            return rows[index:]
    return []


def _normalize_temporal_messages(
    rows: list[LoggedMessageRow], *, agent_name: str, agent_description: str | None
) -> list[Message]:
    """Normalize rows while retaining chronological identity in visible text."""
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
    return "\n\n".join(
        f"## {message.role.value.upper()}\n{message.content}" for message in messages
    )


def _snapshot_mode(status: RunStatus) -> SnapshotMode:
    if status is RunStatus.COMPLETED:
        return SnapshotMode.TERMINAL_COMPLETED
    if status is RunStatus.FAILED:
        return SnapshotMode.TERMINAL_FAILED
    return SnapshotMode.INCREMENTAL


def _summary_input(
    *,
    run: ConversationRun,
    previous_snapshot: str | None,
    previous_snapshot_time: datetime | None,
    uncovered: list[Message],
) -> str:
    ended_at = run.ended_at or _NOT_ENDED
    source = (
        "## Source conversation\n\n"
        f"- Agent: {run.agent_name}\n"
        f"- Conversation: {run.conversation_id}\n"
        f"- Run status: {run.status.value}\n"
        f"- Snapshot mode: {_snapshot_mode(run.status).value}\n"
        f"- Run started at: {run.started_at}\n"
        f"- Run ended at: {ended_at}\n"
        "- Message ordering: ascending sequence; created_at is UTC context.\n\n"
    )
    if previous_snapshot is None:
        return source + "## New conversation segment\n\n" + _conversation_text(uncovered)
    recorded_at = (
        previous_snapshot_time.isoformat()
        if previous_snapshot_time is not None
        else _UNKNOWN_TIME
    )
    return (
        source
        + f"## Previous snapshot (recorded_at={recorded_at})\n\n"
        f"{previous_snapshot.strip()}\n\n"
        "## New conversation segment\n\n"
        f"{_conversation_text(uncovered)}"
    )


def _should_snapshot(*, run: ConversationRun, threshold: int, new_tokens: int) -> bool:
    if run.status is RunStatus.CANCELLED:
        return False
    return new_tokens >= threshold or (
        run.status in TERMINAL_SYNCABLE_STATUSES
        and new_tokens > _MIN_TOKEN_THRESHOLD
    )


def _snapshot_one_run(
    *,
    run: ConversationRun,
    ctx: SnapshotConversationsCtx,
    threshold: int,
) -> bool:
    snapshot_folder = ctx.snapshot_root / run.conversation_id
    snapshot_time, snapshot_location = latest_timestamped_file(
        snapshot_folder, suffix=MARKDOWN_SUFFIX
    )
    uncovered_rows = _uncovered_rows(list(run.messages), snapshot_time)
    normalized = _normalize_temporal_messages(
        uncovered_rows,
        agent_name=run.agent_name,
        agent_description=run.metadata.agent_description,
    )
    visible_messages = get_truncated_messages_for_context(normalized)
    new_tokens = estimate_conversation_tokens(visible_messages)
    if not _should_snapshot(run=run, threshold=threshold, new_tokens=new_tokens):
        return False

    try:
        summary = summarize_conversation_segment(
            endpoint=endpoint_resource(ctx.endpoint),
            system_prompt=SNAPSHOT_CONVERSATION_SYSTEM_PROMPT,
            instructions=SNAPSHOT_CONVERSATION_INSTRUCTIONS,
            max_chars=ctx.max_chars,
            max_chars_tolerance_percent=ctx.max_chars_tolerance_percent,
            conversation=_summary_input(
                run=run,
                previous_snapshot=(
                    snapshot_location.read_text(encoding=UTF8_ENCODING)
                    if snapshot_location is not None
                    else None
                ),
                previous_snapshot_time=snapshot_time,
                uncovered=visible_messages,
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

    current_time, _ = latest_timestamped_file(
        snapshot_folder, suffix=MARKDOWN_SUFFIX
    )
    if current_time is not None and (
        snapshot_time is None or current_time > snapshot_time
    ):
        return False

    snapshot_path = write_timestamped_file(
        snapshot_folder,
        f"# Conversation Snapshot: {run.agent_name}\n\n{summary.strip()}\n",
        suffix=MARKDOWN_SUFFIX,
        replace=None,
        pipe=ctx.pipe,
    )
    event_data = {
        _TOOL_KEY: SNAPSHOT_CONVERSATIONS_TOOL_NAME,
        _SOURCE_AGENT_KEY: run.agent_name,
        _CONVERSATION_ID_KEY: run.conversation_id,
        _ARTIFACT_FILE_KEY: snapshot_path.name,
        _NEW_TOKENS_KEY: new_tokens,
    }
    log_with_data(
        logger,
        logging.INFO,
        (
            "Librarian snapshot created "
            f"(agent={run.agent_name}, new_tokens={new_tokens})"
        ),
        data=event_data,
    )
    return True


@factory
def snapshot_conversations(
    input: All, messages: list[Message], ctx: SnapshotConversationsCtx
) -> Str:
    """Create append-only snapshots for eligible persisted conversation runs."""
    del input, messages
    if ctx.pipe is not None:
        ctx.pipe.raise_if_cancelled()
    threshold = max(_MIN_TOKEN_THRESHOLD, ctx.token_growth_threshold)
    source_files = list(ctx.conversation_root.rglob(f"*{JSON_SUFFIX}"))

    scanned = len(source_files)
    parsed = 0
    created = 0
    skipped_invalid = 0
    skipped_agent = 0

    for file in source_files:
        if ctx.pipe is not None:
            ctx.pipe.raise_if_cancelled()
        try:
            if file.resolve().is_relative_to(ctx.snapshot_root.resolve()):
                continue
        except OSError:
            continue

        run = load_conversation_run(file)
        if run is None:
            skipped_invalid += 1
            continue
        parsed += 1
        if run.agent_name not in ctx.agent_names:
            skipped_agent += 1
            continue
        if _snapshot_one_run(run=run, ctx=ctx, threshold=threshold):
            created += 1

    return Str(
        value=(
            f"{SNAPSHOT_CONVERSATIONS_TOOL_NAME}: "
            f"scanned={scanned}, parsed={parsed}, created={created}, "
            f"skipped_invalid={skipped_invalid}, skipped_agent={skipped_agent}"
        ),
        truncation=NO_MESSAGE,
    )


__all__ = ["SnapshotMode", "snapshot_conversations"]
