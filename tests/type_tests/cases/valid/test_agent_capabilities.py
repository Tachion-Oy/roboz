from typing import assert_type

from roboz import Agent
from roboz.deployment import (
    AgentCapability,
    DeployableAgent,
    Capability,
)
from roboz.llm import EndpointLike, MockLLMEndpoint
from roboz.runtime import EventPipe


class Extension:
    def build(
        self, pipe: EventPipe, *, default_endpoint: EndpointLike | None
    ) -> Capability:
        assert_type(default_endpoint, EndpointLike | None)
        return Capability()


class EndpointFree(AgentCapability):
    def build(
        self, pipe: EventPipe, *, default_endpoint: EndpointLike | None
    ) -> Capability:
        return Capability()


static_capability: AgentCapability = Capability()
capability: AgentCapability = Extension()
endpoint_free: AgentCapability = EndpointFree()


endpoint: EndpointLike = MockLLMEndpoint([])
definition = DeployableAgent(
    name="custom",
    agent_endpoint=endpoint,
    system_prompt="Use the configured capabilities.",
    capabilities=(static_capability, capability, endpoint_free),
)
assert_type(definition.build(), Agent)
