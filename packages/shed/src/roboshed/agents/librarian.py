"""Deterministic Librarian preset with caller-selected maintenance capabilities."""

from collections.abc import Sequence
from typing import Final

from roboshed.identifiers import LIBRARIAN_AGENT_NAME
from roboz.deployment import AgentCapability, DeployableAgent
from roboz.llm import EndpointLike

LIBRARIAN_AGENT_DESCRIPTION: Final[str] = (
    "Runs deterministic maintenance cycles using the configured capabilities."
)


def librarian(
    *,
    capabilities: Sequence[AgentCapability],
    agent_endpoint: EndpointLike | None = None,
    name: str = LIBRARIAN_AGENT_NAME,
) -> DeployableAgent:
    """Configure a deterministic background agent with ordered capabilities.

    Supply automatic maintenance work and a stopping policy, such as
    MaintenanceCadence. Each cycle runs the contributed default tools in order.
    Capabilities own their settings and may use agent_endpoint as a model default.
    The definition selects no project, persistence, or thread lifecycle.
    """
    return DeployableAgent(
        name=name,
        description=LIBRARIAN_AGENT_DESCRIPTION,
        interaction_mode=None,
        agent_endpoint=agent_endpoint,
        is_agentic=False,
        automatic_tool_prompt=False,
        capabilities=tuple(capabilities),
    )


__all__ = ["LIBRARIAN_AGENT_DESCRIPTION", "librarian"]
