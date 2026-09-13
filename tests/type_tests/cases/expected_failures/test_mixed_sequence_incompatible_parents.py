from __future__ import annotations

from roboz import Int, Message, Str, factory, tool


@tool
def tool_parent_str(input: Str, messages: list[Message]) -> Str:
    return input


@factory
def factory_parent_int(input: Int, messages: list[Message], ctx: None) -> Int:
    return input


# Scenario: sequence chain mixes incompatible tool/factory parents.
@tool(chained_to=[tool_parent_str, factory_parent_int(None)])
def tool_child_bad_sequence(input: Str, messages: list[Message]) -> Str:
    return input


@tool(chained_to=[tool_parent_str, factory_parent_int])
def tool_child_bad_sequence_2(input: Str, messages: list[Message]) -> Str:
    return input
