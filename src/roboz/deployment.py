"""Configurable agent definitions and runtime-bound capabilities."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import ClassVar, Literal

from roboz.agent import (
    Agent,
    AgentMode,
    BackgroundAgentContext,
    run_background_agent,
    run_nested_agent,
)
from roboz.dependencies import ExternalDependency, dedupe_external_dependencies
from roboz.llm import EndpointLike
from roboz.runtime import EventPipe, EventSink
from roboz.skill import Skill
from roboz.tooling import HasExternalDependencies, Tool

type RequiredAttributeType = type[object] | tuple[type[object], ...]
type RequiredAttributes = Mapping[str, RequiredAttributeType]


class SkillLoading(StrEnum):
    """Choose when an intact skill's instructions and tools become available."""

    ON_DEMAND = "on_demand"
    AUTOMATIC = "automatic"


@dataclass(frozen=True)
class CapabilityLabel:
    """Identify one whole capability and whether its owner may deselect it."""

    name: str
    selectable: bool = False

    def __post_init__(self) -> None:
        """Require a usable selection name before registering the capability."""
        if not self.name.strip():
            raise ValueError("capability name must be nonempty")


@dataclass(frozen=True)
class ToolLabel(CapabilityLabel):
    """Expose a tool or chain; default roots run in declaration order."""

    kind: ClassVar[Literal["tool"]] = "tool"
    default: bool = False


@dataclass(frozen=True)
class SkillLabel(CapabilityLabel):
    """Describe an opaque skill's declared loading behavior."""

    kind: ClassVar[Literal["skill"]] = "skill"
    loading: SkillLoading = SkillLoading.ON_DEMAND

    def __post_init__(self) -> None:
        """Validate the loading mode independently of the skill's contents."""
        super().__post_init__()
        if not isinstance(self.loading, SkillLoading):
            raise TypeError("loading must be a SkillLoading value")


class Capability:
    """A labeled feature, supplied directly or bound by a subclass at build time.

    Supply a value for an existing tool, chain, or intact skill. Subclasses can
    omit the value and override build() to return fresh tools, chains, or skills.
    Deployment applies this capability's declared label to every returned value.
    Default chain roots retain declaration order. Objects remain caller-owned.
    """

    def __init__(
        self,
        *,
        label: ToolLabel | SkillLabel,
        value: Tool | Sequence[Tool] | Skill | None = None,
    ) -> None:
        """Validate the outer payload without inspecting skill contents."""
        self._label = label
        self.value: Tool | tuple[Tool, ...] | Skill | None = None
        if value is None:
            return
        match label:
            case SkillLabel():
                if not isinstance(value, Skill):
                    raise TypeError("a SkillLabel requires a Skill")
                self.value = value
                return
            case ToolLabel():
                pass
            case _:
                raise TypeError("a bound capability requires a ToolLabel or SkillLabel")

        if isinstance(value, Tool):
            self.value = value
            return
        if not isinstance(value, Sequence) or any(
            not isinstance(tool, Tool) for tool in value
        ):
            raise TypeError("a ToolLabel requires a Tool or tool chain")
        if label.default and not value:
            raise ValueError("a default tool capability requires a chain root")
        self.value = tuple(value)

    @property
    def label(self) -> ToolLabel | SkillLabel:
        """Return the immutable label used to select this whole capability."""
        return self._label

    @property
    def required_attributes(self) -> RequiredAttributes:
        """Declare owner configuration needed by build(); none by default."""
        return {}

    def build(
        self, agent: "DeployableAgent", pipe: EventPipe
    ) -> tuple[Tool | Sequence[Tool] | Skill, ...]:
        """Return runtime values in order; deployment applies the declared label."""
        if self.value is None:
            raise NotImplementedError(
                "a capability without a value must implement build()"
            )
        return (self.value,)


class DeployableAgent(HasExternalDependencies):
    """Mutable configuration for one agent and its attached child graph.

    Capabilities form one ordered collection; labels define selectability.
    Selection belongs to this definition and can be replaced between builds.
    Runtime-specific values can be supplied
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
        capabilities: Sequence[Capability] = (),
        nested_agents: Sequence["DeployableAgent"] = (),
        background_agents: Sequence["DeployableAgent"] = (),
    ) -> None:
        """Configure identity, behavior, capabilities, and initial children."""
        self._name = name
        self._description = description
        self._system_prompt = system_prompt
        try:
            self._mode = AgentMode(mode)
        except ValueError as error:
            raise ValueError(f"Unsupported agent mode: {mode!r}") from error
        self._automatic_tool_prompt = automatic_tool_prompt
        self._capabilities: tuple[Capability, ...] = ()
        self._capability_selection: dict[str, bool | SkillLoading] | None = None
        self.add_capabilities(*capabilities)
        self._nested_agents: list[DeployableAgent] = []
        self._background_agents: list[DeployableAgent] = []
        self._agent_endpoint: EndpointLike | None = None
        self._initial_messages: tuple[Path | str, ...] = ()
        self._attributes: dict[str, object] = {}
        self.add_nested_agents(*nested_agents)
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
    def capabilities(self) -> tuple[Capability, ...]:
        """Return every declared capability in construction order."""
        return self._capabilities

    @property
    def capability_selection(self) -> Mapping[str, bool | SkillLoading] | None:
        """Return the explicit choices, or None for all declared capabilities."""
        if self._capability_selection is None:
            return None
        return MappingProxyType(self._capability_selection)

    def set_capability_selection(
        self, selection: Mapping[str, bool | SkillLoading] | None
    ) -> None:
        """Replace choices for future builds without modifying shared capabilities.

        Fixed capabilities are always included. In an explicit selection,
        omitted optional entries are disabled; True uses declared behavior.
        Only selectable skill capabilities accept a loading override.
        """
        choices = None if selection is None else dict(selection)
        self.resolve_capabilities(choices)
        self._capability_selection = choices

    def resolve_capabilities(
        self, selection: Mapping[str, bool | SkillLoading] | None
    ) -> dict[str, bool | SkillLoading]:
        """Validate and resolve choices without changing this definition.

        None includes all declarations. Fixed capabilities resolve to True;
        enabled optional skills resolve to their loading mode. Omitted optional
        choices resolve to False. No capabilities are built.
        """
        labels = {
            capability.label.name: capability.label for capability in self.capabilities
        }
        for name, choice in (selection or {}).items():
            if name not in labels:
                raise ValueError(f"unknown capability: {name!r}")
            label = labels[name]
            if not isinstance(choice, (bool, SkillLoading)):
                raise TypeError(f"invalid capability choice for {name!r}")
            if not label.selectable and choice is not True:
                raise ValueError(f"capability {name!r} is fixed")
            if isinstance(choice, SkillLoading) and not isinstance(label, SkillLabel):
                raise ValueError(f"capability {name!r} is not a skill")
        resolved: dict[str, bool | SkillLoading] = {}
        for label in labels.values():
            choice = True if selection is None else selection.get(label.name, False)
            match label, choice:
                case CapabilityLabel(selectable=False), _:
                    choice = True
                case SkillLabel(loading=loading), True:
                    choice = loading
            resolved[label.name] = choice
        return resolved

    @property
    def nested_agents(self) -> tuple["DeployableAgent", ...]:
        """Return attached synchronous child definitions."""
        return tuple(self._nested_agents)

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

    def add_capabilities(self, *capabilities: Capability) -> None:
        """Append declarations without changing an explicit selection."""
        combined = (*self._capabilities, *capabilities)
        names = [capability.label.name for capability in combined]
        if len(set(names)) != len(names):
            raise ValueError("capability names must be unique within an agent")
        self._capabilities = combined

    def add_nested_agents(self, *nested_agents: "DeployableAgent") -> None:
        """Append synchronous child definitions."""
        self._nested_agents.extend(self._checked_agents(nested_agents))

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
        definitions = self.nested_agents
        if include_background:
            definitions += self.background_agents
        for definition in definitions:
            children = definition._collect_names(include_background, ancestors)
            if names & children:
                raise ValueError("agent names must be unique")
            names.update(children)
        return names

    def validate(self) -> None:
        """Validate graph structure and the selected capabilities' requirements."""
        self._validate()

    def _selected_capabilities(
        self, *, include_all: bool = False
    ) -> tuple[Capability, ...]:
        if include_all:
            return self.capabilities
        selection = self.resolve_capabilities(self._capability_selection)
        return tuple(
            capability
            for capability in self.capabilities
            if selection[capability.label.name] is not False
        )

    def _validate(self, *, include_all: bool = False) -> None:
        self.agent_names()
        errors = [
            error
            for agent in self._walk()
            for error in _capability_errors(
                agent, agent._selected_capabilities(include_all=include_all)
            )
        ]
        if errors:
            details = "\n".join(f"- {error}" for error in errors)
            raise ValueError(f"invalid agent configuration:\n{details}")

    def _bound_capabilities(
        self, pipe: EventPipe, *, include_all: bool = False
    ) -> Iterator[Capability]:
        selection = {} if include_all else self.resolve_capabilities(self._capability_selection)
        for capability in self.capabilities:
            label = capability.label
            choice = selection.get(label.name, True)
            if choice is False:
                continue
            match label, choice:
                case SkillLabel(), SkillLoading() if choice != label.loading:
                    label = replace(label, loading=choice)
            for value in capability.build(self, pipe):
                yield Capability(label=label, value=value)

    def _walk(self) -> tuple["DeployableAgent", ...]:
        definitions: list[DeployableAgent] = [self]
        walked: list[DeployableAgent] = []
        while definitions:
            agent = definitions.pop(0)
            walked.append(agent)
            definitions.extend((*agent.nested_agents, *agent.background_agents))
        return tuple(walked)

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        """Inspect the full declared graph, independently of capability selection.

        Use normal configuration validation and capability construction with no
        event sinks, collecting dependencies without registering alternatives
        together in a runtime. Include foreground and background descendants.
        No agent is invoked and no resource is checked or materialized.

        Each call builds fresh runtime state. Custom capability builders run as
        usual, including any construction effects they introduce; inspection
        does not provide filesystem isolation.
        """
        self._validate(include_all=True)
        resources: list[ExternalDependency] = []
        for definition in self._walk():
            resources.extend(definition._endpoint_dependencies())
            tools, defaults, skills, automatic_skills = (
                definition._build_capability_inputs(EventPipe(), include_all=True)
            )
            for skill in (*skills, *automatic_skills):
                tools.extend(skill.tools)
            for tool in (*defaults, *tools):
                resources.extend(tool.external_dependencies())
        return dedupe_external_dependencies(resources)

    def _endpoint_dependencies(self) -> tuple[ExternalDependency, ...]:
        """Inspect the agent's model endpoint only when its mode uses it."""
        if self.mode is AgentMode.DETERMINISTIC:
            return ()
        endpoint = self.agent_endpoint
        if endpoint is None:
            return ()
        return endpoint.external_dependencies()

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
            event_sinks=event_sinks,
            event_sink_factory=event_sink_factory,
        )

    def _build(
        self,
        *,
        include_all: bool = False,
        event_sinks: Sequence[EventSink] = (),
        event_sink_factory: Callable[[str], Sequence[EventSink]] | None = None,
    ) -> tuple[Agent, tuple[Agent, ...]]:
        pipe = EventPipe(
            event_sinks=(
                *(event_sink_factory(self.name) if event_sink_factory else ()),
                *event_sinks,
            )
        )
        tools, default_tools, skills, auto_loaded_skills = (
            self._build_capability_inputs(pipe, include_all=include_all)
        )
        nested_tools, nested_backgrounds = self._build_nested_agents(
            include_all=include_all,
            event_sinks=event_sinks,
            event_sink_factory=event_sink_factory,
        )
        background_tools, background_agents = self._build_background_agents(
            include_all=include_all, event_sink_factory=event_sink_factory
        )

        agent = Agent(
            name=self.name,
            description=self.description,
            agent_endpoint=self.agent_endpoint,
            event_pipe=pipe,
            mode=self.mode,
            automatic_tool_prompt=self.automatic_tool_prompt,
            system_prompt=self.system_prompt,
            tools=(*tools, *nested_tools),
            default_tools=(*default_tools, *background_tools),
            skills=skills,
            auto_loaded_skills=auto_loaded_skills,
            initial_messages=self.initial_messages,
        )
        return agent, (*nested_backgrounds, *background_agents)

    def _build_capability_inputs(
        self, pipe: EventPipe, *, include_all: bool
    ) -> tuple[list[Tool], list[Tool], list[Skill], list[Skill]]:
        """Dispatch bound contributions into the runtime's ordered input lists."""
        tools: list[Tool] = []
        default_tools: list[Tool] = []
        skills: list[Skill] = []
        auto_loaded_skills: list[Skill] = []
        for contribution in self._bound_capabilities(pipe, include_all=include_all):
            value = contribution.value
            if isinstance(value, Tool):
                value = (value,)
            match contribution.label, value:
                case SkillLabel(loading=SkillLoading.AUTOMATIC), Skill() as skill:
                    auto_loaded_skills.append(skill)
                case SkillLabel(), Skill() as skill:
                    skills.append(skill)
                case ToolLabel(default=True), tuple() as chain:
                    default_tools.append(chain[0])
                    tools.extend(chain[1:])
                case ToolLabel(), tuple() as chain:
                    tools.extend(chain)
                case _:
                    raise TypeError(
                        "build() must return bound tool or skill capabilities"
                    )

        return tools, default_tools, skills, auto_loaded_skills

    def _build_nested_agents(
        self,
        *,
        include_all: bool,
        event_sinks: Sequence[EventSink],
        event_sink_factory: Callable[[str], Sequence[EventSink]] | None,
    ) -> tuple[list[Tool], list[Agent]]:
        """Bind nested delegates, retaining their descendants' background handles."""
        tools: list[Tool] = []
        background_agents: list[Agent] = []
        for definition in self.nested_agents:
            child, descendants = definition._build(
                include_all=include_all,
                event_sinks=event_sinks,
                event_sink_factory=event_sink_factory,
            )
            tools.append(
                run_nested_agent(child).copy(
                    name=child.name,
                    description=child.description,
                )
            )
            background_agents.extend(descendants)
        return tools, background_agents

    def _build_background_agents(
        self,
        *,
        include_all: bool,
        event_sink_factory: Callable[[str], Sequence[EventSink]] | None,
    ) -> tuple[list[Tool], list[Agent]]:
        """Bind background starters without inheriting foreground event sinks."""
        tools: list[Tool] = []
        background_agents: list[Agent] = []
        for definition in self.background_agents:
            child, descendants = definition._build(
                include_all=include_all, event_sink_factory=event_sink_factory
            )
            tools.append(
                run_background_agent(BackgroundAgentContext(agent=child)).copy(
                    name=f"start_background_agent_{child.name}",
                )
            )
            background_agents.append(child)
            background_agents.extend(descendants)

        return tools, background_agents


_MISSING = object()


def _type_name(expected: RequiredAttributeType) -> str:
    types = expected if isinstance(expected, tuple) else (expected,)
    return " or ".join(item.__name__ for item in types)


def _capability_errors(
    agent: DeployableAgent, capabilities: Sequence[Capability]
) -> Iterator[str]:
    """Yield unmet attribute requirements for one configuration node."""
    for capability in capabilities:
        for name, expected in capability.required_attributes.items():
            error = _required_attribute_error(agent, capability, name, expected)
            if error is not None:
                yield error


def _required_attribute_error(
    agent: DeployableAgent,
    capability: Capability,
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
    "Capability",
    "CapabilityLabel",
    "ToolLabel",
    "SkillLabel",
    "SkillLoading",
    "DeployableAgent",
    "RequiredAttributeType",
    "RequiredAttributes",
]
