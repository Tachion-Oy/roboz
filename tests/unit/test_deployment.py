from collections.abc import Mapping

import pytest

from roboz import Empty, Message, Skill, Str, stop, tool
from roboz.deployment import (
    AgentCapability,
    Capability,
    DeployableAgent,
    RequiredAttributes,
)
from roboz.llm import MockLLMEndpoint
from roboz.runtime import Output


class _ConfiguredCapability(AgentCapability):
    @property
    def required_attributes(self) -> RequiredAttributes:
        return {"setting": str}

    def build(self, agent, pipe):
        return Capability(tools=(stop,))


def _definition(
    name: str,
    *,
    capabilities=(),
    subagents=(),
    background_agents=(),
) -> DeployableAgent:
    definition = DeployableAgent(
        name=name,
        is_agentic=False,
        default_capabilities=(
            capabilities
            if capabilities
            else (Capability(default_tools=(stop,)),)
        ),
        subagents=subagents,
        background_agents=background_agents,
    )
    definition.set_interaction_mode(None)
    return definition


def test_capabilities_build_all_four_surfaces_and_preserve_chain_identity():
    calls = []
    built_pipes = []
    received_agents = []

    class Capabilities(AgentCapability):
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

            return Capability(
                tools=((begin, finish),),
                default_tools=(begin,),
                skills=(
                    Skill(
                        name="research",
                        description="Research instructions.",
                        instructions="ON_DEMAND_MARKER",
                    ),
                ),
                auto_loaded_skills=(
                    Skill(
                        name="orientation",
                        description="Orientation.",
                        instructions="AUTO_LOADED_MARKER",
                    ),
                ),
            )

    definition = DeployableAgent(
        name="test",
        system_prompt="Exercise the capabilities.",
        default_capabilities=(Capability(tools=(stop,)), Capabilities()),
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
    definition.set_interaction_mode(Output.API)

    agent, backgrounds = definition.build()
    second, second_backgrounds = definition.build()

    assert backgrounds == second_backgrounds == ()
    assert built_pipes == [agent.pipe, second.pipe]
    assert received_agents == [definition, definition]
    assert agent.pipe is not second.pipe
    assert agent.default_tools[0] is not second.default_tools[0]
    assert agent.default_tools[0] in agent.tools
    result, messages = agent.invoke()
    assert result.value == "ok"
    assert calls == ["default", "handoff", "default", "handoff"]
    text = "\n".join(message.content for message in messages)
    assert "AUTO_LOADED_MARKER" in text and "ON_DEMAND_MARKER" in text


def test_duplicate_names_fail_before_building_capabilities_or_sinks():
    def unexpected(_name):
        raise AssertionError("Must validate names before allocating sinks")

    root = _definition("duplicate", subagents=(_definition("duplicate"),))
    with pytest.raises(ValueError, match="unique"):
        root.build(event_sink_factory=unexpected)


def test_build_returns_fresh_nested_background_handles():
    nested = _definition("nested")
    background = _definition("background", background_agents=(nested,))
    child = _definition("child", background_agents=(background,))
    root = _definition(
        "root",
        capabilities=(Capability(default_tools=(stop,)),),
        subagents=(child,),
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

    class Feature:
        @property
        def required_attributes(self):
            return {}

        def build(self, agent, pipe):
            received.append((agent, pipe))
            return Capability(tools=(stop,))

    child = DeployableAgent(
        name="child",
        system_prompt="Complete the task.",
        default_capabilities=(Feature(),),
    )
    child.set_agent_endpoint(MockLLMEndpoint([]))
    parent = DeployableAgent(
        name="parent",
        system_prompt="Delegate the task.",
        default_capabilities=(Feature(),),
        subagents=(child,),
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


def test_validation_aggregates_missing_none_and_wrong_types_across_graph():
    missing = _definition("missing", capabilities=(_ConfiguredCapability(),))
    none = _definition("none", capabilities=(_ConfiguredCapability(),))
    none.set_attributes(setting=None)
    wrong = _definition("wrong", capabilities=(_ConfiguredCapability(),))
    wrong.set_attributes(setting=42)
    root = _definition("root", subagents=(missing, none), background_agents=(wrong,))

    with pytest.raises(ValueError) as raised:
        root.validate()

    message = str(raised.value)
    assert "agent 'missing', capability _ConfiguredCapability" in message
    assert "setting' must be str; it is missing" in message
    assert "agent 'none', capability _ConfiguredCapability" in message
    assert "setting' must be str; it is None" in message
    assert "agent 'wrong', capability _ConfiguredCapability" in message
    assert "setting' must be str; got int" in message


def test_default_capabilities_and_child_views_cannot_be_replaced_or_cleared():
    built_in = Capability()
    extension = Capability(tools=(stop,))
    child = _definition("child")
    background = _definition("background")
    definition = _definition("root", capabilities=(built_in,))

    definition.add_capabilities(extension)
    definition.add_subagents(child)
    definition.add_background_agents(background)

    assert definition.default_capabilities == (built_in,)
    assert definition.additional_capabilities == (extension,)
    assert definition.capabilities == (built_in, extension)
    assert definition.subagents == (child,)
    assert definition.background_agents == (background,)
    for attribute in (
        "default_capabilities",
        "additional_capabilities",
        "capabilities",
        "subagents",
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
    class FalseyCapability:
        @property
        def required_attributes(self):
            return {"items": list, "enabled": bool, "count": int}

        def build(self, agent, pipe):
            return Capability()

    definition = _definition("falsey", capabilities=(FalseyCapability(),))
    definition.set_attributes(items=[], enabled=False, count=0)
    definition.validate()
