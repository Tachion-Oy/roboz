"""Threshold-triggered conversation compaction built on Roboz summarization."""

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

__all__ = [
    "COMPACTED_CONTEXT_KIND",
    "DEFAULT_THRESHOLD_PERCENT",
    "COMPACTIFY_SYSTEM_PROMPT",
    "COMPACTIFICATION_CONTINUATION_SKILL_MESSAGE",
    "CompactifyStatus",
    "compactify_messages_when_needed",
    "get_compactify_messages_when_needed_tool",
]
