from roboz.deployment import (
    Capability,
    DeployableAgent,
    SkillLabel,
    SkillLoading,
    ToolLabel,
)
from typing import Literal, assert_type
from collections.abc import Mapping

from roboz import Agent
from roboz.agent import AgentMode
from roboz.llm import EndpointLike, MockLLMEndpoint
from roboz.runtime import EventPipe
from roboz.tooling import Tool


class Extension(Capability):
    def __init__(self):
        super().__init__(label=ToolLabel("extension"))

    @property
    def required_attributes(self):
        return {"agent_endpoint": MockLLMEndpoint}

    def build(self, agent: DeployableAgent, pipe: EventPipe) -> tuple[Tool, ...]:
        assert_type(agent, DeployableAgent)
        return ()


class EndpointFree(Capability):
    def __init__(self):
        super().__init__(label=ToolLabel("endpoint_free"))

    @property
    def required_attributes(self):
        return {}

    def build(self, agent: DeployableAgent, pipe: EventPipe) -> tuple[Tool, ...]:
        return ()


static_capability: Capability = Capability(label=ToolLabel("empty"), value=())
capability: Capability = Extension()
endpoint_free: Capability = EndpointFree()


endpoint: EndpointLike = MockLLMEndpoint([])
definition = DeployableAgent(
    name="custom",
    system_prompt="Use the configured capabilities.",
    capabilities=(static_capability, capability, endpoint_free),
)
definition.set_agent_endpoint(endpoint)
definition.name = "updated"
definition.description = "Updated description."
definition.system_prompt = "Use the updated configuration."
definition.mode = AgentMode.AUTONOMOUS
definition.automatic_tool_prompt = False
definition.agent_endpoint = endpoint
definition.initial_messages = ("Updated context.",)
assert_type(definition.build(), tuple[Agent, tuple[Agent, ...]])

assert_type(definition.capabilities, tuple[Capability, ...])
assert_type(definition.capability_selection, Mapping[str, bool | SkillLoading] | None)
assert_type(definition.resolve_capabilities(), dict[str, bool | SkillLoading])
assert_type(ToolLabel("tool").kind, Literal["tool"])
assert_type(SkillLabel("skill").kind, Literal["skill"])
assert_type(definition.set_capability_selection({}), None)
assert_type(definition.set_capability_selection(None), None)
