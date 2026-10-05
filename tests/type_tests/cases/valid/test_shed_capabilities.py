"""Shed capabilities compose with concrete endpoints and the core build contract."""

from pathlib import Path
from typing import assert_type

from roboz.shed.agents import librarian, orchestrator
from roboz.shed.capabilities import (
    ArtifactRetention,
    Compactification,
    ConversationSnapshots,
    Email,
    Filesystem,
    MaintenanceCadence,
    MemoryConsolidation,
    SafeScripts,
)
from roboz.shed.sandbox import Sandbox
from roboz.shed.tools.email import EmailService
from roboz import Agent, Skill, Tool
from roboz.dependencies import ExternalDependency
from roboz.deployment import Capability, DeployableAgent
from roboz.llm import EndpointLike
from roboz.runtime import EventPipe


def compose(
    sandbox: Sandbox, endpoint: EndpointLike, pipe: EventPipe, service: EmailService
) -> None:
    capabilities: tuple[Capability, ...] = (
        Email(service=service),
        Filesystem(),
        Compactification(endpoint=endpoint),
        ConversationSnapshots(endpoint=endpoint),
        MemoryConsolidation(endpoint=endpoint),
        ArtifactRetention(),
        MaintenanceCadence(seconds=0),
        SafeScripts(scripts_dir=Path("/opt/trusted-scripts")),
        SafeScripts(socket_path=Path("/run/scripts/service.sock")),
    )
    sandbox.configure_scope("project")
    definition = orchestrator(sandbox, agent_endpoint=endpoint)
    definition.set_attributes(sandbox=sandbox, watched_agent_names={"orchestrator"})
    assert_type(definition, DeployableAgent)
    assert_type(
        librarian(sandbox, {"orchestrator"}, agent_endpoint=endpoint), DeployableAgent
    )
    for capability in capabilities:
        assert_type(
            capability.build(definition, pipe),
            tuple[Tool, ...] | tuple[Skill, ...],
        )
    assert_type(definition.build(), tuple[Agent, tuple[Agent, ...]])
    assert_type(definition.external_dependencies(), tuple[ExternalDependency, ...])


def host_entrypoint(socket: Path, scripts: Path, cwd: Path) -> None:
    from roboz.shed.tools.safe_scripts import serve_scripts

    assert_type(serve_scripts(socket_path=socket, scripts_dir=scripts, cwd=cwd), None)
