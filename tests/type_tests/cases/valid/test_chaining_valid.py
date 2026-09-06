from __future__ import annotations

from typing import assert_type

from roboz import Ctx, Int, Message, Stop, Str, Tool, factory, tool


@tool
def root_tool(input: Str, messages: list[Message]) -> Str:
    return Str(value=input.value)


@tool(chained_to=root_tool)
def next_tool(input: Str, messages: list[Message]) -> Int:
    return Int(value=len(input.value))


assert_type(next_tool, Tool[Str, Int])


@tool
def union_parent(input: Int, messages: list[Message]) -> Int | Str:
    return input


@tool(chained_to=union_parent, chain_condition=lambda x: True)
def union_child_single_member(input: Int, messages: list[Message]) -> Int:
    return input


assert_type(union_child_single_member, Tool[Int, Int] | Tool[Str, Int])


@tool(chained_to=next_tool)
def third_tool(input: Int, messages: list[Message]) -> Str:
    return Str(value=str(input.value))


assert_type(third_tool, Tool[Int, Str])


@factory
def root_factory(input: Str, messages: list[Message], ctx: Ctx) -> Str:
    return input


@factory(chained_to=root_factory)
def next_factory(input: Str, messages: list[Message], ctx: Ctx) -> Int:
    return Int(value=len(input.value))


@tool(chained_to=root_factory)
def tool_from_factory_parent(input: Str, messages: list[Message]) -> Str:
    return input


@factory(chained_to=root_tool)
def factory_from_tool_parent(input: Str, messages: list[Message], ctx: Ctx) -> Str:
    return input


ctx = Ctx()
generated_tool = next_factory(ctx)
assert_type(generated_tool, Tool[Str, Int])


# copy() keeps Tool[TInput, TOutput] propagation.
copied_next = next_tool.copy(name="copied_next")
assert_type(copied_next, Tool[Str, Int])


@tool(chained_to=copied_next)
def after_copied_tool(input: Int, messages: list[Message]) -> Str:
    return Str(value=str(input.value))


assert_type(after_copied_tool, Tool[Int, Str])


# Copy of a factory-produced tool also preserves chained typing.
factory_tool = factory_from_tool_parent(ctx)
assert_type(factory_tool, Tool[Str, Str])

copied_factory_tool = factory_tool.copy(name="copied_factory_tool")
assert_type(copied_factory_tool, Tool[Str, Str])


@tool
def union_with_stop_parent(input: Int, messages: list[Message]) -> Int | Stop:
    return input


@tool(chained_to=union_with_stop_parent, chain_condition=lambda x: True)
def union_with_stop_child(input: Int, messages: list[Message]) -> Int:
    return input


@tool
def seq_parent_union(input: Int, messages: list[Message]) -> Str | Int:
    return input


# Single parent with mixed output; child accepts one branch (Int), so this is valid.
@tool(chained_to=seq_parent_union, chain_condition=lambda x: True)
def seq_single_union_child(input: Int, messages: list[Message]) -> Str | Int:
    return input


@tool
def seq_parent_tool(input: Str, messages: list[Message]) -> Str:
    return input


@factory
def seq_parent_factory(input: Str, messages: list[Message], ctx: Ctx) -> Str:
    return input


@tool(chained_to=[seq_parent_tool, seq_parent_factory])
def seq_mixed_child(input: Str, messages: list[Message]) -> Int:
    return Int(value=len(input.value))


assert_type(seq_mixed_child, Tool[Str, Int])


# Multiple parents where one has mixed output; Int branch matches child input.
@tool(chained_to=[seq_parent_union, seq_parent_tool], chain_condition=lambda x: True)
def seq_multi_union_child(input: Int, messages: list[Message]) -> Str | Int:
    return input


@factory(chained_to=[seq_parent_tool, seq_parent_factory])
def seq_mixed_factory(input: Str, messages: list[Message], ctx: Ctx) -> Str:
    return input


seq_factory_tool = seq_mixed_factory(ctx)
assert_type(seq_factory_tool, Tool[Str, Str])


copied_seq_parent = seq_parent_tool.copy(name="copied_seq_parent")
assert_type(copied_seq_parent, Tool[Str, Str])


@tool(chained_to=[copied_seq_parent, root_factory])
def seq_copied_mixed_child(input: Str, messages: list[Message]) -> Str:
    return input


assert_type(seq_copied_mixed_child, Tool[Str, Str])
