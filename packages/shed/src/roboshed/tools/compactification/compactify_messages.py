"""Compactify live agent messages when context threshold is exceeded."""

import json
import math
from dataclasses import dataclass
from functools import partial
from typing import Final

from roboz import Ctx
from roboz.llm import EndpointLike, estimate_conversation_tokens, resolve_endpoint
from roboz.llm.binding import _validate_endpoint
from roboz.models import (
    BOOTSTRAP_MESSAGE_KINDS,
    All,
    BaseNames,
    Empty,
    Message,
    MessageKind,
    Role,
)
from roboz.models.truncation import NO_MESSAGE
from roboz.runtime import EventPipe
from roboz.tooling import Tool
from roboz.tooling.context import _prepare_context
from roboz.tooling.decorators import factory
from roboz.tools.compactification import summarize_conversation_segment
from roboshed.identifiers import COMPACTIFY_MESSAGES_TOOL_NAME

from .prompts import (
    COMPACTIFICATION_CONTINUATION_SKILL_MESSAGE,
    COMPACTIFY_SYSTEM_PROMPT,
)


@dataclass
class CompactionState:
    """Successful compactions owned by one constructed tool."""

    count: int = 0


DEFAULT_THRESHOLD_PERCENT: Final[float] = 80.0
COMPACTED_CONTEXT_KIND: Final[MessageKind] = MessageKind.COMPACTED_CONTEXT
# Match estimate_conversation_tokens; validate the serialized candidate as well.
_ESTIMATED_CHARS_PER_TOKEN: Final[int] = 4


class CompactifyStatus(Empty):
    """Evolving context-budget heartbeat for the compactify tool."""

    status: str  # "ok" | "compacted" | "blocked"
    context: str  # e.g. "45.2k / 128k (35%)"
    to_compaction: str  # tokens of headroom before the next compaction
    compactions: int
    compaction_summary: str | None


def _fmt_tokens(n: int) -> str:
    if n < 1_000:
        return str(n)
    thousands = n / 1_000
    if thousands < 100:
        return f"{thousands:.1f}k"
    return f"{round(thousands)}k"


def _context_summary(consumed: int, max_tokens: int, percent_used: float) -> str:
    return f"{_fmt_tokens(consumed)} / {_fmt_tokens(max_tokens)} ({percent_used:.0f}%)"


def _conversation_text(messages: list[Message]) -> str:
    return "\n\n".join(f"## {m.role.value.upper()}\n{m.content}" for m in messages)


def _bootstrap_prefix_len(messages: list[Message]) -> int:
    """Preserve only the contiguous bootstrap run stamped with known bootstrap kinds."""
    idx = 0
    while idx < len(messages) and messages[idx].message_kind in BOOTSTRAP_MESSAGE_KINDS:
        idx += 1
    return idx


def _compacted_message(
    summary: str, *, percent_used_before: float, threshold_percent: float
) -> Message:
    """Build the persisted continuation payload, including its JSON overhead."""
    return Message(
        role=Role.USER,
        content=json.dumps(
            {
                BaseNames.CALLER_FIELD: COMPACTIFY_MESSAGES_TOOL_NAME,
                BaseNames.VALUE_FIELD: "Context compactified for continuation.",
                "summary_markdown": summary,
                "percent_used_before": percent_used_before,
                "threshold_percent": threshold_percent,
            }
        ),
        message_kind=COMPACTED_CONTEXT_KIND,
    )


def _status(
    status: str,
    *,
    consumed: int,
    threshold_tokens: int,
    max_tokens: int,
    compactions: int,
    compaction_summary: str | None = None,
) -> CompactifyStatus:
    pct = 0.0 if max_tokens <= 0 else (consumed / max_tokens) * 100.0
    return CompactifyStatus(
        status=status,
        context=_context_summary(consumed, max_tokens, pct),
        to_compaction=_fmt_tokens(max(0, threshold_tokens - consumed)),
        compactions=compactions,
        truncation=NO_MESSAGE,
        compaction_summary=compaction_summary,
    )


def _validate_timeout(timeout_s: float | None) -> None:
    if timeout_s is not None and (not math.isfinite(timeout_s) or timeout_s <= 0):
        raise ValueError("timeout_s must be finite and greater than 0")


def _check_controls(pipe: EventPipe | None) -> None:
    if pipe is not None:
        pipe.raise_if_cancelled()
        pipe.raise_if_interrupted()


@factory
def compactify_messages_when_needed(
    input: All, messages: list[Message], ctx: Ctx
) -> CompactifyStatus:
    """Compact the active conversation when its context budget reaches the threshold.

    Preserve startup instructions and replace the remaining history with a
    continuation handoff. Return context usage and the number of successful
    compactions. Report blocked without changing history when only startup
    instructions remain or no replacement fits below the context budget.
    """
    threshold_percent = ctx.threshold_percent
    if not math.isfinite(threshold_percent) or threshold_percent <= 0:
        raise ValueError("threshold_percent must be finite and greater than 0")
    _validate_timeout(ctx.timeout_s)
    _check_controls(ctx.pipe)
    endpoint_like = ctx.endpoint
    endpoint = resolve_endpoint(endpoint_like)
    max_tokens = endpoint.max_context_tokens
    consumed = estimate_conversation_tokens(messages)
    percent_used = 0.0 if max_tokens <= 0 else (consumed / max_tokens) * 100.0
    threshold_tokens = int(max_tokens * threshold_percent / 100.0)
    compactions = ctx.state.count

    if percent_used < threshold_percent:
        return _status(
            "ok",
            consumed=consumed,
            threshold_tokens=threshold_tokens,
            max_tokens=max_tokens,
            compactions=compactions,
        )
    prefix_len = _bootstrap_prefix_len(messages)
    blocked = _status(
        "blocked",
        consumed=consumed,
        threshold_tokens=threshold_tokens,
        max_tokens=max_tokens,
        compactions=compactions,
    )
    if prefix_len >= len(messages):
        return blocked
    skill_message = ctx.skill_message.strip()
    if not skill_message:
        raise ValueError("skill_message must be a non-empty string")
    # Measure at the replacement's length: truncation of preserved messages can
    # depend on their distance from the end of the conversation.
    candidate = [
        *messages[:prefix_len],
        _compacted_message(
            "", percent_used_before=percent_used, threshold_percent=threshold_percent
        ),
    ]
    target_tokens = min(threshold_tokens, max_tokens) - 1
    max_chars = (
        target_tokens - estimate_conversation_tokens(candidate)
    ) * _ESTIMATED_CHARS_PER_TOKEN
    if max_chars <= 0:
        return blocked
    summary = summarize_conversation_segment(
        endpoint=endpoint_like,
        system_prompt=ctx.system_prompt,
        instructions=skill_message,
        conversation=_conversation_text(messages[prefix_len:]),
        max_chars=max_chars,
        max_chars_tolerance_percent=0,
        pipe=ctx.pipe,
        timeout_s=ctx.timeout_s,
    )
    _check_controls(ctx.pipe)
    candidate[-1] = _compacted_message(
        summary, percent_used_before=percent_used, threshold_percent=threshold_percent
    )
    compacted_tokens = estimate_conversation_tokens(candidate)
    # The summarizer returns its shortest attempt even when none meets the
    # character limit. JSON escaping can also exhaust the remaining headroom.
    if len(summary) > max_chars or compacted_tokens > target_tokens:
        return blocked
    _check_controls(ctx.pipe)
    messages[:] = candidate
    compactions += 1
    ctx.state.count = compactions
    return _status(
        "compacted",
        consumed=compacted_tokens,
        threshold_tokens=threshold_tokens,
        max_tokens=max_tokens,
        compactions=compactions,
        compaction_summary=summary,
    )


def get_compactify_messages_when_needed_tool(
    *,
    endpoint: EndpointLike,
    threshold_percent: float = DEFAULT_THRESHOLD_PERCENT,
    system_prompt: str = COMPACTIFY_SYSTEM_PROMPT,
    skill_message: str = COMPACTIFICATION_CONTINUATION_SKILL_MESSAGE,
    pipe: EventPipe | None = None,
    timeout_s: float | None = None,
) -> Tool[All, CompactifyStatus]:
    """Build a continuation compactor with an independent success counter.

    ``threshold_percent`` must be finite and positive. The owning agent's
    ``pipe`` supplies cancellation, interruption, and summarization events.
    ``timeout_s`` is a positive, finite per-provider-attempt timeout; ``None``
    leaves attempts unbounded. Cancellation or timeout stops waiting for the
    provider, without forcibly terminating its worker. Failed attempts preserve
    the conversation and success counter.

    Summaries are budgeted below the context threshold and endpoint capacity.
    If the preserved prefix leaves no room, or no returned replacement fits,
    return ``blocked`` with the conversation and success counter unchanged.
    """
    if not math.isfinite(threshold_percent) or threshold_percent <= 0:
        raise ValueError("threshold_percent must be finite and greater than 0")
    _validate_timeout(timeout_s)
    _validate_endpoint(endpoint)
    ctx = Ctx(
        endpoint=endpoint,
        threshold_percent=threshold_percent,
        system_prompt=system_prompt,
        skill_message=skill_message,
        pipe=pipe,
        timeout_s=timeout_s,
    )
    return compactify_messages_when_needed(ctx).copy(name=COMPACTIFY_MESSAGES_TOOL_NAME)


__all__ = [
    "COMPACTED_CONTEXT_KIND",
    "DEFAULT_THRESHOLD_PERCENT",
    "CompactifyStatus",
    "compactify_messages_when_needed",
    "get_compactify_messages_when_needed_tool",
]


compactify_messages_when_needed._prepare_ctx = partial(
    _prepare_context,
    required=("endpoint", "threshold_percent", "system_prompt", "skill_message"),
    defaults={"pipe": None, "timeout_s": None},
    default_factories={"state": CompactionState},
)
