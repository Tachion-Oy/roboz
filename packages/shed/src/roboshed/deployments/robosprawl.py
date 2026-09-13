"""RoboSprawl's persistent orchestrator and Librarian agent recipe."""

from collections.abc import Callable, Sequence
from dataclasses import replace

from roboshed.agents import librarian, orchestrator
from roboshed.sandbox import Sandbox
from roboz.agent import Agent
from roboz.deployment import AgentCapability, DeployableAgent
from roboz.llm import EndpointLike, LLMEndpoint, LLMEndpointRoute
from roboz.runtime import EventSink, Output, default_event_sinks


def robosprawl(
    sandbox: Sandbox,
    /,
    *,
    endpoint_getter: Callable[[], LLMEndpoint],
    memory_endpoint: EndpointLike,
    additional_capabilities: Sequence[AgentCapability],
    specialists: Sequence[DeployableAgent],
    interaction_mode: Output | None,
    event_sinks: Sequence[EventSink] = (),
) -> tuple[Agent, tuple[Agent, ...]]:
    """Build a fresh fixed orchestrator and Librarian for a scoped project.

    Additional capabilities follow the orchestrator's protected defaults, and
    specialists become its synchronous children. The Librarian definition and
    capability pipeline are owned entirely by this recipe. Construction creates
    fresh runtime agents and bindings but does not start them.
    """
    sandbox = replace(sandbox)
    project_slug = sandbox.scope
    if project_slug is None:
        raise ValueError("sandbox scope is not configured; call configure_scope()")
    sandbox.project_dir()

    root = orchestrator(
        sandbox,
        agent_endpoint=LLMEndpointRoute(endpoint_getter),
        subagents=tuple(specialists),
        interaction_mode=interaction_mode,
    )
    root.add_capabilities(*additional_capabilities)
    watched_agent_names = root.agent_names(include_background=False)
    root.add_background_agents(
        librarian(
            sandbox,
            watched_agent_names,
            agent_endpoint=memory_endpoint,
        )
    )
    context = (
        "## Project context\n"
        f"File tool base: {sandbox.resolved_root}\n"
        f"Project: {project_slug}\n"
        f"Writable project directory: {sandbox.project_dir()}\n"
        f"Read-only directory: {sandbox.readonly_dir}\n"
        f"Shared directory: {sandbox.shared_dir}\n"
        f"Conversation logs: {sandbox.project_logs_dir()}\n"
        f"Snapshots: {sandbox.project_snapshots_dir()}\n"
        f"Memory: {sandbox.project_memory_dir()}"
    )
    root.set_initial_messages((sandbox.project_memory_dir(), context))
    caller_sinks = tuple(event_sinks)

    def sinks(name: str) -> tuple[EventSink, ...]:
        """Create agent-specific persistence sinks for this runtime build."""
        return default_event_sinks(
            data_path=sandbox.project_logs_dir() / name,
            include_cli=False,
        )

    return root.build(event_sinks=caller_sinks, event_sink_factory=sinks)


__all__ = ["robosprawl"]
