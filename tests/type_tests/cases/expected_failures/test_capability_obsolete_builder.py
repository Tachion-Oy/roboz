from roboz.deployment import (
    Capability,
)
from roboz.llm import EndpointLike
from roboz.runtime import EventPipe
from roboz.tooling import Tool


class Obsolete(Capability):
    def __init__(self):
        super().__init__(name="obsolete")

    def build(
        self, pipe: EventPipe, agent_endpoint: EndpointLike | None
    ) -> tuple[Tool, ...]:
        return ()


capability: Capability = Obsolete()
