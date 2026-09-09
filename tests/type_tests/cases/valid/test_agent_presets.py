from typing import assert_type

from roboshed.agents import librarian, orchestrator
from roboshed.capabilities import (
    ArtifactRetention,
    ConversationSnapshots,
    MaintenanceCadence,
    MemoryConsolidation,
)
from roboshed.sandbox import Sandbox

from roboz.deployment import AgentCapability, AgentDefinition
from roboz.llm import EndpointLike


def configure(
    sandbox: Sandbox, endpoint: EndpointLike, capability: AgentCapability
) -> None:
    assert_type(
        orchestrator(agent_endpoint=endpoint, capabilities=(capability,)),
        AgentDefinition,
    )
    assert_type(
        librarian(capabilities=(capability,), agent_endpoint=endpoint),
        AgentDefinition,
    )

    assert_type(
        librarian(
            capabilities=(
                ConversationSnapshots(
                    sandbox, "project", {"orchestrator"}, endpoint=endpoint
                ),
                MemoryConsolidation(
                    sandbox, "project", {"orchestrator"}, endpoint=endpoint
                ),
                ArtifactRetention(sandbox, "project"),
                MaintenanceCadence(sandbox, "project", {"orchestrator"}),
            )
        ),
        AgentDefinition,
    )
