"""Prompt fragments shared by conversation summarization tools."""

from typing import Final

SUMMARY_OUTPUT_EXAMPLE: Final = {"value": "## Summary\n- Durable fact."}


def build_summary_user_prompt(
    *, instructions: str, conversation: str, max_chars: int | None
) -> str:
    """Build the task prompt, including the optional output-size contract."""
    size_instructions = (
        "\n\n## Output size\n\n"
        f"The decoded `value` string must be at most {max_chars} characters. "
        "Meet this limit in your first response by compressing or dropping the "
        "least important content first."
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
    return (
        f"Your output was {response_chars} characters; the hard limit is "
        f"{max_chars}. Rewrite it shorter, dropping the least durable content "
        "first."
    )


__all__ = [
    "SUMMARY_OUTPUT_EXAMPLE",
    "build_summary_length_feedback",
    "build_summary_user_prompt",
]
