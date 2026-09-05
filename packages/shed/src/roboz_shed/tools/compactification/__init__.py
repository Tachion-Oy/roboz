"""Threshold-triggered conversation compaction built on Roboz summarization."""

from .compactify_messages import (
    COMPACTED_CONTEXT_KIND,
    DEFAULT_THRESHOLD_PERCENT,
    CompactifyMessagesCtx,
    CompactifyStatus,
    compactify_messages_when_needed,
    get_compactify_messages_when_needed_tool,
)
from .prompts import (
    COMPACTIFY_SYSTEM_PROMPT,
    COMPACTIFICATION_CONTINUATION_SKILL_MESSAGE,
)

__all__ = [
    "COMPACTED_CONTEXT_KIND",
    "DEFAULT_THRESHOLD_PERCENT",
    "COMPACTIFY_SYSTEM_PROMPT",
    "COMPACTIFICATION_CONTINUATION_SKILL_MESSAGE",
    "CompactifyMessagesCtx",
    "CompactifyStatus",
    "compactify_messages_when_needed",
    "get_compactify_messages_when_needed_tool",
]
