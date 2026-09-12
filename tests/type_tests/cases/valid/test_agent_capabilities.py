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
    @property
    def required_attributes(self):
        return {"agent_endpoint": MockLLMEndpoint}

    def build(self, agent: DeployableAgent, pipe: EventPipe) -> Capability:
        assert_type(agent, DeployableAgent)
        return Capability()


class EndpointFree(AgentCapability):
    @property
    def required_attributes(self):
        return {}

    def build(self, agent: DeployableAgent, pipe: EventPipe) -> Capability:
        return Capability()


static_capability: AgentCapability = Capability()
capability: AgentCapability = Extension()
endpoint_free: AgentCapability = EndpointFree()


endpoint: EndpointLike = MockLLMEndpoint([])
definition = DeployableAgent(
    name="custom",
    system_prompt="Use the configured capabilities.",
    default_capabilities=(static_capability, capability, endpoint_free),
)
definition.set_agent_endpoint(endpoint)
assert_type(definition.build(), tuple[Agent, tuple[Agent, ...]])
