"""The inferred context cannot be replaced with another type."""

from roboz import Skill, Tool


def build_tools(ctx: str) -> list[Tool]:
    return []


make_skill = Skill.factory(
    name="text", description="Text", instructions="Text", build_tools=build_tools,
)
# Expected: reportArgumentType; the builder requires a string context.
make_skill(42)
