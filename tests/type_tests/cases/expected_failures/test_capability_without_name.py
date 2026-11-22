from roboz.deployment import Capability, DeployableAgent
from roboz.runtime import EventPipe
from roboz.tooling import Tool
from roboz.tools import stop


class Unnamed(Capability):
    def build(self, agent: DeployableAgent, pipe: EventPipe) -> tuple[Tool, ...]:
        return (stop,)


# Expected: reportCallIssue; every capability needs a name.
Unnamed()
