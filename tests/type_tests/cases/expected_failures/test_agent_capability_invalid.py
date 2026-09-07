from roboz.deployment import AgentCapability
from roboz.llm import EndpointLike
from roboz.runtime import EventPipe


class Invalid(AgentCapability):
    def build(self, pipe: EventPipe, *, default_endpoint: EndpointLike | None) -> str:
        return "not capability contributions"


capability: AgentCapability = Invalid()
