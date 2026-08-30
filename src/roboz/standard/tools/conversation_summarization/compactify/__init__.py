"""Compact live agent context into a continuation summary."""

from .prompts import COMPACTIFY_INSTRUCTIONS, COMPACTIFY_SYSTEM_PROMPT
from .tool import (
    COMPACTED_CONTEXT_KIND,
    compactify_messages_when_needed,
    get_compactify_messages_when_needed_tool,
)
from .types import CompactifyBaseCtx, CompactifyMessagesCtx, CompactifyState

__all__ = [
    "COMPACTED_CONTEXT_KIND",
    "COMPACTIFY_INSTRUCTIONS",
    "COMPACTIFY_SYSTEM_PROMPT",
    "CompactifyBaseCtx",
    "CompactifyMessagesCtx",
    "CompactifyState",
    "compactify_messages_when_needed",
    "get_compactify_messages_when_needed_tool",
]
