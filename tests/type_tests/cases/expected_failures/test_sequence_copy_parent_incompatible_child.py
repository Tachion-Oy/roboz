from __future__ import annotations

from roboz.models import Int, Message, Str
from roboz import tool


@tool
def seq_tool_parent_int(input: Int, messages: list[Message]) -> Int:
    return input


copied_seq_tool_parent_int = seq_tool_parent_int.copy(name="copied_seq_tool_parent_int")


# Scenario: copied parent used in sequence with incompatible child input.
@tool(chained_to=[copied_seq_tool_parent_int, seq_tool_parent_int])
def seq_child_str_after_copy_bad(input: Str, messages: list[Message]) -> Str:
    return input
