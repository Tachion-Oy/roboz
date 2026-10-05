"""Skill factories retain the concrete context required by their tool builder."""

from collections.abc import Callable
from typing import assert_type

from roboz import Agent, Skill, Tool, factory
from roboz.deployment import Capability
from roboz.llm import MockLLMEndpoint
from roboz.models import Message, Str
from roboz.shed.skills import FilesystemContext, filesystem_skill
from tests.type_tests.fixtures.primitive_contexts import PrefixContext


@factory
def prefix(input: Str, messages: list[Message], ctx: PrefixContext) -> Str:
    return Str(value=ctx.prefix + input.value)


def build_tools(ctx: PrefixContext) -> list[Tool | list[Tool]]:
    assert_type(ctx, PrefixContext)
    return [[prefix(ctx)]]


make_skill = Skill.factory(
    name="prefix", description="Prefix text", instructions="Use the prefix tool.",
    build_tools=build_tools,
)
assert_type(make_skill, Callable[[PrefixContext], Skill])
assert_type(make_skill(PrefixContext(prefix="> ")), Skill)
assert_type(filesystem_skill, Callable[[FilesystemContext], Skill])


def bind(ctx: FilesystemContext) -> None:
    skill = filesystem_skill(ctx)
    assert_type(skill, Skill)
    Agent(
        name="files", system_prompt="Work on files.",
        agent_endpoint=MockLLMEndpoint([]), skills=[skill],
    )
    Capability(auto_loaded_skills=(skill,))
