from roboz.deployment import DeployableAgent
from roboz.llm import MockLLMEndpoint
from roboz.runtime import EventPipe


def endpoint_factory(pipe: EventPipe) -> MockLLMEndpoint:
    return MockLLMEndpoint([])


definition = DeployableAgent(name="invalid")
definition.set_agent_endpoint(endpoint_factory)
