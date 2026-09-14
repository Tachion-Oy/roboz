from __future__ import annotations

from roboz.models import Int, Message, Str
from roboz import tool


# Scenario: direct tool -> tool mismatch.
@tool
def parent_str(input: Str, messages: list[Message]) -> Str:
    return input


@tool(chained_to=parent_str)
def child_expects_int(input: Int, messages: list[Message]) -> Int:
    return input
