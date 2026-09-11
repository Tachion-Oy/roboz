"""RoboSprawl's persistent orchestrator and Librarian deployment recipe."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from roboshed.agents import librarian, orchestrator
from roboshed.deployments import Deployment
from roboshed.sandbox import Sandbox
from roboz.dependencies import DependencyRoute, LazyExternalDependency
from roboz.deployment import AgentCapability, DeployableAgent
from roboz.llm import EndpointLike, LLMEndpoint
from roboz.runtime import EventSink, Output


@dataclass(frozen=True, kw_only=True)
class RoboSprawl:
    """Configured choices for a persistent orchestrator with memory maintenance.

    Each call creates a fresh deployment from the supplied scoped sandbox.
    Capabilities, specialist definitions, and endpoints remain caller-owned.
    Construction does not create directories, materialize endpoints, or run agents.
    """

    memory_endpoint: EndpointLike
    additional_capabilities: Sequence[AgentCapability]
    subagents: Sequence[DeployableAgent]
    interaction_mode: Output | None

    def __post_init__(self) -> None:
        """Capture the configured capability and specialist sequences."""
        object.__setattr__(
            self, "additional_capabilities", tuple(self.additional_capabilities)
        )
        object.__setattr__(self, "subagents", tuple(self.subagents))

    def __call__(
        self,
        sandbox: Sandbox,
        project_slug: str,
        /,
        *,
        endpoint_getter: Callable[[], LazyExternalDependency[LLMEndpoint]],
        event_sinks: Sequence[EventSink] = (),
    ) -> Deployment:
        """Construct the scoped graph; the caller owns build and execution.

        The sandbox must already be scoped to the supplied project. Use that
        same instance for file permissions, memory maintenance, and persistence.
        The root follows the endpoint getter when the selected model changes;
        the Librarian uses the separately configured memory endpoint.
        """
        if sandbox.scope != project_slug:
            raise ValueError("deployment project must match the sandbox scope")
        names = {"orchestrator"}
        for specialist in self.subagents:
            names.update(specialist.agent_names(include_background=False))
        lib = librarian(sandbox, names, agent_endpoint=self.memory_endpoint)
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
        return Deployment(
            agent=orchestrator(
                sandbox,
                agent_endpoint=DependencyRoute(endpoint_getter),
                subagents=self.subagents,
                background_agents=(lib,),
                interaction_mode=self.interaction_mode,
                initial_messages=(sandbox.project_memory_dir(), context),
            ),
            sandbox=sandbox,
            additional_capabilities=tuple(self.additional_capabilities),
            event_sinks=list(event_sinks),
        )


__all__ = ["RoboSprawl"]
