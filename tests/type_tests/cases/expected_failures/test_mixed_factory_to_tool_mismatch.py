from __future__ import annotations

from roboz import Int, Message, Str, factory, tool


@factory
def factory_parent_int(input: Int, messages: list[Message], ctx: None) -> Int:
    return input


# Scenario: factory -> tool mismatch.
@tool(chained_to=factory_parent_int)
def tool_child_expects_str(input: Str, messages: list[Message]) -> Str:
    return input
