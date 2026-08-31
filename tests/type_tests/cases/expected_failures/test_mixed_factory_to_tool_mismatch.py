from __future__ import annotations

from dataclasses import dataclass

from roboz import FactoryCtx, Int, Message, Str, factory, tool


@dataclass(frozen=True)
class Ctx(FactoryCtx):
    pass


@factory
def factory_parent_int(input: Int, messages: list[Message], ctx: Ctx) -> Int:
    return input


# Scenario: factory -> tool mismatch.
@tool(chained_to=factory_parent_int)
def tool_child_expects_str(input: Str, messages: list[Message]) -> Str:
    return input
