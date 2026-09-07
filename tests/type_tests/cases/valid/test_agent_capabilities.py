from typing import assert_type

from roboz import Agent
from roboz.deployment import (
    AgentCapability,
    AgentDefinition,
    Capability,
)
from roboz.llm import EndpointLike
from roboz.runtime import EventPipe


class Extension:
    def build(self, pipe: EventPipe, agent_endpoint: EndpointLike | None) -> Capability:
        return Capability()


static_capability: AgentCapability = Capability()
capability: AgentCapability = Extension()
assert_type(
    AgentDefinition(
        name="custom",
        agent_endpoint=None,
        is_agentic=False,
        capabilities=(
            static_capability,
            capability,
        ),
    ),
    AgentDefinition,
)


definition = AgentDefinition(name="worker", agent_endpoint=None, is_agentic=False)
assert_type(definition.build(), Agent)
