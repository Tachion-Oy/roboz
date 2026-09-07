from __future__ import annotations

from roboz import Ctx, Int, Message, Str, factory, tool


@tool
def seq_tool_parent_str(input: Str, messages: list[Message]) -> Str:
    return input


@factory
def seq_factory_parent_str(input: Str, messages: list[Message], ctx: Ctx) -> Str:
    return input


# Scenario: mixed tool/factory sequence where all parents are incompatible.
@tool(chained_to=[seq_tool_parent_str, seq_factory_parent_str])
def seq_child_int_mixed_bad(input: Int, messages: list[Message]) -> Int:
    return input
