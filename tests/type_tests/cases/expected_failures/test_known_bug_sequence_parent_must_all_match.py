from __future__ import annotations

from roboz import Int, Message, Stop, Str, tool


# Known typing bug:
# In chained_to sequences, every parent should be compatible with child input.
# This should be rejected because seq_parent_str outputs Str, not Int.


@tool
def parent_for_copy(input: Int, messages: list[Message]) -> Int | Stop:
    return input


@tool(chained_to=parent_for_copy, chain_condition=lambda x: True)
def copied_source(input: Int, messages: list[Message]) -> Int:
    return input


copied_source_bad = copied_source.copy(name="copied_source_bad")


@tool
def seq_parent_str(input: Str, messages: list[Message]) -> Str:
    return input


@tool(chained_to=[copied_source_bad, seq_parent_str])
def child_should_be_rejected(input: Int, messages: list[Message]) -> Int:
    return input
