"""Agent configuration accepts constructed skills, not their factories."""

from roboz import Agent, Skill
from roboz.deployment import Capability, DeployableAgent
from roboz.llm import MockLLMEndpoint
from roboz.runtime import EventPipe
from roboz.shed.skills import filesystem_skill


# Expected: reportArgumentType; the collection requires concrete Skill objects.
Agent(
    name="files",
    system_prompt="Work on files.",
    agent_endpoint=MockLLMEndpoint([]),
    skills=[filesystem_skill],
)


class Files(Capability):
    # Expected: reportReturnType; build() returns concrete Skill objects too.
    def build(self, agent: DeployableAgent, pipe: EventPipe) -> tuple[Skill, ...]:
        return (filesystem_skill,)
