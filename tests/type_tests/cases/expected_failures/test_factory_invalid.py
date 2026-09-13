from __future__ import annotations

from roboz import Int, Message, Str, factory


# Scenario: factory -> factory mismatch.
@factory
def factory_parent_str(input: Str, messages: list[Message], ctx: None) -> Str:
    return input


@factory(chained_to=factory_parent_str)
def factory_child_expects_int(input: Int, messages: list[Message], ctx: None) -> Int:
    return input
