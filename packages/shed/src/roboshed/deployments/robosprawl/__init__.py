"""Project-based root and Librarian composition for RoboSprawl."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import NamedTuple

from roboshed.workspace import Project
from roboz.agent import Agent, run_background_agent
from roboz.deployment import AgentDefinition, Capability
from roboz.runtime import EventSink, default_event_sinks
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
