from roboz.deployment import (
    Capability,
    DeployableAgent,
    RequiredAttributes,
    SkillLabel,
    SkillLoading,
    ToolLabel,
)
import pickle
from copy import copy, deepcopy
from collections.abc import Mapping

import pytest

from roboz.agent import AgentMode
from roboz.models import Empty, Message, Str
from roboz import Skill, tool
from roboz.tools import stop
from roboz.llm import MockLLMEndpoint


class _ConfiguredCapability(Capability):
    def __init__(self):
        super().__init__(label=ToolLabel("configured_capability"))

    @property
    def required_attributes(self) -> RequiredAttributes:
        return {"setting": str}

    def build(self, agent, pipe):
        return (stop,)


def _definition(
    name: str,
    *,
    capabilities=(),
    nested_agents=(),
    background_agents=(),
) -> DeployableAgent:
    definition = DeployableAgent(
        name=name,
        mode=AgentMode.DETERMINISTIC,
        capabilities=(
            capabilities
            if capabilities
            else (Capability(label=ToolLabel("stop", default=True), value=stop),)
        ),
        nested_agents=nested_agents,
        background_agents=background_agents,
    )
    return definition


def test_build_preserves_loading_scheduling_and_chain_identity():
    calls = []
    built_pipes = []
    received_agents = []

    class ScheduledChain(Capability):
        def __init__(self):
            super().__init__(label=ToolLabel("chain", default=True))

        @property
        def required_attributes(self) -> Mapping:
            return {}

        def build(self, agent, pipe):
            built_pipes.append(pipe)
            received_agents.append(agent)

            @tool
            def begin(input: Empty, messages: list[Message]) -> Str:
                calls.append("default")
                return Str(value="handoff")

            @tool(chained_to=begin)
            def finish(input: Str, messages: list[Message]) -> Empty:
                calls.append(input.value)
                return Empty()

            return ((begin, finish),)

    definition = DeployableAgent(
        name="test",
        system_prompt="Exercise the capabilities.",
        capabilities=(
            Capability(label=ToolLabel("stop"), value=stop),
            ScheduledChain(),
            Capability(
                label=SkillLabel("research"),
                value=Skill(
                    name="research",
                    description="Research instructions.",
                    instructions="ON_DEMAND_MARKER",
                ),
            ),
            Capability(
                label=SkillLabel("orientation", loading=SkillLoading.AUTOMATIC),
                value=Skill(
                    name="orientation",
                    description="Orientation.",
                    instructions="AUTO_LOADED_MARKER",
                ),
            ),
        ),
    )
    definition.set_agent_endpoint(
        MockLLMEndpoint(
            [
                {"action": "research", "rationale": "load instructions"},
                {"action": "stop", "rationale": "done", "value": "ok"},
                {"action": "research", "rationale": "load instructions"},
                {"action": "stop", "rationale": "done", "value": "ok"},
            ]
        )
    )

    agent, backgrounds = definition.build()
    second, second_backgrounds = definition.build()

    assert backgrounds == second_backgrounds == ()
    assert built_pipes == [agent.pipe, second.pipe]
    assert received_agents == [definition, definition]
    assert agent.pipe is not second.pipe
    assert agent.default_tools[0] is not second.default_tools[0]
    assert agent.tools[1].chained_to[0] is agent.default_tools[0]
    result, messages = agent.invoke()
    assert result.value == "ok"
    assert calls == ["default", "handoff", "default", "handoff"]
    text = "\n".join(message.content for message in messages)
    assert "AUTO_LOADED_MARKER" in text and "ON_DEMAND_MARKER" in text


def test_duplicate_names_fail_before_building_capabilities_or_sinks():
    def unexpected(_name):
        raise AssertionError("Must validate names before allocating sinks")

    root = _definition("duplicate", nested_agents=(_definition("duplicate"),))
    with pytest.raises(ValueError, match="unique"):
        root.build(event_sink_factory=unexpected)


def test_background_cycle_fails_before_build_and_in_foreground_name_queries():
    root = _definition("root")
    background = _definition("background", nested_agents=(root,))
    root.add_background_agents(background)

    with pytest.raises(ValueError, match="cycles"):
        root.agent_names(include_background=False)
    with pytest.raises(ValueError, match="cycles"):
        root.build(event_sink_factory=lambda name: pytest.fail("allocated sinks"))


def test_reusing_a_child_across_branches_is_a_duplicate_name():
    child = _definition("child")
    root = _definition("root", nested_agents=(child,), background_agents=(child,))

    with pytest.raises(ValueError, match="agent names must be unique"):
        root.validate()


@pytest.mark.parametrize("children", ["nested_agents", "background_agents"])
def test_invalid_child_additions_leave_existing_children_unchanged(children):
    existing = _definition("existing")
    root = _definition("root", **{children: (existing,)})

    with pytest.raises(TypeError, match="child definitions"):
        getattr(root, f"add_{children}")(_definition("valid"), object())

    assert getattr(root, children) == (existing,)


@pytest.mark.parametrize(
    "label, value, error, message",
    [
        (SkillLabel("bad"), stop, TypeError, "a SkillLabel requires a Skill"),
        (
            ToolLabel("bad"),
            Skill(name="skill", description="Skill", instructions="Help."),
            TypeError,
            "a ToolLabel requires a Tool or tool chain",
        ),
        (
            ToolLabel("bad"),
            (stop, object()),
            TypeError,
            "a ToolLabel requires a Tool or tool chain",
        ),
        (
            ToolLabel("bad", default=True),
            (),
            ValueError,
            "a default tool capability requires a chain root",
        ),
    ],
)
def test_supplied_and_built_payloads_use_the_same_validation(
    label, value, error, message
):
    with pytest.raises(error, match=message):
        Capability(label=label, value=value)

    class Invalid(Capability):
        def build(self, agent, pipe):
            return (value,)

    definition = _definition("invalid", capabilities=(Invalid(label=label),))
    with pytest.raises(error, match=message):
        definition.build()


def test_builder_cannot_return_an_unbound_value():
    class Unbound(Capability):
        def build(self, agent, pipe):
            return (None,)

    definition = _definition(
        "invalid", capabilities=(Unbound(label=ToolLabel("unbound")),)
    )
    with pytest.raises(TypeError, match="must return bound tool or skill capabilities"):
        definition.build()


def test_builder_returning_no_values_still_configures_the_runtime_pipe():
    events = []

    class ConfigurePipe(Capability):
        def build(self, agent, pipe):
            pipe.add_sink(events.append)
            return ()

    definition = _definition(
        "configured",
        capabilities=(
            ConfigurePipe(label=ToolLabel("configure_pipe", default=True)),
            Capability(label=ToolLabel("stop", default=True), value=stop),
        ),
    )
    runtime, _ = definition.build()

    assert events.append in runtime.pipe.event_sinks
    assert runtime.default_tools == [stop]


def test_build_returns_fresh_nested_background_handles():
    nested = _definition("nested")
    background = _definition("background", background_agents=(nested,))
    child = _definition("child", background_agents=(background,))
    root = _definition(
        "root",
        capabilities=(Capability(label=ToolLabel("stop", default=True), value=stop),),
        nested_agents=(child,),
        background_agents=(_definition("other"),),
    )

    agent, backgrounds = root.build()
    second, fresh_backgrounds = root.build()

    assert agent.name == "root"
    assert [item.name for item in backgrounds] == ["background", "nested", "other"]
    assert agent.pipe is not second.pipe
    for first, fresh in zip(backgrounds, fresh_backgrounds, strict=True):
        assert first.pipe is not fresh.pipe


def test_each_capability_receives_its_owning_agent():
    received = []

    class Feature(Capability):
        def __init__(self):
            super().__init__(label=ToolLabel("feature"))

        @property
        def required_attributes(self):
            return {}

        def build(self, agent, pipe):
            received.append((agent, pipe))
            return (stop,)

    child = DeployableAgent(
        name="child",
        system_prompt="Complete the task.",
        capabilities=(Feature(),),
    )
    child.set_agent_endpoint(MockLLMEndpoint([]))
    parent = DeployableAgent(
        name="parent",
        system_prompt="Delegate the task.",
        capabilities=(Feature(),),
        nested_agents=(child,),
    )
    parent.set_agent_endpoint(MockLLMEndpoint([]))

    runtime, _ = parent.build()

    assert received[0] == (parent, runtime.pipe)
    assert received[1][0] is child
    assert received[1][1] is not runtime.pipe


def test_incomplete_configuration_can_be_completed_later():
    definition = _definition("configurable", capabilities=(_ConfiguredCapability(),))

    with pytest.raises(ValueError, match="setting.*missing"):
        definition.validate()

    definition.set_attributes(setting="configured")
    definition.validate()


def test_configuration_can_be_copied_and_unpickled():
    definition = DeployableAgent(name="copyable", mode=AgentMode.DETERMINISTIC)
    definition.set_attributes(setting="configured")
    definition.set_capability_selection({})

    restored_definitions = (
        copy(definition),
        deepcopy(definition),
        pickle.loads(pickle.dumps(definition)),
    )

    for restored in restored_definitions:
        assert restored is not definition
        assert restored.setting == "configured"
        assert restored.mode is AgentMode.DETERMINISTIC
        assert restored.capability_selection == {}
        restored.set_capability_selection(None)
        assert definition.capability_selection == {}


def test_build_uses_updated_configuration():
    definition = _definition("original")
    endpoint = MockLLMEndpoint([])

    definition.name = "updated"
    definition.description = "Updated description."
    definition.system_prompt = "Use the updated configuration."
    definition.mode = AgentMode.AUTONOMOUS
    definition.automatic_tool_prompt = False
    definition.agent_endpoint = endpoint
    definition.initial_messages = ("Updated context.",)

    agent, _ = definition.build()

    assert agent.name == "updated"
    assert agent.description == "Updated description."
    assert agent.system_prompt == "Use the updated configuration."
    assert agent.mode is AgentMode.AUTONOMOUS
    assert agent.automatic_tool_prompt is False
    assert agent.agent_endpoint is endpoint
    assert agent.initial_messages == ("Updated context.",)


def test_agent_modes_are_string_valued_enums():
    assert [(mode.name, mode.value) for mode in AgentMode] == [
        ("DETERMINISTIC", "deterministic"),
        ("STEERABLE", "steerable"),
        ("AUTONOMOUS", "autonomous"),
    ]
    assert all(isinstance(mode, str) for mode in AgentMode)
    assert AgentMode.DETERMINISTIC.allows_user_interaction
    assert AgentMode.STEERABLE.allows_user_interaction
    assert not AgentMode.AUTONOMOUS.allows_user_interaction


@pytest.mark.parametrize("mode", list(AgentMode))
def test_build_preserves_agent_mode(mode: AgentMode):
    capability = (
        Capability(label=ToolLabel("stop", default=True), value=stop)
        if mode is AgentMode.DETERMINISTIC
        else Capability(label=ToolLabel("stop"), value=stop)
    )
    definition = DeployableAgent(
        name=f"{mode}_agent",
        mode=mode,
        system_prompt=(
            "Complete the task." if mode is not AgentMode.DETERMINISTIC else ""
        ),
        capabilities=(capability,),
    )
    if mode is not AgentMode.DETERMINISTIC:
        definition.set_agent_endpoint(MockLLMEndpoint([]))

    agent, _ = definition.build()

    assert agent.mode is mode
    assert (agent.prompt_user_tool is None) is (mode is AgentMode.AUTONOMOUS)


def test_nested_agent_binds_its_own_mode_inside_autonomous_parent(monkeypatch, capsys):
    child = DeployableAgent(
        name="steerable_child",
        mode=AgentMode.STEERABLE,
        system_prompt="Ask once, then stop.",
        capabilities=(Capability(label=ToolLabel("stop"), value=stop),),
    )
    child.set_agent_endpoint(
        MockLLMEndpoint(
            [
                {
                    "action": "prompt_user",
                    "rationale": "ask",
                    "value": "question",
                },
                {"action": "stop", "rationale": "done", "value": "child done"},
            ]
        )
    )
    parent = DeployableAgent(
        name="autonomous_parent",
        mode=AgentMode.AUTONOMOUS,
        system_prompt="Delegate, then stop.",
        capabilities=(Capability(label=ToolLabel("stop"), value=stop),),
        nested_agents=(child,),
    )
    parent.set_agent_endpoint(
        MockLLMEndpoint(
            [
                {"action": "steerable_child", "rationale": "delegate"},
                {"action": "stop", "rationale": "done", "value": "parent done"},
            ]
        )
    )
    from roboz.runtime import io

    monkeypatch.setattr(io.sys.stdin, "readline", lambda: "answer\n")

    agent, _ = parent.build()
    result, _ = agent.invoke()

    assert result.value == "parent done"
    assert "question" in capsys.readouterr().out


def test_validation_aggregates_missing_none_and_wrong_types_across_graph():
    missing = _definition("missing", capabilities=(_ConfiguredCapability(),))
    none = _definition("none", capabilities=(_ConfiguredCapability(),))
    none.set_attributes(setting=None)
    wrong = _definition("wrong", capabilities=(_ConfiguredCapability(),))
    wrong.set_attributes(setting=42)
    root = _definition(
        "root", nested_agents=(missing, none), background_agents=(wrong,)
    )

    with pytest.raises(ValueError) as raised:
        root.validate()

    message = str(raised.value)
    assert "agent 'missing', capability _ConfiguredCapability" in message
    assert "setting' must be str; it is missing" in message
    assert "agent 'none', capability _ConfiguredCapability" in message
    assert "setting' must be str; it is None" in message
    assert "agent 'wrong', capability _ConfiguredCapability" in message
    assert "setting' must be str; got int" in message


def test_capabilities_and_child_views_cannot_be_replaced_or_cleared():
    built_in = Capability(label=ToolLabel("empty"), value=())
    extension = Capability(label=ToolLabel("stop"), value=stop)
    child = _definition("child")
    background = _definition("background")
    definition = _definition("root", capabilities=(built_in,))

    definition.add_capabilities(extension)
    definition.add_nested_agents(child)
    definition.add_background_agents(background)

    assert definition.capabilities == (built_in, extension)
    assert definition.nested_agents == (child,)
    assert definition.background_agents == (background,)
    for attribute in (
        "capabilities",
        "nested_agents",
        "background_agents",
    ):
        with pytest.raises(AttributeError):
            setattr(definition, attribute, ())


@pytest.mark.parametrize(
    "name",
    ["name", "agent_endpoint", "capabilities", "build", "_attributes"],
)
def test_capability_attributes_cannot_overwrite_agent_structure(name):
    definition = _definition("protected")
    with pytest.raises(ValueError, match="cannot overwrite"):
        definition.set_attributes(**{name: object()})


def test_valid_falsey_capability_attributes_are_accepted():
    class FalseyCapability(Capability):
        def __init__(self):
            super().__init__(label=ToolLabel("falsey_capability"))

        @property
        def required_attributes(self):
            return {"items": list, "enabled": bool, "count": int}

        def build(self, agent, pipe):
            return ((),)

    definition = _definition("falsey", capabilities=(FalseyCapability(),))
    definition.set_attributes(items=[], enabled=False, count=0)
    definition.validate()
