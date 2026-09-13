"""Threshold-triggered conversation compaction with shared conversation summarization."""

from roboshed.tools.contexts import CompactionContext, CompactionState

from .compactify_messages import (
    COMPACTED_CONTEXT_KIND,
    DEFAULT_THRESHOLD_PERCENT,
    CompactifyStatus,
    compactify_messages_when_needed,
    get_compactify_messages_when_needed_tool,
)
from .prompts import (
    COMPACTIFICATION_CONTINUATION_SKILL_MESSAGE,
    COMPACTIFY_SYSTEM_PROMPT,
)
from .summarize import (
    DEFAULT_MAX_CHARS_TOLERANCE_PERCENT,
    SUMMARY_LENGTH_ATTEMPTS,
    summarize_conversation_segment,
)

__all__ = [
    "CompactionContext",
    "CompactionState",
    "COMPACTED_CONTEXT_KIND",
    "COMPACTIFICATION_CONTINUATION_SKILL_MESSAGE",
    "COMPACTIFY_SYSTEM_PROMPT",
    "CompactifyStatus",
    "DEFAULT_MAX_CHARS_TOLERANCE_PERCENT",
    "DEFAULT_THRESHOLD_PERCENT",
    "SUMMARY_LENGTH_ATTEMPTS",
    "compactify_messages_when_needed",
    "get_compactify_messages_when_needed_tool",
    "summarize_conversation_segment",
]
