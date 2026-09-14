from __future__ import annotations

from roboz.models import Int, Message, Stop, Str
from roboz import tool


# Scenario: copied tool used in incompatible downstream chain.
@tool
def parent_for_copy(input: Int, messages: list[Message]) -> Int | Stop:
    return input


@tool(chained_to=parent_for_copy, chain_condition=lambda x: True)
def copied_source(input: Int, messages: list[Message]) -> Int:
    return input


copied_source_bad = copied_source.copy(name="copied_source_bad")


@tool(chained_to=copied_source_bad)
def child_bad_after_copy(input: Str, messages: list[Message]) -> Str:
    return input
