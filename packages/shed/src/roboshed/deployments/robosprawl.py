"""RoboSprawl's persistent orchestrator and Librarian agent recipe."""

from collections.abc import Callable, Sequence
from dataclasses import replace

from roboshed.agents import librarian, orchestrator
from roboshed.sandbox import Sandbox
from roboz.agent import Agent
from roboz.dependencies import DependencyRoute, LazyExternalDependency
from roboz.deployment import AgentCapability, DeployableAgent
from roboz.llm import EndpointLike, LLMEndpoint
from roboz.runtime import EventSink, Output, default_event_sinks


class RoboSprawl:
    """The existing recipe with explicit inputs supplied before build."""

    def __init__(self) -> None:
        """Declare missing inputs and defaults without starting work."""
        self._sandbox: Sandbox | None = None
        self._endpoint_getter: (
            Callable[[], LazyExternalDependency[LLMEndpoint]] | None
        ) = None
        self._memory_endpoint: EndpointLike | None = None
        self._additional_capabilities: tuple[AgentCapability, ...] = ()
        self._specialists: tuple[DeployableAgent, ...] = ()
        self._interaction_mode: Output | None = None
        self._event_sinks: tuple[EventSink, ...] = ()

    def set_sandbox(self, sandbox: Sandbox) -> None:
        """Capture the caller's sandbox layout and selected project."""
        self._sandbox = replace(sandbox)

    def set_endpoint_getter(
        self, getter: Callable[[], LazyExternalDependency[LLMEndpoint]]
    ) -> None:
        """Supply the orchestrator's live model selection."""
        self._endpoint_getter = getter

    def set_memory_endpoint(self, endpoint: EndpointLike) -> None:
        """Supply the Librarian's independent model endpoint."""
        self._memory_endpoint = endpoint

    def set_additional_capabilities(
        self, capabilities: Sequence[AgentCapability]
    ) -> None:
        """Set additions that follow the orchestrator's fixed defaults."""
        self._additional_capabilities = tuple(capabilities)

    def set_specialists(self, specialists: Sequence[DeployableAgent]) -> None:
        """Set the orchestrator's synchronous child definitions."""
        self._specialists = tuple(specialists)

    def set_interaction_mode(self, mode: Output | None) -> None:
        """Select the orchestrator's user interaction mode."""
        self._interaction_mode = mode

    def set_event_sinks(self, sinks: Sequence[EventSink]) -> None:
        """Set the caller's foreground event listeners."""
        self._event_sinks = tuple(sinks)

    def build(self) -> tuple[Agent, tuple[Agent, ...]]:
        """Complete the existing recipe and build fresh agents without invoking."""
        sandbox = self._sandbox
        endpoint_getter = self._endpoint_getter
        memory_endpoint = self._memory_endpoint
        missing = [
            name
            for name, value in (
                ("sandbox", sandbox),
                ("endpoint_getter", endpoint_getter),
                ("memory_endpoint", memory_endpoint),
            )
            if value is None
        ]
        if missing:
            raise ValueError(f"RoboSprawl is missing: {', '.join(missing)}")

        assert sandbox is not None
        assert endpoint_getter is not None
        assert memory_endpoint is not None
        sandbox = replace(sandbox)
        project_slug = sandbox.scope
        if project_slug is None:
            raise ValueError("sandbox scope is not configured; call configure_scope()")
        sandbox.project_dir()

        root = orchestrator(
            sandbox,
            agent_endpoint=DependencyRoute(endpoint_getter),
            subagents=self._specialists,
            interaction_mode=self._interaction_mode,
        )
        root.add_capabilities(*self._additional_capabilities)
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
        caller_sinks = tuple(self._event_sinks)

        def sinks(name: str) -> tuple[EventSink, ...]:
            """Create agent-specific persistence sinks for this runtime build."""
            return default_event_sinks(
                data_path=sandbox.project_logs_dir() / name,
                include_cli=False,
            )

        return root.build(event_sinks=caller_sinks, event_sink_factory=sinks)


robosprawl = RoboSprawl
__all__ = ["RoboSprawl", "robosprawl"]
