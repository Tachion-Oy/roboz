from roboshed.capabilities import Compactification

from roboz.llm import MockLLMEndpoint
from roboz.runtime import EventPipe


def endpoint_factory(pipe: EventPipe) -> MockLLMEndpoint:
    return MockLLMEndpoint([])


capability = Compactification(endpoint=endpoint_factory)
