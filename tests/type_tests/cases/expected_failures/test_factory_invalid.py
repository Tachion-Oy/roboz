from __future__ import annotations

from dataclasses import dataclass

from roboz import FactoryCtx, Int, Message, Str, factory


@dataclass(frozen=True)
class Ctx(FactoryCtx):
    pass


# Scenario: factory -> factory mismatch.
@factory
def factory_parent_str(input: Str, messages: list[Message], ctx: Ctx) -> Str:
    return input


@factory(chained_to=factory_parent_str)
def factory_child_expects_int(input: Int, messages: list[Message], ctx: Ctx) -> Int:
    return input
