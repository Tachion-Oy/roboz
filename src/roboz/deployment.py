"""Data-driven agent definitions and runtime-bound capabilities."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from roboz.agent import Agent, run_background_agent, run_subagent
from roboz.llm import EndpointLike
from roboz.runtime import EventPipe, EventSink, Output
from roboz.skill import Skill
from roboz.tooling import Tool
from roboz.tooling.context import Ctx


class AgentCapability(Protocol):
    """A configured feature that binds its own tools to an agent runtime.

    Store tool-specific endpoints on the capability. The build input supplies
    the owning agent's endpoint as a default, independently of its event pipe.
    """

    def build(
        self, pipe: EventPipe, *, default_endpoint: EndpointLike | None
    ) -> "Capability":
        """Bind configured tools, using the supplied default for unset endpoints."""
        ...


@dataclass(frozen=True)
class Capability(AgentCapability):
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
        self, pipe: EventPipe, *, default_endpoint: EndpointLike | None
    ) -> "Capability":
        """Return these already-bound inputs without copying or starting work."""
        return self


@dataclass(frozen=True, kw_only=True)
class DeployableAgent:
    """One agent's configuration, including its sub-agents and background agents.

    All tools and skills enter through capabilities. Already-bound inputs and
    endpoint dependencies are supplied objects; callers own their reuse. Scripted
    mock endpoints that consume responses must be recreated for each run.
    Configure definitions before building; do not mutate them concurrently with
    construction. Each capability selects its tool endpoints, using the agent
    endpoint as the default. Endpoint objects remain independent of runtime controls.
    Sub-agents become named delegation tools. Background agents are started
    through default tools when their parent runs. Both slots contain the same
    recursive definition type.
    """

    name: str
    agent_endpoint: EndpointLike | None
    description: str = ""
    system_prompt: str = ""
    interaction_mode: Output | None = Output.CLI
    is_agentic: bool = True
    automatic_tool_prompt: bool = True
    capabilities: tuple[AgentCapability, ...] = ()
    subagents: tuple["DeployableAgent", ...] = ()
    background_agents: tuple["DeployableAgent", ...] = ()
    initial_messages: tuple[Path | str, ...] = ()

    def agent_names(self, *, include_background: bool = True) -> frozenset[str]:
        """Validate all names and return identities in the selected branches.

        Excluding background agents also excludes their descendants, for
        foreground-only consumers such as conversation maintenance.
        """
        if not include_background:
            self.agent_names()
        names = {self.name}
        definitions = self.subagents
        if include_background:
            definitions += self.background_agents
        for definition in definitions:
            children = definition.agent_names(include_background=include_background)
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

        Caller sinks follow synchronous children. The optional factory supplies fresh
        agent-specific sinks by name; these are never inherited by children.
        Without supplied sinks, construction selects no output or persistence.
        Use Deployment.build() to retain background agents for host control.
        """
        self.agent_names()
        return self._build(event_sinks, event_sink_factory)[0]

    def _build(
        self,
        event_sinks: Sequence[EventSink],
        event_sink_factory: Callable[[str], Sequence[EventSink]] | None,
    ) -> tuple[Agent, tuple[Agent, ...]]:
        """Build a validated graph and retain every background invocation target."""
        pipe = EventPipe(
            event_sinks=(
                *(event_sink_factory(self.name) if event_sink_factory else ()),
                *event_sinks,
            )
        )
        contributions = []
        for capability in self.capabilities:
            contributions.append(
                capability.build(pipe, default_endpoint=self.agent_endpoint)
            )

        tools = [tool for contribution in contributions for tool in contribution.tools]
        default_tools = [tool for c in contributions for tool in c.default_tools]
        background_agents: list[Agent] = []
        for definition in self.subagents:
            child, descendants = definition._build(event_sinks, event_sink_factory)
            tools.append(
                run_subagent(Ctx(agent=child)).copy(
                    name=child.name,
                    description=child.description,
                )
            )
            background_agents.extend(descendants)
        for definition in self.background_agents:
            child, descendants = definition._build((), event_sink_factory)
            default_tools.append(
                run_background_agent(Ctx(agent=child)).copy(
                    name=f"start_background_agent_{child.name}",
                )
            )
            background_agents.append(child)
            background_agents.extend(descendants)

        agent = Agent(
            name=self.name,
            description=self.description,
            agent_endpoint=self.agent_endpoint,
            event_pipe=pipe,
            interaction_mode=self.interaction_mode,
            is_agentic=self.is_agentic,
            automatic_tool_prompt=self.automatic_tool_prompt,
            system_prompt=self.system_prompt,
            tools=tools,
            default_tools=default_tools,
            skills=[skill for c in contributions for skill in c.skills],
            auto_loaded_skills=[
                skill for c in contributions for skill in c.auto_loaded_skills
            ],
            initial_messages=self.initial_messages,
        )
        return agent, tuple(background_agents)


__all__ = [
    "AgentCapability",
    "Capability",
    "DeployableAgent",
]
