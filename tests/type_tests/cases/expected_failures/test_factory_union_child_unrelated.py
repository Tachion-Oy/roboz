from __future__ import annotations

from roboz import Int, Message, Str, Strs, factory


# Scenario: union parent where child accepts unrelated type.
@factory
def factory_union_parent(input: Int, messages: list[Message], ctx: None) -> Int | Str:
    return input


@factory(chained_to=factory_union_parent, chain_condition=lambda x: True)
def factory_union_child_bad(input: Strs, messages: list[Message], ctx: None) -> Strs:
    return input
