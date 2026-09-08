from roboz.deployment import AgentDefinition
from roboz.llm import MockLLMEndpoint
from roboz.runtime import EventPipe


def endpoint_factory(pipe: EventPipe) -> MockLLMEndpoint:
    return MockLLMEndpoint([])


definition = AgentDefinition(name="invalid", agent_endpoint=endpoint_factory)
