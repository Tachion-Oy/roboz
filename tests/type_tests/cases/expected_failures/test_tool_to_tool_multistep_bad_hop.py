from __future__ import annotations

from roboz import Int, Message, Str, tool


# Scenario: multi-step chain with one incompatible hop.
@tool
def parent_int(input: Int, messages: list[Message]) -> Int:
    return input


@tool(chained_to=parent_int)
def mid_int_to_str(input: Int, messages: list[Message]) -> Str:
    return Str(value=str(input.value))


@tool(chained_to=mid_int_to_str)
def tail_wrong_input(input: Int, messages: list[Message]) -> Int:
    return input
