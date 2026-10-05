"""Skill builders must produce concrete tools, not tool factories."""

from roboz import Factory, Skill, factory
from roboz.models import Message, Str


@factory
def prefix(input: Str, messages: list[Message], ctx: str) -> Str:
    return Str(value=ctx + input.value)


def build_tools(ctx: str) -> list[Factory[Str, Str, str]]:
    return [prefix]


# Expected: reportArgumentType; the builder returns factories instead of tools.
Skill.factory(name="text", description="Text", instructions="Text", build_tools=build_tools)
