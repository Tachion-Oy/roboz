from typing import assert_type

from roboshed.agents import librarian, orchestrator
from roboshed.sandbox import Sandbox

from roboz.deployment import AgentCapability, DeployableAgent
from roboz.llm import EndpointLike


def configure(
    sandbox: Sandbox, endpoint: EndpointLike, capability: AgentCapability
) -> None:
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
