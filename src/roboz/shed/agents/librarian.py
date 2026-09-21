"""Deterministic Librarian constructor with its maintenance capabilities."""

from collections.abc import Collection
from typing import Final

from roboz.shed.capabilities import (
    ArtifactRetention,
    ConversationSnapshots,
    MaintenanceCadence,
    MemoryConsolidation,
)
from roboz.shed.identifiers import LIBRARIAN_AGENT_NAME
from roboz.shed.sandbox import Sandbox
from roboz.deployment import DeployableAgent
from roboz.llm import EndpointLike
from roboz.models import AgentMode

LIBRARIAN_AGENT_DESCRIPTION: Final[str] = (
    "Runs deterministic maintenance cycles using the configured capabilities."
)


def librarian(
    sandbox: Sandbox,
    watched_agent_names: Collection[str],
    *,
    agent_endpoint: EndpointLike | None,
) -> DeployableAgent:
    """Configure maintenance for the sandbox and foreground agent names."""
    agent = DeployableAgent(
        name=LIBRARIAN_AGENT_NAME,
        description=LIBRARIAN_AGENT_DESCRIPTION,
        mode=AgentMode.DETERMINISTIC,
        automatic_tool_prompt=False,
        default_capabilities=(
            ConversationSnapshots(),
            MemoryConsolidation(),
            ArtifactRetention(),
            MaintenanceCadence(),
        ),
    )
    agent.set_agent_endpoint(agent_endpoint)
    agent.set_attributes(
        sandbox=sandbox,
        watched_agent_names=frozenset(watched_agent_names),
    )
    return agent


__all__ = ["LIBRARIAN_AGENT_DESCRIPTION", "librarian"]
