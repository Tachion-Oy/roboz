"""Start parallel subagents that share notes while pursuing a common task."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import partial
from threading import Lock

from pydantic import ConfigDict, Field

from roboz.agent._execution_context import (
    get_active_agent_endpoint,
    get_active_agent_stack,
)
from roboz.agent._prompts import get_subagent_system_prompt
from roboz.agent.background_agent import BackgroundAgentContext, run_background_agent
from roboz.agent.core import Agent
from roboz.dependencies import ExternalDependency, dedupe_external_dependencies
from roboz.models import AgentMode, Empty, Message, Str, Strs
from roboz.runtime.events import PipeEvent, RunLifecycleEvent
from roboz.runtime.pipe import EventPipe
from roboz.runtime.sinks import default_event_sinks
from roboz.skill import Skill
from roboz.tooling.context import HasExternalDependencies
from roboz.tooling.core import Tool
from roboz.tooling.decorators import factory
from roboz.tools.control import stop_after


class SharedNotes:
    """Append-only notes that cooperating agents post and read under one lock."""

    def __init__(self) -> None:
        """Start with no notes and no readers."""
        self._lock = Lock()
        self._notes: list[tuple[str, str]] = []
        self._seen: dict[str, int] = {}

    def post(self, author: str, text: str) -> None:
        """Append one note under its author's name."""
        with self._lock:
            self._notes.append((author, text))

    def unread(self, reader: str) -> list[str]:
        """Return the notes others posted since this reader last asked."""
        with self._lock:
            new = self._notes[self._seen.get(reader, 0) :]
            self._seen[reader] = len(self._notes)
        return [f"{author}: {text}" for author, text in new if author != reader]


@dataclass(kw_only=True)
class SubagentsContext(HasExternalDependencies):
    """One team of identical subagents, owned by the agent whose pipe this is.

    Members are called ``<name>_1``, ``<name>_2`` and so on. Each holds the
    given tools, loads the given skills when it starts, saves its conversation
    in a folder of that name next to its owner's, and is cancelled when the
    owner's run stops.
    """

    name: str
    pipe: EventPipe
    tools: Sequence[Tool | Sequence[Tool]] = ()
    skills: Sequence[Skill] = ()
    team: list[BackgroundAgentContext] = field(default_factory=list)
    notes: SharedNotes = field(default_factory=SharedNotes)

    def __post_init__(self) -> None:
        """Watch the owner's run so that its end stops the team."""
        self.pipe.add_sink(self._cancel_when_owner_stops)

    def _cancel_when_owner_stops(self, event: PipeEvent) -> None:
        if isinstance(event, RunLifecycleEvent) and event.kind == "stopped":
            self.cancel()

    def cancel(self) -> None:
        """Ask every member of the current team to stop."""
        for member in self.team:
            member.agent.pipe.cancel()

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        """Report the dependencies of the members' tools and skills without starting one."""
        tools = [*Tool.to_tool_list(self.tools)]
        tools += [tool for skill in self.skills for tool in skill.tools]
        return dedupe_external_dependencies(
            dependency for tool in tools for dependency in tool.external_dependencies()
        )


class SubagentGoals(Empty):
    """A common task and one sub-goal for each subagent to start."""

    task: str = Field(description="The common task every subagent works toward.")
    subgoals: list[str] = Field(
        description="One distinct sub-goal per subagent; one subagent starts for each."
    )
    model_config = ConfigDict(extra="forbid")


@factory
def share_note(input: Str, messages: list[Message], ctx: SubagentsContext) -> Str:
    """Share a finding with the other agents working on the common task.

    Share a useful source, a dead end, or something that belongs to another
    agent's sub-goal. Keep it short.
    """
    # The last name on the stack is the agent making this call.
    ctx.notes.post(get_active_agent_stack()[-1], input.value)
    return Str(value="Shared.")


@factory
def read_shared_notes(
    input: Empty, messages: list[Message], ctx: SubagentsContext
) -> Strs:
    """Read what the other agents shared since you last read."""
    new = ctx.notes.unread(get_active_agent_stack()[-1])
    return Strs(items=new or ["No new notes."])


@factory
def start_subagents(
    input: SubagentGoals, messages: list[Message], ctx: SubagentsContext
) -> Strs:
    """Start one subagent per sub-goal to work in parallel toward a common task.

    The call returns at once and they work in the background, sharing findings
    with each other. Read their notes, results and final status with
    `read_shared_notes`. Starting again cancels subagents that are still
    running and discards their notes, so read those first.
    """
    ctx.cancel()
    notes = ctx.notes = SharedNotes()
    names = [f"{ctx.name}_{number}" for number in range(1, len(input.subgoals) + 1)]
    roster = [f"{name}: {goal}" for name, goal in zip(names, input.subgoals)]
    owner_logs = ctx.pipe.data_path

    def announce(name: str, event: PipeEvent) -> None:
        if isinstance(event, RunLifecycleEvent) and event.kind == "stopped":
            notes.post(name, f"stopped ({event.status})")

    share = share_note(ctx)
    ctx.team = [
        BackgroundAgentContext(
            agent=Agent(
                name=name,
                mode=AgentMode.AUTONOMOUS,
                system_prompt=get_subagent_system_prompt(
                    name=name, task=input.task, subgoal=goal, team=roster
                ),
                agent_endpoint=get_active_agent_endpoint(),
                # The stop command posts the final result before it stops.
                tools=[*ctx.tools, share, read_shared_notes(ctx), *stop_after(share)],
                auto_loaded_skills=ctx.skills,
                event_sinks=[
                    partial(announce, name),
                    # Each member logs in its own folder next to its owner's.
                    *default_event_sinks(
                        data_path=owner_logs and owner_logs.parent / name,
                        include_cli=False,
                    ),
                ],
            )
        )
        for name, goal in zip(names, input.subgoals)
    ]
    report = []
    for member, line in zip(ctx.team, roster):
        try:
            run_background_agent(member)(Empty(), messages)
        except RuntimeError:
            line = f"{member.agent.name}: did not start"
        report.append(line)
    return Strs(items=report)


__all__ = [
    "SharedNotes",
    "SubagentGoals",
    "SubagentsContext",
    "read_shared_notes",
    "share_note",
    "start_subagents",
]
