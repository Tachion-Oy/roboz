"""A task-oriented file assistant preset using the shared agent definition."""

from collections.abc import Sequence
from pathlib import Path

from roboshed.capabilities import FileCommands, FileEditing
from roboshed.workspace import Project
from roboz import Agent, stop
from roboz.deployment import AgentCapability, AgentDefinition, Capability
from roboz.llm import EndpointLike
from roboz.runtime import EventSink, Output, default_event_sinks

from .workspace import WorkspacePermissions


def build_assistant(
    *,
    endpoint: EndpointLike,
    project: Project,
    permissions: WorkspacePermissions | None = None,
    capabilities: Sequence[AgentCapability] = (),
    event_sinks: Sequence[EventSink] = (),
    name: str = "assistant",
    system_prompt: str = "Complete the user's task using the available tools, then call stop.",
    initial_messages: Sequence[Path | str] = (),
    interaction_mode: Output = Output.CLI,
) -> Agent:
    """Build a task-oriented file assistant with explicit additional capabilities.

    Capabilities supply all extensions, including their tools and instructions.
    The default tool policy allows this project only. Hosts may supply a different
    policy. Construction does not create directories or start the agent.
    """
    policy = (
        permissions
        if permissions is not None
        else WorkspacePermissions.local(project.root)
    )
    return AgentDefinition(
        name=name,
        agent_endpoint=endpoint,
        system_prompt=f"{system_prompt}\nFile tool base: {policy.base.resolve()}",
        initial_messages=tuple(initial_messages),
        interaction_mode=interaction_mode,
        capabilities=(
            Capability(tools=(stop,)),
            FileCommands(policy),
            FileEditing(policy),
            *capabilities,
        ),
    ).build(
        event_sinks=event_sinks,
        event_sink_factory=lambda agent_name: default_event_sinks(
            data_path=project.logs / agent_name, include_cli=False
        ),
    )


__all__ = ["build_assistant"]
