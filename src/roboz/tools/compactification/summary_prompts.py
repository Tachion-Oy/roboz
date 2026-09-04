"""Shared prompt fragments for conversation summaries."""

from collections.abc import Mapping
from typing import Final

from roboz.models import BaseNames

SUMMARY_OUTPUT_EXAMPLE: Final[Mapping[str, str]] = {
    BaseNames.VALUE_FIELD: "## Summary\n- Durable fact."
}

_PERCENT_SCALE: Final[int] = 100
_MINIMUM_OVERAGE_PERCENT: Final[int] = 1


def build_summary_user_prompt(
    *, instructions: str, conversation: str, max_chars: int | None
) -> str:
    """Build the task prompt, including the optional output-size contract."""
    size_instructions = (
        "\n\n## Output size\n\n"
        f"The requested budget for the decoded `value` string is {max_chars} "
        "characters. Do not count characters or pack the response to the "
        "boundary. Select decisively, drop the least important content first, "
        "and aim comfortably below the budget."
        if max_chars is not None
        else ""
    )
    return (
        "## Instructions\n\n"
        f"{instructions}{size_instructions}\n\n"
        "## Conversation content\n\n"
        f"{conversation}"
    )


def build_summary_length_feedback(*, response_chars: int, max_chars: int) -> str:
    """Build the corrective prompt for an over-length summary."""
    overage = (
        f"roughly {max(
            _MINIMUM_OVERAGE_PERCENT,
            round((response_chars - max_chars) / max_chars * _PERCENT_SCALE),
        )}% over"
        if max_chars > 0
        else "over"
    )
    return (
        f"The response was {overage} the requested budget. "
        "Rewrite it substantially shorter. Use that percentage only as a rough "
        "guide; do not count characters or preserve content merely because it "
        "appeared before. Remove complete low-priority items first and leave "
        "comfortable headroom."
    )


__all__ = [
    "SUMMARY_OUTPUT_EXAMPLE",
    "build_summary_length_feedback",
    "build_summary_user_prompt",
]
