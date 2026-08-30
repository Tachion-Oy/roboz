"""Compactify live agent messages when context threshold is exceeded."""

import json
import logging
from typing import Final

from roboz import factory
from roboz.agent import get_active_agent_stack
from roboz.llm import (
    EndpointLike,
    bind_endpoint,
    endpoint_resource,
    estimate_conversation_tokens,
    resolve_endpoint,
)
from roboz.models import (
    BOOTSTRAP_MESSAGE_KINDS,
    NO_MESSAGE,
    All,
    BaseNames,
    Empty,
    Message,
    MessageKind,
    Role,
)

from roboz.standard.identifiers import COMPACTIFY_MESSAGES_TOOL_NAME

from ..summarize import summarize_conversation_segment
from .prompts import COMPACTIFY_INSTRUCTIONS, COMPACTIFY_SYSTEM_PROMPT
from .types import CompactifyMessagesCtx

logger = logging.getLogger(__name__)

COMPACTED_CONTEXT_KIND: Final[MessageKind] = MessageKind.COMPACTED_CONTEXT


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


@factory
def compactify_messages_when_needed(
    input: All, messages: list[Message], ctx: CompactifyMessagesCtx
) -> CompactifyStatus:
    """Compactify message history in place when context usage exceeds threshold."""
    threshold_percent = ctx.threshold_percent
    if threshold_percent <= 0:
        raise ValueError("threshold_percent must be greater than 0")
    endpoint_like = endpoint_resource(ctx.endpoint)
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
    if prefix_len >= len(messages):
        return _status(
            "blocked",
            consumed=consumed,
            threshold_tokens=threshold_tokens,
            max_tokens=max_tokens,
            compactions=compactions,
        )
    instructions = ctx.instructions.strip()
    if not instructions:
        raise ValueError("instructions must be a non-empty string")
    agent_stack = get_active_agent_stack()
    logger.info(
        "Calling llm (agent=%s, tool=%s)",
        agent_stack[-1] if agent_stack else None,
        COMPACTIFY_MESSAGES_TOOL_NAME,
    )
    summary = summarize_conversation_segment(
        endpoint=endpoint_like,
        system_prompt=ctx.system_prompt,
        instructions=instructions,
        conversation=_conversation_text(messages[prefix_len:]),
    )
    compacted_payload = {
        BaseNames.CALLER_FIELD: COMPACTIFY_MESSAGES_TOOL_NAME,
        BaseNames.VALUE_FIELD: "Context compactified for continuation.",
        "summary_markdown": summary,
        "percent_used_before": percent_used,
        "threshold_percent": threshold_percent,
    }
    messages[:] = [
        *messages[:prefix_len],
        Message(
            role=Role.USER,
            content=json.dumps(compacted_payload),
            message_kind=COMPACTED_CONTEXT_KIND,
        ),
    ]
    consumed = estimate_conversation_tokens(messages)
    compactions += 1
    ctx.state.count = compactions
    return _status(
        "compacted",
        consumed=consumed,
        threshold_tokens=threshold_tokens,
        max_tokens=max_tokens,
        compactions=compactions,
        compaction_summary=summary,
    )


COMPACTIFY_THRESHOLD_PERCENT: Final[float] = 60.0


def get_compactify_messages_when_needed_tool(
    *,
    endpoint: EndpointLike,
    threshold_percent: float | None = None,
    system_prompt: str = COMPACTIFY_SYSTEM_PROMPT,
    instructions: str = COMPACTIFY_INSTRUCTIONS,
):
    """Build ``compactify_messages_when_needed`` with shared defaults."""
    if threshold_percent is None:
        threshold_percent = COMPACTIFY_THRESHOLD_PERCENT
    if threshold_percent <= 0:
        raise ValueError("threshold_percent must be greater than 0")
    ctx = CompactifyMessagesCtx(
        endpoint=bind_endpoint(endpoint),
        threshold_percent=threshold_percent,
        system_prompt=system_prompt,
        instructions=instructions,
    )
    return compactify_messages_when_needed(ctx).copy(name=COMPACTIFY_MESSAGES_TOOL_NAME)


__all__ = [
    "COMPACTED_CONTEXT_KIND",
    "COMPACTIFY_THRESHOLD_PERCENT",
    "CompactifyStatus",
    "compactify_messages_when_needed",
    "get_compactify_messages_when_needed_tool",
]
