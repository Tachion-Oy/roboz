from __future__ import annotations

from roboz import Int, Message, Str, tool


@tool
def seq_tool_parent_str(input: Str, messages: list[Message]) -> Str:
    return input


@tool
def seq_tool_parent_int(input: Int, messages: list[Message]) -> Int:
    return input


# Scenario: one parent in the sequence is incompatible with the child input.
@tool(chained_to=[seq_tool_parent_str, seq_tool_parent_int])
def seq_child_int_bad(input: Int, messages: list[Message]) -> Int:
    return input
