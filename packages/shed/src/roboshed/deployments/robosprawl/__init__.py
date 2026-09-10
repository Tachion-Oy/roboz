"""RoboSprawl project policy expressed as a configured Deployment."""

from collections.abc import Callable, Sequence
from dataclasses import replace

from roboshed.agents import librarian, orchestrator
from roboshed.capabilities import (
    ArtifactRetention,
    ConversationSnapshots,
    MaintenanceCadence,
    MemoryConsolidation,
)
from roboshed.sandbox import PermissionPolicy, Sandbox
from roboz.deployment import AgentCapability, DeployableAgent, Deployment
from roboz.llm import EndpointLike
from roboz.runtime import Output, default_event_sinks


def _default_librarian_capabilities(
    sandbox: Sandbox, project_slug: str, names: frozenset[str]
) -> tuple[AgentCapability, ...]:
    return (
        ConversationSnapshots(sandbox, project_slug, names),
        MemoryConsolidation(sandbox, project_slug, names),
        ArtifactRetention(sandbox, project_slug),
        MaintenanceCadence(sandbox, project_slug, names),
    )


def robosprawl(
    sandbox: Sandbox,
    project_slug: str,
    /,
    *,
    orchestrator_endpoint: EndpointLike,
    capabilities: Sequence[
        AgentCapability | Callable[[PermissionPolicy], AgentCapability]
    ],
    memory_endpoint: EndpointLike,
    librarian_capabilities: Callable[
        [Sandbox, str, frozenset[str]], Sequence[AgentCapability]
    ] = _default_librarian_capabilities,
    subagents: Sequence[DeployableAgent] = (),
    background_agents: Sequence[DeployableAgent] = (),
    interaction_mode: Output | None = Output.CLI,
    project_context: str = (
        "## Project context\n"
        "File tool base: {sandbox.resolved_root}\n"
        "Project: {project_slug}\n"
        "Writable project directory: {project_root}\n"
        "Read-only directory: {sandbox.readonly_dir}\n"
        "Shared directory: {sandbox.shared_dir}\n"
        "Conversation logs: {project_logs}\n"
        "Snapshots: {project_snapshots}\n"
        "Memory: {project_memory}"
    ),
    seed_initial_messages_from_memory: bool = True,
    include_cli_output: bool = False,
) -> Deployment:
    """Configure a project orchestrator and Librarian without building or running them.

    Bind capability factories to this project's permissions. The maintenance
    callback receives the sandbox, slug, and recursive foreground names; its
    default selects snapshots, consolidation, retention, and a 120-second cadence.
    Register the Librarian as an ordinary background child in the root's default
    execution sequence. Project memory precedes other initial context, and each
    agent gets its own persistence sinks when the deployment is built.
    """
    permissions = sandbox.permissions(project_slug)
    root = orchestrator(
        agent_endpoint=orchestrator_endpoint,
        interaction_mode=interaction_mode,
        subagents=subagents,
        background_agents=background_agents,
        capabilities=tuple(
            capability(permissions) if callable(capability) else capability
            for capability in capabilities
        ),
        instructions=project_context.format(
            sandbox=sandbox,
            project_slug=project_slug,
            project_root=sandbox.project_dir(project_slug),
            project_logs=sandbox.project_logs_dir(project_slug),
            project_snapshots=sandbox.project_snapshots_dir(project_slug),
            project_memory=sandbox.project_memory_dir(project_slug),
        ),
    )
    maintenance = librarian(
        agent_endpoint=memory_endpoint,
        capabilities=librarian_capabilities(
            sandbox, project_slug, root.agent_names(include_background=False)
        ),
    )
    return Deployment(
        root=replace(
            root,
            background_agents=(*root.background_agents, maintenance),
        ),
        initial_messages=(sandbox.project_memory_dir(project_slug),)
        if seed_initial_messages_from_memory
        else (),
        event_sink_factory=lambda name: default_event_sinks(
            data_path=sandbox.project_logs_dir(project_slug) / name,
            include_cli=include_cli_output,
        ),
    )


__all__ = ["robosprawl"]
