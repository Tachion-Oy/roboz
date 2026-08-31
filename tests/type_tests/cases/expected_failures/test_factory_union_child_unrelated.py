from __future__ import annotations

from dataclasses import dataclass

from roboz import FactoryCtx, Int, Message, Str, Strs, factory


@dataclass(frozen=True)
class Ctx(FactoryCtx):
    pass


# Scenario: union parent where child accepts unrelated type.
@factory
def factory_union_parent(input: Int, messages: list[Message], ctx: Ctx) -> Int | Str:
    return input


@factory(chained_to=factory_union_parent, chain_condition=lambda x: True)
def factory_union_child_bad(input: Strs, messages: list[Message], ctx: Ctx) -> Strs:
    return input
