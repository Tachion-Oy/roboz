from roboshed.capabilities import (
    Compactification,
    ConversationSnapshots,
    MemoryConsolidation,
)

from roboz.llm import MockLLMEndpoint
from roboz.runtime import EventPipe


def endpoint_factory(pipe: EventPipe) -> MockLLMEndpoint:
    return MockLLMEndpoint([])


capability = Compactification(endpoint=endpoint_factory)

# Every model-using capability requires a concrete endpoint (reportArgumentType).
snapshots = ConversationSnapshots(endpoint=endpoint_factory)
consolidation = MemoryConsolidation(endpoint=endpoint_factory)
