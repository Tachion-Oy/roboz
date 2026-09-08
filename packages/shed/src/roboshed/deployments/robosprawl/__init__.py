"""Project-based root and Librarian composition for RoboSprawl."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import NamedTuple, Protocol
import tempfile
from pathlib import Path

from roboshed.agents import librarian, orchestrator
from roboshed.capabilities import (
    ArtifactRetention,
    ConversationSnapshots,
    MaintenanceCadence,
    MemoryConsolidation,
)
from roboshed.workspace import Project
from roboz.agent import Agent, run_background_agent
from roboz.deployment import AgentCapability, AgentDefinition, Capability, SubAgentSpec
from roboz.runtime import EventSink, Output, default_event_sinks
from roboz.llm import EndpointLike, LLMEndpoint
from roboz.dependencies import (
    BoundDependency,
    DependencyContractError,
    DependencyRegistration,
    DependencyRoute,
    ExternalDependency,
    ExternalDependencyKind,
    LazyExternalDependency,
    bind_dependencies,
)
from roboshed.dependency_health import (
    check_executable,
    check_openai_compatible_endpoint,
)
from roboz.tooling.context import Ctx


class RoboSprawlBundle(NamedTuple):
    """The runnable root and background agents retained for host cancellation."""

    agent: Agent
    background_agents: tuple[Agent, ...] = ()


@dataclass(frozen=True, kw_only=True)
class AgenticFactory:
    """Bind a root definition and optional Librarian to a shared project.

    Construction never invokes an agent or starts a thread. The root starts
    background work through its default tools; the host owns shutdown.
    """

    project: Project
    orchestrator: AgentDefinition
    librarian: AgentDefinition | None = None
    seed_initial_messages_from_memory: bool = True
    include_cli_output: bool = False

    def agent_names(self) -> frozenset[str]:
        """Validate the foreground tree and its separation from the Librarian."""
        names = self.orchestrator.agent_names()
        if self.librarian is not None and names & self.librarian.agent_names():
            raise ValueError("librarian names must differ from foreground agents")
        return names

    def build(self, *, event_sinks: Sequence[EventSink] = ()) -> RoboSprawlBundle:
        """Return the root and background agents, with their start tools wired.

        Invoke the root directly. Retain the background tuple for cancellation
        and shutdown; constructing the bundle starts no work.
        """
        self.agent_names()

        def sinks(name: str) -> tuple[EventSink, ...]:
            return default_event_sinks(
                data_path=self.project.logs / name, include_cli=self.include_cli_output
            )

        background = (
            ()
            if self.librarian is None
            else (self.librarian.build(event_sink_factory=sinks),)
        )
        starts = tuple(
            run_background_agent(Ctx(agent=agent)).copy(
                name=f"start_background_agent_{agent.name}"
            )
            for agent in background
        )
        definition = replace(
            self.orchestrator,
            capabilities=(
                *self.orchestrator.capabilities,
                Capability(default_tools=starts),
            ),
        )
        if self.seed_initial_messages_from_memory:
            definition = replace(
                definition,
                initial_messages=(self.project.memory, *definition.initial_messages),
            )
        root = definition.build(event_sinks=event_sinks, event_sink_factory=sinks)
        return RoboSprawlBundle(agent=root, background_agents=background)


@dataclass(frozen=True, kw_only=True)
class RoboSprawl:
    """Configure the persistent project orchestrator and its memory maintenance.

    Consumers choose capabilities and endpoints. This deployment derives project
    instructions, recursive watched names, and the ordered Librarian maintenance
    capabilities. DeploymentFactory supplies the live root endpoint per run.
    """

    capabilities: Callable[[Project], Sequence[AgentCapability]]
    memory_endpoint: EndpointLike
    subagents: tuple[SubAgentSpec, ...] = ()
    interaction_mode: Output | None = Output.CLI
    instructions: Callable[[Project], str] | None = None

    def __call__(
        self, project: Project, /, *, orchestrator_endpoint: EndpointLike
    ) -> AgenticFactory:
        """Derive configured definitions without constructing clients or starting work."""
        workspace = project.workspace
        instructions = (
            f"File tool base: {workspace.resolved_root}. Project: {project.slug}. "
            f"Your writable project directory is {project.root}. "
            f"Read-only shared files are in {workspace.readonly_dir}; "
            f"writes in {workspace.shared_dir} require user confirmation."
        )
        if self.instructions is not None:
            instructions += " " + self.instructions(project)
        root = orchestrator(
            agent_endpoint=orchestrator_endpoint,
            interaction_mode=self.interaction_mode,
            subagents=self.subagents,
            capabilities=self.capabilities(project),
            instructions=instructions,
        )
        names = root.agent_names()
        return AgenticFactory(
            project=project,
            orchestrator=root,
            librarian=librarian(
                agent_endpoint=self.memory_endpoint,
                capabilities=(
                    ConversationSnapshots(project, names),
                    MemoryConsolidation(project, names),
                    ArtifactRetention(project),
                    MaintenanceCadence(project, names),
                ),
            ),
        )


class DeploymentRecipe(Protocol):
    """Supply fresh configured definitions and explicitly scoped dependencies."""

    def __call__(
        self, project: Project, /, *, orchestrator_endpoint: EndpointLike
    ) -> AgenticFactory:
        """Configure this run without invoking agents or starting background work."""
        ...


class RunFactory(Protocol):
    """Construct a runnable bundle for any host supplying events and a model route."""

    def __call__(
        self,
        project: Project,
        /,
        *,
        endpoint_getter: Callable[[], LazyExternalDependency[LLMEndpoint]],
        event_sinks: Sequence[EventSink],
    ) -> RoboSprawlBundle:
        """Return uninvoked agents; the caller owns invocation and shutdown."""
        ...


@dataclass(frozen=True)
class DeploymentFactory:
    """Create a fresh configured deployment for every host construction request.

    Recipes own dependency reuse: borrowed endpoints remain borrowed, while
    stateful scripts and capabilities can be allocated anew on each recipe call.
    No copying, implicit materialization, or disposal of supplied objects occurs.
    """

    recipe: DeploymentRecipe
    event_sink_factory: Callable[[], Sequence[EventSink]] | None = None

    def __call__(
        self,
        project: Project,
        /,
        *,
        endpoint_getter: Callable[[], LazyExternalDependency[LLMEndpoint]],
        event_sinks: Sequence[EventSink] = (),
    ) -> RoboSprawlBundle:
        """Evaluate the recipe once, then bind fresh runtime state and sinks."""
        configured = self.recipe(
            project, orchestrator_endpoint=DependencyRoute(endpoint_getter)
        )
        sinks = self.event_sink_factory() if self.event_sink_factory is not None else ()
        return configured.build(event_sinks=(*sinks, *event_sinks))


def inspect_dependencies(
    factory: RunFactory,
    *,
    project: Project,
    endpoint_getter: Callable[[], LazyExternalDependency[LLMEndpoint]],
    registrations: Sequence[DependencyRegistration] | None,
    additional_dependencies: Sequence[ExternalDependency] = (),
) -> tuple[BoundDependency, ...]:
    """Inspect an isolated build and bind its exact operational registrations."""
    with tempfile.TemporaryDirectory(prefix="deployment-dependency-inspection-") as raw:
        project = replace(
            project,
            workspace=replace(project.workspace, root=Path(raw)),
        )
        built = factory(project, endpoint_getter=endpoint_getter, event_sinks=())
        agents = (built.agent, *built.background_agents)
        discovered = [
            dependency
            for agent in agents
            for dependency in agent.external_dependencies()
        ]
        discovered.extend(additional_dependencies)
        if registrations is None:
            checks = {
                ExternalDependencyKind.EXECUTABLE: check_executable,
                ExternalDependencyKind.MODEL_ENDPOINT: check_openai_compatible_endpoint,
            }
            unique = {item.dependency_id: item for item in discovered}
            if any(item.kind not in checks for item in unique.values()):
                raise DependencyContractError(
                    "custom dependency kind requires an explicit checker registration"
                )
            registrations = tuple(
                DependencyRegistration(item.dependency_id, item.kind, checks[item.kind])
                for item in unique.values()
            )
        return bind_dependencies(discovered, registrations)
