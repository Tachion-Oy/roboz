"""Configurable agent definitions and runtime-bound capabilities."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from roboz.agent import (
    Agent,
    AgentMode,
    BackgroundAgentContext,
    run_background_agent,
    run_subagent,
)
from roboz.dependencies import ExternalDependency
from roboz.llm import EndpointLike
from roboz.runtime import EventPipe, EventSink
from roboz.skill import Skill
from roboz.tooling import HasExternalDependencies, Tool

type RequiredAttributeType = type[object] | tuple[type[object], ...]
type RequiredAttributes = Mapping[str, RequiredAttributeType]


class AgentCapability(Protocol):
    """A configured feature that binds its tools to one agent runtime."""

    @property
    def required_attributes(self) -> RequiredAttributes:
        """Declare configuration values required from the owning agent."""
        ...

    def build(self, agent: "DeployableAgent", pipe: EventPipe) -> "Capability":
        """Bind configured tools using the owning agent and its fresh pipe."""
        ...


@dataclass(frozen=True)
class Capability(AgentCapability):
    """Already-bound tools and skills that provide an agent feature.

    ``default_tools`` are ordered, repeatedly scheduled chain roots. A tool in
    ``tools`` may declare ``chained_to`` with one of those defaults as its parent,
    and scheduling resumes with the next default when the downstream branch ends.
    Defaults cannot themselves declare ``chained_to``. Tools shared between
    ``tools`` and ``default_tools`` retain their identity. Supplied objects remain
    caller-owned and require no owner configuration.
    """

    tools: tuple[Tool | Sequence[Tool], ...] = ()
    default_tools: tuple[Tool, ...] = ()
    skills: tuple[Skill, ...] = ()
    auto_loaded_skills: tuple[Skill, ...] = ()

    @property
    def required_attributes(self) -> RequiredAttributes:
        """Declare that already-bound objects need no owner configuration."""
        return {}

    def build(self, agent: "DeployableAgent", pipe: EventPipe) -> "Capability":
        """Return these already-bound inputs without copying or starting work."""
        return self


class DeployableAgent(HasExternalDependencies):
    """Mutable configuration for one agent and its attached child graph.

    Constructor capabilities are fixed defaults. Later capabilities and child
    definitions can only be appended. Runtime-specific values can be supplied
    after declaration with set_attributes(); they belong only to this node.
    Each build validates the complete graph and creates fresh runtime agents,
    pipes, and capability bindings without retaining execution state here.
    """

    def __init__(
        self,
        *,
        name: str,
        description: str = "",
        system_prompt: str = "",
        mode: AgentMode = AgentMode.STEERABLE,
        automatic_tool_prompt: bool = True,
        default_capabilities: Sequence[AgentCapability] = (),
        subagents: Sequence["DeployableAgent"] = (),
        background_agents: Sequence["DeployableAgent"] = (),
    ) -> None:
        """Configure identity, behavior, fixed capabilities, and initial children."""
        self._name = name
        self._description = description
        self._system_prompt = system_prompt
        try:
            self._mode = AgentMode(mode)
        except ValueError as error:
            raise ValueError(f"Unsupported agent mode: {mode!r}") from error
        self._automatic_tool_prompt = automatic_tool_prompt
        self._default_capabilities = tuple(default_capabilities)
        self._additional_capabilities: list[AgentCapability] = []
        self._subagents: list[DeployableAgent] = []
        self._background_agents: list[DeployableAgent] = []
        self._agent_endpoint: EndpointLike | None = None
        self._initial_messages: tuple[Path | str, ...] = ()
        self._attributes: dict[str, object] = {}
        self.add_subagents(*subagents)
        self.add_background_agents(*background_agents)

    @property
    def name(self) -> str:
        """Return this agent's runtime name."""
        return self._name

    @property
    def description(self) -> str:
        """Return this agent's delegation description."""
        return self._description

    @property
    def system_prompt(self) -> str:
        """Return this agent's system prompt."""
        return self._system_prompt

    @property
    def mode(self) -> AgentMode:
        """Return this agent's execution and interaction mode."""
        return self._mode

    @property
    def automatic_tool_prompt(self) -> bool:
        """Return whether tool instructions are added automatically."""
        return self._automatic_tool_prompt

    @property
    def default_capabilities(self) -> tuple[AgentCapability, ...]:
        """Return the fixed capabilities supplied at construction."""
        return self._default_capabilities

    @property
    def additional_capabilities(self) -> tuple[AgentCapability, ...]:
        """Return capabilities appended after the fixed defaults."""
        return tuple(self._additional_capabilities)

    @property
    def capabilities(self) -> tuple[AgentCapability, ...]:
        """Return fixed and additional capabilities in build order."""
        return (*self._default_capabilities, *self._additional_capabilities)

    @property
    def subagents(self) -> tuple["DeployableAgent", ...]:
        """Return attached synchronous child definitions."""
        return tuple(self._subagents)

    @property
    def background_agents(self) -> tuple["DeployableAgent", ...]:
        """Return attached background child definitions."""
        return tuple(self._background_agents)

    @property
    def agent_endpoint(self) -> EndpointLike | None:
        """Return the endpoint configured for this node."""
        return self._agent_endpoint

    @property
    def initial_messages(self) -> tuple[Path | str, ...]:
        """Return configured initial message sources."""
        return self._initial_messages

    def __getattr__(self, name: str) -> object:
        """Expose explicitly supplied capability attributes for owner reads."""
        try:
            attributes: dict[str, object] = object.__getattribute__(self, "_attributes")
            return attributes[name]
        except (AttributeError, KeyError):
            raise AttributeError(name) from None

    def set_agent_endpoint(self, endpoint: EndpointLike | None) -> None:
        """Set or defer this node's model endpoint."""
        self._agent_endpoint = endpoint

    def set_initial_messages(self, messages: Sequence[Path | str]) -> None:
        """Replace this node's initial message sources before a build."""
        self._initial_messages = tuple(messages)

    def set_attributes(self, **values: object) -> None:
        """Set capability-specific values without altering agent structure.

        Existing capability attributes may be updated for a later build. Public
        structural attributes, methods, and all private names are protected.
        """
        for name in values:
            if name.startswith("_") or any(
                name in cls.__dict__ for cls in type(self).__mro__
            ):
                raise ValueError(
                    f"capability attribute cannot overwrite agent structure: {name!r}"
                )
        self._attributes.update(values)

    def add_capabilities(self, *capabilities: AgentCapability) -> None:
        """Append capabilities after this node's fixed defaults."""
        self._additional_capabilities.extend(capabilities)

    def add_subagents(self, *subagents: "DeployableAgent") -> None:
        """Append synchronous child definitions."""
        self._subagents.extend(self._checked_agents(subagents))

    def add_background_agents(self, *agents: "DeployableAgent") -> None:
        """Append background child definitions."""
        self._background_agents.extend(self._checked_agents(agents))

    @staticmethod
    def _checked_agents(
        agents: Sequence["DeployableAgent"],
    ) -> tuple["DeployableAgent", ...]:
        checked = tuple(agents)
        if any(not isinstance(agent, DeployableAgent) for agent in checked):
            raise TypeError("child definitions must be DeployableAgent instances")
        return checked

    def agent_names(self, *, include_background: bool = True) -> frozenset[str]:
        """Validate all names and return identities in the selected branches.

        Excluding background agents also excludes their descendants, for
        foreground-only consumers such as conversation maintenance.
        """
        if not include_background:
            self.agent_names()
        return frozenset(self._collect_names(include_background, frozenset()))

    def _collect_names(
        self, include_background: bool, ancestors: frozenset[int]
    ) -> set[str]:
        identity = id(self)
        if identity in ancestors:
            raise ValueError("agent graph must not contain cycles")
        ancestors = ancestors | {identity}
        names = {self.name}
        definitions = self.subagents
        if include_background:
            definitions += self.background_agents
        for definition in definitions:
            children = definition._collect_names(include_background, ancestors)
            if names & children:
                raise ValueError("agent names must be unique")
            names.update(children)
        return names

    def validate(self) -> None:
        """Validate names and every capability requirement in the full graph."""
        self.agent_names()
        errors = list(self._configuration_errors())
        if not errors:
            return
        details = "\n".join(f"- {error}" for error in errors)
        raise ValueError(f"invalid agent configuration:\n{details}")

    def _configuration_errors(self) -> Iterator[str]:
        """Yield capability requirement failures in stable graph order."""
        for agent in self._walk():
            yield from _capability_errors(agent)

    def _walk(self) -> tuple["DeployableAgent", ...]:
        definitions: list[DeployableAgent] = [self]
        walked: list[DeployableAgent] = []
        while definitions:
            agent = definitions.pop(0)
            walked.append(agent)
            definitions.extend((*agent.subagents, *agent.background_agents))
        return tuple(walked)

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        """Build an unstarted graph and inspect its tools' current resources.

        Use normal configuration validation and capability construction with no
        event sinks. The root agent includes its foreground and background
        descendants through their bound tool contexts. No agent is invoked and
        no resource is checked or materialized by this method.

        Each call builds fresh runtime state. Custom capability builders run as
        usual, including any construction effects they introduce; inspection
        does not provide filesystem isolation.
        """
        agent, _ = self.build()
        return agent.external_dependencies()

    def build(
        self,
        *,
        event_sinks: Sequence[EventSink] = (),
        event_sink_factory: Callable[[str], Sequence[EventSink]] | None = None,
    ) -> tuple[Agent, tuple[Agent, ...]]:
        """Validate and build a fresh root with every background handle.

        Caller sinks reach foreground branches only. The optional factory gives
        each named agent its own fresh sinks. Construction starts no agents.
        """
        self.validate()
        return self._build(
            event_sinks=event_sinks, event_sink_factory=event_sink_factory
        )

    def _build(
        self,
        *,
        event_sinks: Sequence[EventSink] = (),
        event_sink_factory: Callable[[str], Sequence[EventSink]] | None = None,
    ) -> tuple[Agent, tuple[Agent, ...]]:
        pipe = EventPipe(
            event_sinks=(
                *(event_sink_factory(self.name) if event_sink_factory else ()),
                *event_sinks,
            )
        )
        contributions = [
            capability.build(self, pipe) for capability in self.capabilities
        ]

        tools = [tool for contribution in contributions for tool in contribution.tools]
        default_tools = [tool for c in contributions for tool in c.default_tools]
        background_agents: list[Agent] = []
        for definition in self.subagents:
            child, descendants = definition._build(
                event_sinks=event_sinks, event_sink_factory=event_sink_factory
            )
            tools.append(
                run_subagent(child).copy(
                    name=child.name,
                    description=child.description,
                )
            )
            background_agents.extend(descendants)
        for definition in self.background_agents:
            child, descendants = definition._build(
                event_sink_factory=event_sink_factory
            )
            default_tools.append(
                run_background_agent(BackgroundAgentContext(agent=child)).copy(
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
            mode=self.mode,
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


_MISSING = object()


def _type_name(expected: RequiredAttributeType) -> str:
    types = expected if isinstance(expected, tuple) else (expected,)
    return " or ".join(item.__name__ for item in types)


def _capability_errors(agent: DeployableAgent) -> Iterator[str]:
    """Yield unmet attribute requirements for one configuration node."""
    for capability in agent.capabilities:
        for name, expected in capability.required_attributes.items():
            error = _required_attribute_error(agent, capability, name, expected)
            if error is not None:
                yield error


def _required_attribute_error(
    agent: DeployableAgent,
    capability: AgentCapability,
    name: str,
    expected: RequiredAttributeType,
) -> str | None:
    """Describe one unmet requirement, or return None when it is satisfied."""
    value = getattr(agent, name, _MISSING)
    if value is not _MISSING and value is not None and isinstance(value, expected):
        return None

    owner = f"agent {agent.name!r}, capability {type(capability).__name__}"
    requirement = f"attribute {name!r} must be {_type_name(expected)}"
    if value is _MISSING:
        actual = "it is missing"
    elif value is None:
        actual = "it is None"
    else:
        actual = f"got {type(value).__name__}"
    return f"{owner}: {requirement}; {actual}"


__all__ = [
    "AgentCapability",
    "Capability",
    "DeployableAgent",
    "RequiredAttributeType",
    "RequiredAttributes",
]
