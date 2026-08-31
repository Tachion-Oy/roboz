from __future__ import annotations

from dataclasses import dataclass

from roboz import FactoryCtx, Int, Message, Str, factory, tool


@dataclass(frozen=True)
class Ctx(FactoryCtx):
    pass


@tool
def tool_parent_str(input: Str, messages: list[Message]) -> Str:
    return input


# Scenario: tool -> factory mismatch.
@factory(chained_to=tool_parent_str)
def factory_child_expects_int(input: Int, messages: list[Message], ctx: Ctx) -> Int:
    return input
