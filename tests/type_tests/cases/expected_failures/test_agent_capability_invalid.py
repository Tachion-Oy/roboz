from roboz.deployment import AgentCapability, DeployableAgent
from roboz.runtime import EventPipe


class Invalid(AgentCapability):
    @property
    def required_attributes(self):
        return {}

    def build(self, agent: DeployableAgent, pipe: EventPipe) -> str:
        return "not capability contributions"


capability: AgentCapability = Invalid()
