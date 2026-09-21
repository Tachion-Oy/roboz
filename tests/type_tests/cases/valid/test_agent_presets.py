from typing import assert_type

from roboz.shed.agents import librarian, orchestrator
from roboz.shed.sandbox import Sandbox

from roboz.deployment import AgentCapability, DeployableAgent
from roboz.llm import EndpointLike


def configure(
    sandbox: Sandbox, endpoint: EndpointLike, capability: AgentCapability
) -> None:
    sandbox.configure_scope("project")
    assert_type(
        orchestrator(sandbox, agent_endpoint=endpoint),
        DeployableAgent,
    )
    assert_type(
        librarian(sandbox, {"orchestrator"}, agent_endpoint=endpoint),
        DeployableAgent,
    )

    assert_type(
        librarian(sandbox, {"orchestrator"}, agent_endpoint=endpoint),
        DeployableAgent,
    )
