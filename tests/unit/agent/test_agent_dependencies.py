import pytest

from roboz import Ctx
from roboz.agent.background_agent import run_background_agent
from roboz.agent.core import Agent
from roboz.agent.subagent import run_subagent
from roboz.llm.endpoints import LLMEndpoint, MockLLMEndpoint
from roboz.models import Empty, Message
from roboz.skill.core import Skill
from roboz.tooling.decorators import factory
from roboz.dependencies import ExecutableDependency


@factory
def uses_executable(input: Empty, messages: list[Message], ctx: Ctx) -> Empty:
    return input


def _bound(name: str):
    return uses_executable(Ctx(executable=ExecutableDependency(name))).copy(
        name=f"use_{name}"
    )


def _agent(*, endpoint=None) -> Agent:
    active = _bound("active")
    passive = _bound("passive").copy(chained_to=active)
    default = _bound("default")
    skill = Skill(
        name="lazy_skill",
        description="lazy",
        instructions="lazy",
        tools=[_bound("skill")],
    )
    return Agent(
        name="dependency_agent",
        system_prompt="Use dependencies.",
        tools=[active, passive],
        skills=[skill],
        default_tools=[default],
        agent_endpoint=endpoint
        or LLMEndpoint(client=object(), api_name="test", model_name="agent-model"),
    )


def _ids(agent: Agent, *, include_lazy_skills: bool = True) -> set[str]:
    return {
        dependency.dependency_id
        for dependency in agent.external_dependencies(
            include_lazy_skills=include_lazy_skills
        )
    }


def test_agent_dependency_view_covers_complete_tool_graph() -> None:
    agent = _agent()

    assert _ids(agent) == {
        "model:test:agent-model",
        "executable:active",
        "executable:passive",
        "executable:default",
        "executable:skill",
    }
    assert _ids(agent, include_lazy_skills=False) == {
        "model:test:agent-model",
        "executable:active",
        "executable:passive",
        "executable:default",
    }
    assert agent.master_tool.external_dependencies[0].dependency_id == (
        "model:test:agent-model"
    )


def test_dynamic_tools_update_agent_dependency_view() -> None:
    agent = _agent()

    agent.add(tools=[_bound("dynamic")])

    assert "executable:dynamic" in _ids(agent)


def test_mock_endpoint_does_not_add_an_external_dependency() -> None:
    agent = _agent(endpoint=MockLLMEndpoint(responses=[]))

    assert not any(dependency_id.startswith("model:") for dependency_id in _ids(agent))


def test_callable_endpoint_is_rejected() -> None:
    with pytest.raises(TypeError, match="agent_endpoint"):
        _agent(endpoint=lambda: MockLLMEndpoint(responses=[]))


def test_agent_wrappers_derive_live_child_tool_graph() -> None:
    child = _agent()
    expected = _ids(child)

    ctx = Ctx(agent=child)
    nested = Ctx(child=ctx)
    assert {d.dependency_id for d in ctx.external_dependencies()} == expected
    subagent_tool = run_subagent(ctx)
    background_tool = run_background_agent(Ctx(agent=child))
    copied_subagent_tool = subagent_tool.copy()
    nested_tool = uses_executable(nested)
    parent = Agent(
        name="parent",
        system_prompt="Use the child.",
        tools=[subagent_tool],
        agent_endpoint=MockLLMEndpoint(responses=[]),
    )

    assert {
        dependency.dependency_id for dependency in subagent_tool.external_dependencies
    } == expected
    assert {
        dependency.dependency_id for dependency in background_tool.external_dependencies
    } == expected

    child.add(tools=[_bound("added_after_wrapping")])
    updated = expected | {"executable:added_after_wrapping"}

    assert {d.dependency_id for d in ctx.external_dependencies()} == updated
    assert {d.dependency_id for d in nested.external_dependencies()} == updated
    assert _ids(parent) == updated
    for wrapper in (subagent_tool, background_tool, copied_subagent_tool, nested_tool):
        assert {
            dependency.dependency_id for dependency in wrapper.external_dependencies
        } == updated
