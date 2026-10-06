"""Agent configuration accepts constructed skills, not their factories."""

from roboz import Agent
from roboz.deployment import (
    Capability,
    SkillLabel,
    SkillLoading,
)
from roboz.llm import MockLLMEndpoint
from roboz.shed.skills import filesystem_skill


# Expected: reportArgumentType; both collections require concrete Skill objects.
Agent(
    name="files",
    system_prompt="Work on files.",
    agent_endpoint=MockLLMEndpoint([]),
    skills=[filesystem_skill],
)
Capability(
    label=SkillLabel("filesystem_skill", loading=SkillLoading.AUTOMATIC),
    value=filesystem_skill,
)
