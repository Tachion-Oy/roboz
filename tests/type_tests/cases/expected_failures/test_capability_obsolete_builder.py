from roboz.deployment import AgentCapability, Capability
from roboz.llm import EndpointLike
from roboz.runtime import EventPipe


class Obsolete(AgentCapability):
    def build(self, pipe: EventPipe, agent_endpoint: EndpointLike | None) -> Capability:
        return Capability()


capability: AgentCapability = Obsolete()
