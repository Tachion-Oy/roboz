"""Deterministic Librarian constructor with its maintenance capabilities."""

from collections.abc import Collection
from typing import Final

from roboshed.capabilities import (
    ArtifactRetention,
    ConversationSnapshots,
    MaintenanceCadence,
    MemoryConsolidation,
)
from roboshed.identifiers import LIBRARIAN_AGENT_NAME
from roboshed.sandbox import Sandbox
from roboz.deployment import DeployableAgent
from roboz.llm import EndpointLike

LIBRARIAN_AGENT_DESCRIPTION: Final[str] = (
    "Runs deterministic maintenance cycles using the configured capabilities."
)


def librarian(
    sandbox: Sandbox,
    agent_names: Collection[str],
    *,
    agent_endpoint: EndpointLike | None,
) -> DeployableAgent:
    """Configure maintenance for the sandbox and foreground agent names."""
    return DeployableAgent(
        name=LIBRARIAN_AGENT_NAME,
        description=LIBRARIAN_AGENT_DESCRIPTION,
        interaction_mode=None,
        is_agentic=False,
        automatic_tool_prompt=False,
        agent_endpoint=agent_endpoint,
        capabilities=(
            ConversationSnapshots(sandbox=sandbox, agent_names=agent_names),
            MemoryConsolidation(sandbox=sandbox, agent_names=agent_names),
            ArtifactRetention(sandbox=sandbox),
            MaintenanceCadence(sandbox=sandbox, agent_names=agent_names),
        ),
    )


__all__ = ["LIBRARIAN_AGENT_DESCRIPTION", "librarian"]
