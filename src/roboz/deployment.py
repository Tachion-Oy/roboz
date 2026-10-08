"""Configurable agent definitions and runtime-bound capabilities."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
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
from roboz.agent.core import AgentInputs
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
        self.value: Tool | tuple[Tool, ...] | Skill | None = (
            None if value is None else self._validate_value(value)
        )

    def _validate_value(self, value: object) -> Tool | tuple[Tool, ...] | Skill:
        """Validate a supplied or built payload without inspecting skill contents."""
        if value is None:
            raise TypeError("build() must return bound tool or skill capabilities")
        match self.label:
            case SkillLabel():
                if not isinstance(value, Skill):
                    raise TypeError("a SkillLabel requires a Skill")
                return value
            case ToolLabel(default=default):
                if isinstance(value, Tool):
                    return value
                if not isinstance(value, Sequence) or any(
                    not isinstance(tool, Tool) for tool in value
                ):
                    raise TypeError("a ToolLabel requires a Tool or tool chain")
                if default and not value:
                    raise ValueError("a default tool capability requires a chain root")
                return tuple(value)
            case _:
                raise TypeError("a bound capability requires a ToolLabel or SkillLabel")

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

    def _build_inputs(
        self,
        agent: DeployableAgent,
        pipe: EventPipe,
        *,
        choice: bool | SkillLoading = True,
    ) -> AgentInputs:
        """Build and route this capability's values into Agent constructor fields."""
        if choice is False:
            return {}
        values = [self._validate_value(value) for value in self.build(agent, pipe)]
        label = self.label
        if isinstance(label, SkillLabel):
            skills = [value for value in values if isinstance(value, Skill)]
            loading = choice if isinstance(choice, SkillLoading) else label.loading
            if loading is SkillLoading.AUTOMATIC:
                return {"auto_loaded_skills": skills}
            return {"skills": skills}

        chains = [
            (value,) if isinstance(value, Tool) else value
            for value in values
            if not isinstance(value, Skill)
        ]
        if label.default:
            return {
                "default_tools": [chain[0] for chain in chains],
                "tools": Tool.to_tool_list([chain[1:] for chain in chains]),
            }
        return {"tools": Tool.to_tool_list(chains)}


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
        self.name = name
        self.description = description
        self.system_prompt = system_prompt
        try:
            self.mode = AgentMode(mode)
        except ValueError as error:
            raise ValueError(f"Unsupported agent mode: {mode!r}") from error
        self.automatic_tool_prompt = automatic_tool_prompt
        self._capabilities: tuple[Capability, ...] = ()
        self._capability_selection: dict[str, bool | SkillLoading] | None = None
        self.add_capabilities(*capabilities)
        self._nested_agents: list[DeployableAgent] = []
        self._background_agents: list[DeployableAgent] = []
        self.agent_endpoint: EndpointLike | None = None
        self.initial_messages: tuple[Path | str, ...] = ()
        self._attributes: dict[str, object] = {}
        self.add_nested_agents(*nested_agents)
        self.add_background_agents(*background_agents)

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
        if selection is None:
            self._capability_selection = None
            return
        choices = dict(selection)
        labels = {
            capability.label.name: capability.label for capability in self.capabilities
        }
        for name, choice in choices.items():
            if name not in labels:
                raise ValueError(f"unknown capability: {name!r}")
            label = labels[name]
            if not isinstance(choice, (bool, SkillLoading)):
                raise TypeError(f"invalid capability choice for {name!r}")
            if not label.selectable and choice is not True:
                raise ValueError(f"capability {name!r} is fixed")
            if isinstance(choice, SkillLoading) and not isinstance(label, SkillLabel):
                raise ValueError(f"capability {name!r} is not a skill")
        self._capability_selection = choices

    def resolve_capabilities(self) -> dict[str, bool | SkillLoading]:
        """Return effective choices for every declared capability.

        Fixed capabilities resolve to True. Optional skills resolve to their
        loading mode when enabled; omitted optional choices resolve to False.
        With no explicit selection, all capabilities use their declared behavior.
        Resolution neither builds capabilities nor changes the explicit selection.
        """
        selection = self._capability_selection
        resolved: dict[str, bool | SkillLoading] = {}
        for capability in self.capabilities:
            label = capability.label
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

    def __getattr__(self, name: str) -> object:
        """Expose explicitly supplied capability attributes for owner reads."""
        try:
            attributes: dict[str, object] = object.__getattribute__(self, "_attributes")
            return attributes[name]
        except (AttributeError, KeyError):
            raise AttributeError(name) from None

    def set_agent_endpoint(self, endpoint: EndpointLike | None) -> None:
        """Set or defer this node's model endpoint."""
        self.agent_endpoint = endpoint

    def set_initial_messages(self, messages: Sequence[Path | str]) -> None:
        """Replace this node's initial message sources before a build."""
        self.initial_messages = tuple(messages)

    def set_attributes(self, **values: object) -> None:
        """Set capability-specific values without altering agent structure.

        Existing capability attributes may be updated for a later build. Public
        structural attributes, methods, and all private names are protected.
        """
        for name in values:
            if (
                name.startswith("_")
                or name in self.__dict__
                or any(name in cls.__dict__ for cls in type(self).__mro__)
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
        if any(not isinstance(agent, DeployableAgent) for agent in nested_agents):
            raise TypeError("child definitions must be DeployableAgent instances")
        self._nested_agents.extend(nested_agents)

    def add_background_agents(self, *agents: "DeployableAgent") -> None:
        """Append background child definitions."""
        if any(not isinstance(agent, DeployableAgent) for agent in agents):
            raise TypeError("child definitions must be DeployableAgent instances")
        self._background_agents.extend(agents)

    def agent_names(self, *, include_background: bool = True) -> frozenset[str]:
        """Validate all names and return identities in the selected branches.

        Excluding background agents also excludes their descendants, for
        foreground-only consumers such as conversation maintenance.
        """
        return frozenset(
            agent.name for agent in self._walk(include_background=include_background)
        )

    def validate(self) -> None:
        """Validate graph structure and the selected capabilities' requirements."""
        errors = [
            error
            for agent in self._walk()
            for error in agent._capability_errors(include_all=False)
        ]
        if errors:
            details = "\n".join(f"- {error}" for error in errors)
            raise ValueError(f"invalid agent configuration:\n{details}")

    def _capability_errors(self, *, include_all: bool) -> Iterator[str]:
        """Yield unmet attribute requirements for this configuration node."""
        missing = object()
        selection = {} if include_all else self.resolve_capabilities()
        for capability in self.capabilities:
            if selection.get(capability.label.name, True) is False:
                continue
            for name, expected in capability.required_attributes.items():
                value = getattr(self, name, missing)
                if (
                    value is not missing
                    and value is not None
                    and isinstance(value, expected)
                ):
                    continue
                owner = f"agent {self.name!r}, capability {type(capability).__name__}"
                types = expected if isinstance(expected, tuple) else (expected,)
                type_name = " or ".join(item.__name__ for item in types)
                requirement = f"attribute {name!r} must be {type_name}"
                if value is missing:
                    actual = "it is missing"
                elif value is None:
                    actual = "it is None"
                else:
                    actual = f"got {type(value).__name__}"
                yield f"{owner}: {requirement}; {actual}"

    def _walk(
        self, *, include_background: bool = True
    ) -> tuple["DeployableAgent", ...]:
        """Validate the full graph and collect selected branches breadth first."""
        pending: deque[tuple[DeployableAgent, bool, frozenset[int]]] = deque(
            [(self, True, frozenset())]
        )
        walked: list[DeployableAgent] = []
        names: set[str] = set()
        while pending:
            agent, foreground, ancestors = pending.popleft()
            if id(agent) in ancestors:
                raise ValueError("agent graph must not contain cycles")
            if agent.name in names:
                raise ValueError("agent names must be unique")
            names.add(agent.name)
            if include_background or foreground:
                walked.append(agent)
            ancestors = ancestors | {id(agent)}
            pending.extend(
                (child, foreground, ancestors) for child in agent.nested_agents
            )
            pending.extend(
                (child, False, ancestors) for child in agent.background_agents
            )
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
        definitions = self._walk()
        errors = [
            error
            for agent in definitions
            for error in agent._capability_errors(include_all=True)
        ]
        if errors:
            details = "\n".join(f"- {error}" for error in errors)
            raise ValueError(f"invalid agent configuration:\n{details}")

        resources: list[ExternalDependency] = []
        for definition in definitions:
            endpoint = definition.agent_endpoint
            if definition.mode is not AgentMode.DETERMINISTIC and endpoint is not None:
                resources.extend(endpoint.external_dependencies())
            pipe = EventPipe()
            inputs = [
                capability._build_inputs(definition, pipe)
                for capability in definition.capabilities
            ]
            tools = [
                tool for item in inputs for tool in (item.get("default_tools") or ())
            ]
            tools.extend(tool for item in inputs for tool in item.get("tools", ()))
            skills = [skill for item in inputs for skill in item.get("skills", ())]
            skills.extend(
                skill
                for item in inputs
                for skill in (item.get("auto_loaded_skills") or ())
            )
            tools.extend(tool for skill in skills for tool in skill.tools)
            for tool in tools:
                resources.extend(tool.external_dependencies())
        return dedupe_external_dependencies(resources)

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
        pipe = EventPipe(
            event_sinks=(
                *(event_sink_factory(self.name) if event_sink_factory else ()),
                *event_sinks,
            )
        )
        selection = self.resolve_capabilities()
        inputs = [
            capability._build_inputs(
                self, pipe, choice=selection[capability.label.name]
            )
            for capability in self.capabilities
        ]
        tools = [tool for item in inputs for tool in item.get("tools", ())]
        default_tools = [
            tool for item in inputs for tool in (item.get("default_tools") or ())
        ]
        skills = [skill for item in inputs for skill in item.get("skills", ())]
        auto_loaded_skills = [
            skill for item in inputs for skill in (item.get("auto_loaded_skills") or ())
        ]

        backgrounds: list[Agent] = []
        for definition in self.nested_agents:
            child, descendants = definition.build(
                event_sinks=event_sinks, event_sink_factory=event_sink_factory
            )
            tools.append(
                run_nested_agent(child).copy(
                    name=child.name, description=child.description
                )
            )
            backgrounds.extend(descendants)
        for definition in self.background_agents:
            child, descendants = definition.build(event_sink_factory=event_sink_factory)
            default_tools.append(
                run_background_agent(BackgroundAgentContext(agent=child)).copy(
                    name=f"start_background_agent_{child.name}"
                )
            )
            backgrounds.extend((child, *descendants))

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
            skills=skills,
            auto_loaded_skills=auto_loaded_skills,
            initial_messages=self.initial_messages,
        )
        return agent, tuple(backgrounds)


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
