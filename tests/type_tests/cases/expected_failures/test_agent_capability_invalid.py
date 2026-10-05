from roboz.deployment import (
    Capability,
    ToolLabel,
    DeployableAgent,
)
from roboz.runtime import EventPipe


class Invalid(Capability):
    def __init__(self):
        super().__init__(label=ToolLabel("invalid"))

    @property
    def required_attributes(self):
        return {}

    def build(self, agent: DeployableAgent, pipe: EventPipe) -> str:
        return "not capability contributions"


capability: Capability = Invalid()
