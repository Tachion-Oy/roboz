"""Deployments inspect constructed agents without invoking their tools."""

from types import SimpleNamespace

import pytest

from roboz.agent import AgentMode
from roboz.models import Message, Str
from roboz import Skill, factory
from roboz.tools import stop
from roboz.dependencies import ExecutableDependency
from roboz.deployment import AgentCapability, Capability, DeployableAgent
from roboz.llm import LLMEndpoint, MockLLMEndpoint


def _endpoint(name):
    def forbidden():
        pytest.fail("inspection initialized or checked a client")

    return LLMEndpoint(
        client=SimpleNamespace(
            chat=object(), models=object(), close=forbidden, materialize=forbidden
        ),
        api_name="test",
        model_name=name,
    )


def _definition(name, endpoint=None, **options):
    definition = DeployableAgent(
        name=name, system_prompt="Complete the task.", **options
    )
    definition.set_agent_endpoint(
        endpoint if endpoint is not None else MockLLMEndpoint([])
    )
    return definition


@factory
def describe_resource(
    input: Str, messages: list[Message], ctx: ExecutableDependency
) -> Str:
    pytest.fail("inspection invoked a tool")


def _resource_tool(name, resource=None):
    return describe_resource(resource or ExecutableDependency(name)).copy(name=name)


def test_inspection_builds_all_agent_modes_without_invoking_or_materializing(tmp_path):
    endpoints = [
        _endpoint(name) for name in ("root", "foreground", "background", "nested")
    ]
    received = []
    program = ExecutableDependency("program")

    class Feature(AgentCapability):
        @property
        def required_attributes(self):
            return {}

        def build(self, agent, pipe):
            received.append(agent)
            assert pipe.event_sinks == ()
            return Capability(tools=(_resource_tool("program", program),))

    nested = _definition("nested", endpoints[3])
    background = _definition("background", endpoints[2], subagents=(nested,))
    foreground = _definition(
        "foreground", endpoints[1], default_capabilities=(Feature(),)
    )
    root = _definition(
        "root",
        endpoints[0],
        default_capabilities=(Feature(),),
        subagents=(foreground,),
        background_agents=(background,),
    )
    absent = tmp_path / "unused"
    root.set_initial_messages((absent,))

    resources = root.external_dependencies()
    assert {id(item) for item in resources} == {
        id(item) for item in (*endpoints, program)
    }
    assert len(resources) == 5
    assert received == [root, foreground]
    assert not absent.exists()


def test_inspection_uses_agent_tool_and_skill_coverage_and_first_resource():
    first = ExecutableDependency("same", display_name="first")
    later = ExecutableDependency("same", display_name="later")
    default = _resource_tool("default", first)
    duplicate = _resource_tool("duplicate", later)
    direct = _resource_tool("direct")
    unloaded = _resource_tool("unloaded")
    automatic = _resource_tool("automatic")
    feature = Capability(
        tools=((direct, duplicate),),
        default_tools=(default,),
        skills=(
            Skill(
                name="lazy", description="Lazy", instructions="Lazy", tools=(unloaded,)
            ),
        ),
        auto_loaded_skills=(
            Skill(
                name="auto", description="Auto", instructions="Auto", tools=(automatic,)
            ),
        ),
    )
    definition = _definition("root", default_capabilities=(feature,))
    runtime, _ = definition.build()
    resources = definition.external_dependencies()
    assert resources == runtime.external_dependencies()
    assert {item.dependency_id for item in resources} == {
        "executable:same",
        "executable:direct",
        "executable:unloaded",
        "executable:automatic",
    }
    assert resources[0] is first


def test_inspection_uses_current_configuration_and_fresh_capability_state():
    pipes = []

    class Feature(AgentCapability):
        @property
        def required_attributes(self):
            return {}

        def build(self, agent, pipe):
            pipes.append(pipe)
            return Capability(tools=(_resource_tool("program"),))

    first, second = _endpoint("first"), _endpoint("second")
    definition = _definition("root", first, default_capabilities=(Feature(),))
    assert definition.external_dependencies()[0] is first
    definition.set_agent_endpoint(second)
    definition.add_subagents(_definition("child", first))
    resources = definition.external_dependencies()
    assert resources[0] is second
    assert first in resources
    runtime, _ = definition.build()
    assert len({id(pipe) for pipe in pipes}) == 3
    assert runtime.pipe is pipes[-1]
    assert runtime.messages == []


@pytest.mark.parametrize("invalid", [[ExecutableDependency("python")], ("python",)])
def test_tool_inspection_errors_propagate_through_deployment(invalid):
    class Context:
        def external_dependencies(self):
            return invalid

    @factory
    def inspect_value(input: Str, messages: list[Message], ctx: Context) -> Str:
        pytest.fail("inspection invoked a tool")

    definition = _definition(
        "invalid", default_capabilities=(Capability(tools=(inspect_value(Context()),)),)
    )
    with pytest.raises(TypeError):
        definition.external_dependencies()


def test_inspection_propagates_capability_construction_errors():
    error = RuntimeError("construction failed")

    class Broken(AgentCapability):
        @property
        def required_attributes(self):
            return {}

        def build(self, agent, pipe):
            raise error

    with pytest.raises(RuntimeError) as raised:
        _definition("broken", default_capabilities=(Broken(),)).external_dependencies()
    assert raised.value is error


def test_resource_free_definition_can_be_inspected_built_and_invoked():
    definition = DeployableAgent(
        name="worker",
        mode=AgentMode.DETERMINISTIC,
        default_capabilities=(Capability(default_tools=(stop,)),),
    )
    definition.set_agent_endpoint(_endpoint("unused"))
    assert definition.external_dependencies() == ()
    runtime, background_agents = definition.build()
    assert background_agents == ()
    assert runtime.external_dependencies() == ()
    result, _ = runtime.invoke(input=Str(value="done"))
    assert result.value == "done"
