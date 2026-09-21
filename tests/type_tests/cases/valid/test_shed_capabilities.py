"""Shed capabilities compose with concrete endpoints and the core build contract."""

from typing import assert_type

from roboz.shed.agents import librarian, orchestrator
from roboz.shed.capabilities import (
    ArtifactRetention,
    Compactification,
    ConversationSnapshots,
    FileCommands,
    FileEditing,
    MaintenanceCadence,
    MemoryConsolidation,
)
from roboz.shed.sandbox import Sandbox
from roboz import Agent
from roboz.dependencies import ExternalDependency
from roboz.deployment import AgentCapability, Capability, DeployableAgent
from roboz.llm import EndpointLike
from roboz.runtime import EventPipe


def compose(sandbox: Sandbox, endpoint: EndpointLike, pipe: EventPipe) -> None:
    capabilities: tuple[AgentCapability, ...] = (
        FileCommands(),
        FileEditing(),
        Compactification(endpoint=endpoint),
        ConversationSnapshots(endpoint=endpoint),
        MemoryConsolidation(endpoint=endpoint),
        ArtifactRetention(),
        MaintenanceCadence(seconds=0),
    )
    definition = orchestrator(sandbox, agent_endpoint=endpoint)
    definition.set_attributes(sandbox=sandbox, watched_agent_names={"orchestrator"})
    assert_type(definition, DeployableAgent)
    assert_type(
        librarian(sandbox, {"orchestrator"}, agent_endpoint=endpoint), DeployableAgent
    )
    for capability in capabilities:
        assert_type(capability.build(definition, pipe), Capability)
    assert_type(definition.build(), tuple[Agent, tuple[Agent, ...]])
    assert_type(definition.external_dependencies(), tuple[ExternalDependency, ...])
