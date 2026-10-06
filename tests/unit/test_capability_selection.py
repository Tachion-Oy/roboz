"""Capability selection belongs to definitions, while bundles remain intact."""

from copy import copy, deepcopy

import pytest

from roboz import Skill, tool
from roboz.agent import AgentMode
from roboz.deployment import (
    Capability,
    DeployableAgent,
    SkillLabel,
    SkillLoading,
    ToolLabel,
)
from roboz.llm import MockLLMEndpoint
from roboz.models import Empty, Message, Str
from roboz.tools import stop


def definition(*capabilities):
    agent = DeployableAgent(
        name="test", system_prompt="Help the user.", capabilities=capabilities
    )
    agent.set_agent_endpoint(MockLLMEndpoint([]))
    return agent


@pytest.mark.parametrize(
    "selection, tool_choice, skill_choice",
    [
        (None, True, SkillLoading.ON_DEMAND),
        ({}, False, False),
        ({"guide": True}, False, SkillLoading.ON_DEMAND),
        ({"guide": SkillLoading.AUTOMATIC}, False, SkillLoading.AUTOMATIC),
    ],
)
def test_resolution_preserves_selection_and_round_trips_without_building(
    selection, tool_choice, skill_choice
):
    agent = DeployableAgent(
        name="test",
        capabilities=(
            Capability(label=ToolLabel("fixed")),
            Capability(label=SkillLabel("fixed_skill", loading=SkillLoading.AUTOMATIC)),
            Capability(label=ToolLabel("optional", selectable=True)),
            Capability(label=SkillLabel("guide", selectable=True)),
        ),
    )
    agent.set_capability_selection(selection)
    resolved = agent.resolve_capabilities()
    assert resolved == {
        "fixed": True,
        "fixed_skill": True,
        "optional": tool_choice,
        "guide": skill_choice,
    }
    assert agent.capability_selection == selection
    agent.set_capability_selection(resolved)
    resolved.clear()
    assert agent.resolve_capabilities()["guide"] == skill_choice


def test_selection_and_loading_belong_to_each_definition_and_future_builds():
    embedded = stop.copy(name="embedded")
    skill = Skill(
        name="guide", description="Guide", instructions="Help.", tools=(embedded,)
    )
    shared = Capability(label=SkillLabel("guide", selectable=True), value=skill)
    first, second = definition(shared), definition(shared)
    first.set_capability_selection({"guide": SkillLoading.ON_DEMAND})
    second.set_capability_selection({"guide": SkillLoading.AUTOMATIC})
    lazy, _ = first.build()
    automatic, _ = second.build()
    assert lazy.skills == [skill] and not lazy.auto_loaded_skills
    assert automatic.auto_loaded_skills == [skill] and not automatic.skills
    assert lazy.skills[0] is skill and automatic.auto_loaded_skills[0] is skill
    assert skill.tools == [embedded]
    assert shared.label.loading is SkillLoading.ON_DEMAND

    first.set_capability_selection({})
    disabled, _ = first.build()
    assert not disabled.skills and not disabled.auto_loaded_skills
    assert lazy.skills == [skill] and automatic.auto_loaded_skills == [skill]
    assert second.capability_selection == {"guide": SkillLoading.AUTOMATIC}


def test_disabled_bundle_skips_requirements_and_builder_then_enables_as_a_whole():
    calls = []

    class Bundle(Capability):
        def __init__(self):
            super().__init__(label=ToolLabel("bundle", selectable=True))

        required_attributes = {"setting": str}

        def build(self, owner, pipe):
            calls.append((owner.setting, pipe))
            return (
                stop.copy(name="one"),
                stop.copy(name="two"),
            )

    agent = definition(Bundle())
    assert agent.capabilities[0].label.name == "bundle" and not calls
    agent.set_capability_selection({})
    agent.validate()
    empty, _ = agent.build()
    assert not empty.tools and empty.prompt_user_tool is not None and not calls
    agent.set_capability_selection({"bundle": True})
    with pytest.raises(ValueError, match="setting.*missing"):
        agent.build()
    assert not calls
    agent.set_attributes(setting="ready")
    runtime, _ = agent.build()
    assert {item.name for item in runtime.tools} == {"one", "two"}
    assert calls == [("ready", runtime.pipe)]


def test_fixed_and_appended_choices_and_selection_ownership():
    fixed = Capability(label=ToolLabel("fixed"), value=stop)
    optional = Capability(
        label=ToolLabel("optional", selectable=True), value=stop.copy(name="optional")
    )
    agent = definition(fixed)
    choices = {"fixed": True}
    agent.set_capability_selection(choices)
    choices["fixed"] = False
    with pytest.raises(TypeError):
        agent.capability_selection["fixed"] = False
    agent.add_capabilities(optional)
    runtime, _ = agent.build()
    assert runtime.tools == [stop]
    for restored in (copy(agent), deepcopy(agent)):
        restored.set_capability_selection({"optional": True})
        assert agent.capability_selection == {"fixed": True}
    agent.set_capability_selection(None)
    runtime, _ = agent.build()
    assert [item.name for item in runtime.tools] == ["stop", "optional"]
    with pytest.raises(ValueError, match="unique"):
        agent.add_capabilities(optional)
    assert agent.capabilities == (fixed, optional)


@pytest.mark.parametrize(
    "choices, error",
    [
        ({"missing": True}, ValueError),
        ({"fixed": False}, ValueError),
        ({"fixed": SkillLoading.AUTOMATIC}, ValueError),
        ({"optional": SkillLoading.ON_DEMAND}, ValueError),
        ({"optional": "automatic"}, TypeError),
    ],
)
def test_invalid_choices_leave_previous_selection_unchanged(choices, error):
    agent = definition(
        Capability(label=ToolLabel("fixed"), value=stop),
        Capability(
            label=ToolLabel("optional", selectable=True),
            value=stop.copy(name="optional"),
        ),
    )
    agent.set_capability_selection({})
    with pytest.raises(error):
        agent.set_capability_selection(choices)
    assert agent.capability_selection == {}


def test_selection_preserves_default_order_and_completes_each_chain():
    calls = []

    @tool
    def begin(input: Empty, messages: list[Message]) -> Str:
        calls.append("begin")
        return Str(value="done")

    @tool(chained_to=begin)
    def follow(input: Str, messages: list[Message]) -> Str:
        calls.append("follow")
        return input

    agent = DeployableAgent(
        name="scheduled",
        mode=AgentMode.DETERMINISTIC,
        capabilities=(
            Capability(
                label=ToolLabel("chain", selectable=True, default=True),
                value=(begin, follow),
            ),
            Capability(
                label=ToolLabel("disabled", selectable=True, default=True),
                value=begin.copy(name="disabled"),
            ),
            Capability(
                label=ToolLabel("stop", selectable=True, default=True), value=stop
            ),
        ),
    )
    agent.set_capability_selection({"stop": True, "chain": True})
    runtime, _ = agent.build()
    assert runtime.default_tools == [begin, stop] and runtime.tools == [follow]
    result, _ = runtime.invoke()
    assert result.value == "done" and calls == ["begin", "follow"]
