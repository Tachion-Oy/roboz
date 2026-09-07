"""Data-driven agent definitions and runtime-bound capabilities."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from roboz.agent import Agent, run_subagent
from roboz.llm import EndpointLike
from roboz.runtime import EventPipe, EventSink, Output
from roboz.skill import Skill
from roboz.tooling import Tool
from roboz.tooling.context import Ctx


@dataclass(frozen=True)
class Capability:
    """Configured tools and skills that provide an agent feature.

    Tools shared between a chain and default execution retain their identity.
    Use this directly for already-bound inputs, or return one from a capability
    builder for runtime-bound inputs. Supplied objects remain caller-owned.
    """

    tools: tuple[Tool | Sequence[Tool], ...] = ()
    default_tools: tuple[Tool, ...] = ()
    skills: tuple[Skill, ...] = ()
    auto_loaded_skills: tuple[Skill, ...] = ()

    def build(
        self, pipe: EventPipe, agent_endpoint: EndpointLike | None
    ) -> "Capability":
        """Return these already-bound inputs without copying or starting work."""
        return self


class AgentCapability(Protocol):
    """A configured capability built against its owning agent's event pipe."""

    def build(self, pipe: EventPipe, agent_endpoint: EndpointLike | None) -> Capability:
        """Resolve this feature's runtime inputs without starting work."""
        ...


@dataclass(frozen=True)
class SubAgentSpec:
    """A specialist definition and its parent-facing tool identity."""

    definition: "AgentDefinition"
    tool_name: str
    tool_description: str


@dataclass(frozen=True, kw_only=True)
class AgentDefinition:
    """Configured inputs for independently constructing an agent runtime.

    All tools and skills enter through capabilities. Already-bound inputs and
    endpoint dependencies are supplied objects; callers own their reuse. Scripted
    mock endpoints that consume responses must be recreated for each run.
    """

    name: str
    agent_endpoint: EndpointLike | None
    description: str = ""
    system_prompt: str = ""
    interaction_mode: Output | None = Output.CLI
    is_agentic: bool = True
    automatic_tool_prompt: bool = True
    capabilities: tuple[AgentCapability, ...] = ()
    subagents: tuple[SubAgentSpec, ...] = ()
    initial_messages: tuple[Path | str, ...] = ()

    def agent_names(self) -> frozenset[str]:
        """Return recursive agent identities, rejecting ambiguous names."""
        names = {self.name}
        for spec in self.subagents:
            children = spec.definition.agent_names()
            if names & children:
                raise ValueError("agent names must be unique")
            names.update(children)
        return frozenset(names)

    def build(
        self,
        *,
        event_sinks: Sequence[EventSink] = (),
        event_sink_factory: Callable[[str], Sequence[EventSink]] | None = None,
    ) -> Agent:
        """Create fresh pipes, resolve capabilities, and construct the agent once.

        Caller sinks follow specialists. The optional factory supplies fresh
        agent-specific sinks by name; these are never inherited by children.
        Without supplied sinks, construction selects no output or persistence.
        """
        self.agent_names()
        pipe = EventPipe(
            event_sinks=(
                *(event_sink_factory(self.name) if event_sink_factory else ()),
                *event_sinks,
            )
        )
        contributions = [c.build(pipe, self.agent_endpoint) for c in self.capabilities]
        children = tuple(
            run_subagent(
                Ctx(
                    agent=spec.definition.build(
                        event_sinks=event_sinks, event_sink_factory=event_sink_factory
                    )
                )
            ).copy(name=spec.tool_name, description=spec.tool_description)
            for spec in self.subagents
        )
        return Agent(
            name=self.name,
            description=self.description,
            agent_endpoint=self.agent_endpoint,
            event_pipe=pipe,
            interaction_mode=self.interaction_mode,
            is_agentic=self.is_agentic,
            automatic_tool_prompt=self.automatic_tool_prompt,
            system_prompt=self.system_prompt,
            tools=[*(tool for c in contributions for tool in c.tools), *children],
            default_tools=[tool for c in contributions for tool in c.default_tools],
            skills=[skill for c in contributions for skill in c.skills],
            auto_loaded_skills=[
                skill for c in contributions for skill in c.auto_loaded_skills
            ],
            initial_messages=self.initial_messages,
        )


__all__ = [
    "AgentCapability",
    "AgentDefinition",
    "Capability",
    "SubAgentSpec",
]
