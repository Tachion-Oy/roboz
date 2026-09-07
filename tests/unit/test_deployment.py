from roboz import Empty, Message, Skill, Str, stop, tool
from roboz.deployment import AgentDefinition, Capability
from roboz.llm import MockLLMEndpoint
from roboz.runtime import Output


def test_capabilities_build_all_four_surfaces_and_preserve_chain_identity():
    calls = []
    built_pipes = []

    class Capabilities:
        def build(self, pipe, agent_endpoint):
            built_pipes.append(pipe)

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
