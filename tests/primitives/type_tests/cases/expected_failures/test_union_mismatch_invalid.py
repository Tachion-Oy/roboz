from __future__ import annotations

from roboz import Int, Message, Str, Strs, tool


# Scenario: union parent child accepts none of members.
@tool
def union_parent(input: Int, messages: list[Message]) -> Int | Str:
    return input


@tool(chained_to=union_parent, chain_condition=lambda x: True)
def union_child_bad(input: Strs, messages: list[Message]) -> Strs:
    return input
