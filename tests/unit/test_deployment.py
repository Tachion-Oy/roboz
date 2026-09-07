from roboz import Empty, Message, Skill, Str, stop, tool
from roboz.deployment import AgentCapability, AgentDefinition, Capability
from roboz.llm import MockLLMEndpoint
from roboz.runtime import Output


def test_capabilities_build_all_four_surfaces_and_preserve_chain_identity():
    calls = []
    built_pipes = []
    received_endpoints = []

    class Capabilities(AgentCapability):
        def build(self, pipe, *, default_endpoint):
            built_pipes.append(pipe)
            received_endpoints.append(default_endpoint)

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

    definition = AgentDefinition(
        name="test",
        agent_endpoint=MockLLMEndpoint(
            [
                {"action": "research", "rationale": "load instructions"},
                {"action": "stop", "rationale": "done", "value": "ok"},
            ]
        ),
        system_prompt="Exercise the capabilities.",
        capabilities=(
            Capability(tools=(stop,)),
            Capabilities(),
        ),
        interaction_mode=Output.API,
    )
    agent = definition.build()
    second = definition.build()
    assert built_pipes == [agent.pipe, second.pipe]
    assert all(endpoint is definition.agent_endpoint for endpoint in received_endpoints)
    assert agent.pipe is not second.pipe
    assert agent.default_tools[0] is not second.default_tools[0]
    assert agent.default_tools[0] in agent.tools
    result, messages = agent.invoke()
    assert result.value == "ok"
    assert calls == ["default", "handoff", "default", "handoff"]
    text = "\n".join(message.content for message in messages)
    assert "AUTO_LOADED_MARKER" in text and "ON_DEMAND_MARKER" in text


def test_duplicate_names_fail_before_building_capabilities_or_sinks():
    import pytest

    from roboz.deployment import SubAgentSpec

    def unexpected(_name):
        raise AssertionError("Must validate names before allocating sinks")

    child = AgentDefinition(name="duplicate", agent_endpoint=None, is_agentic=False)
    root = AgentDefinition(
        name="duplicate",
        agent_endpoint=None,
        is_agentic=False,
        subagents=(SubAgentSpec(child, "delegate", "Delegate."),),
    )
    with pytest.raises(ValueError, match="unique"):
        root.build(event_sink_factory=unexpected)


def test_each_capability_receives_its_owning_agents_default():
    from roboz.deployment import SubAgentSpec

    received = []

    class Feature:
        def build(self, pipe, *, default_endpoint):
            received.append((pipe, default_endpoint))
            return Capability(tools=(stop,))

    parent_endpoint, child_endpoint = MockLLMEndpoint([]), MockLLMEndpoint([])
    child = AgentDefinition(
        name="child",
        system_prompt="Complete the task.",
        agent_endpoint=child_endpoint,
        capabilities=(Feature(),),
    )
    parent = AgentDefinition(
        name="parent",
        system_prompt="Delegate the task.",
        agent_endpoint=parent_endpoint,
        capabilities=(Feature(),),
        subagents=(SubAgentSpec(child, "delegate", "Run the child."),),
    ).build()

    assert received[0] == (parent.pipe, parent_endpoint)
    assert received[1][0] is not parent.pipe
    assert received[1][1] is child_endpoint
