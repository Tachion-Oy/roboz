import pytest

from roboz import Empty, Message, Skill, Stop, Str, stop, tool
from roboz.deployment import AgentCapability, DeployableAgent, Capability, Deployment
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

    definition = DeployableAgent(
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

    def unexpected(_name):
        raise AssertionError("Must validate names before allocating sinks")

    child = DeployableAgent(name="duplicate", agent_endpoint=None, is_agentic=False)
    root = DeployableAgent(
        name="duplicate",
        agent_endpoint=None,
        is_agentic=False,
        subagents=(child,),
    )
    with pytest.raises(ValueError, match="unique"):
        root.build(event_sink_factory=unexpected)


def test_each_capability_receives_its_owning_agents_default():
    received = []

    class Feature:
        def build(self, pipe, *, default_endpoint):
            received.append((pipe, default_endpoint))
            return Capability(tools=(stop,))

    parent_endpoint, child_endpoint = MockLLMEndpoint([]), MockLLMEndpoint([])
    child = DeployableAgent(
        name="child",
        system_prompt="Complete the task.",
        agent_endpoint=child_endpoint,
        capabilities=(Feature(),),
    )
    parent = DeployableAgent(
        name="parent",
        system_prompt="Delegate the task.",
        agent_endpoint=parent_endpoint,
        capabilities=(Feature(),),
        subagents=(child,),
    ).build()

    assert received[0] == (parent.pipe, parent_endpoint)
    assert received[1][0] is not parent.pipe
    assert received[1][1] is child_endpoint


def _definition(name, subagents=(), background_agents=()):
    return DeployableAgent(
        name=name,
        agent_endpoint=MockLLMEndpoint([]),
        system_prompt="Complete the task.",
        subagents=subagents,
        background_agents=background_agents,
        capabilities=(Capability(tools=(stop,)),),
    )


@pytest.mark.parametrize("background", [False, True])
def test_child_slot_selects_invocation_behavior(background):
    from dataclasses import replace
    from threading import Event, current_thread

    entered, release = Event(), Event()
    threads = []
    pipes = []

    class Work:
        def build(self, pipe, *, default_endpoint):
            pipes.append(pipe)

            @tool
            def work(input: Empty, messages: list[Message]) -> Stop:
                threads.append(current_thread())
                entered.set()
                if background:
                    assert release.wait(2)
                return Stop(value="finished")

            return Capability(default_tools=(work,))

    child = DeployableAgent(
        name="worker",
        description="Run the worker.",
        agent_endpoint=None,
        is_agentic=False,
        capabilities=(Work(),),
    )
    definition = _definition(
        "root",
        subagents=() if background else (child,),
        background_agents=(child,) if background else (),
    )
    responses = [] if background else [{"action": "worker", "rationale": "delegate"}]
    responses.append({"action": "stop", "rationale": "done", "value": "root finished"})
    definition = replace(definition, agent_endpoint=MockLLMEndpoint(responses))
    root, backgrounds = Deployment(root=definition).build()
    invocation = (root.default_tools if background else root.tools)[-1]
    assert invocation.name == (
        "start_background_agent_worker" if background else "worker"
    )
    assert (invocation in root.active_tools.values()) is not background
    assert not entered.is_set()
    assert len(backgrounds) == int(background)
    try:
        result, messages = root.invoke()
        assert result.value == "root finished"
        assert entered.wait(2)
        if background:
            assert backgrounds[0].pipe is pipes[0]
            assert threads[0] is not current_thread()
            assert threads[0].is_alive()
            assert invocation(input=Empty(), messages=[]).status == "alive"
            backgrounds[0].pipe.interrupt()
            backgrounds[0].pipe.cancel()
            assert pipes[0].interrupted and pipes[0].cancelled
            assert not root.pipe.cancelled
        else:
            assert invocation.description == child.description
            assert any("finished" in message.content for message in messages)
            assert threads == [current_thread()]
    finally:
        release.set()
        if background and threads:
            threads[0].join(2)
            assert not threads[0].is_alive()


def test_deployment_collects_nested_backgrounds_and_isolates_sinks_and_state(tmp_path):
    from dataclasses import replace
    from roboz.runtime import PersistenceSink

    pipes = {}

    class RecordPipe:
        def __init__(self, name):
            self.name = name

        def build(self, pipe, *, default_endpoint):
            pipes[self.name] = pipe
            return Capability(tools=(stop,))

    def node(name, subagents=(), background_agents=()):
        return replace(
            _definition(name, subagents, background_agents),
            capabilities=(RecordPipe(name),),
        )

    background = node(
        "background",
        subagents=(node("background_child"),),
        background_agents=(node("nested"),),
    )
    child = node("child", background_agents=(background,))
    definition = replace(
        node("root", subagents=(child,), background_agents=(node("other"),)),
        initial_messages=("existing",),
    )
    allocated = {}

    def sinks(name):
        events = []
        allocated[name] = events
        return (events.append, PersistenceSink.for_path(tmp_path / name))

    caller_events = []
    deployment = Deployment(
        root=definition,
        initial_messages=("memory",),
        event_sink_factory=sinks,
    )
    root, backgrounds = deployment.build(event_sinks=(caller_events.append,))
    assert [agent.name for agent in backgrounds] == ["background", "nested", "other"]
    foreground = definition.agent_names(include_background=False)
    assert foreground == {"root", "child"}
    assert set(allocated) == definition.agent_names()
    assert root.initial_messages == ("memory", "existing")
    assert definition.initial_messages == ("existing",)
    assert not list(tmp_path.iterdir())
    assert all(not events for events in allocated.values())
    for name, pipe in pipes.items():
        assert pipe.data_path == tmp_path / name
        assert (caller_events.append in pipe.event_sinks) is (name in foreground)
        assert allocated[name].append in pipe.event_sinks
        for other_name, events in allocated.items():
            if other_name != name:
                assert events.append not in pipe.event_sinks
    for agent in backgrounds:
        assert agent.pipe is pipes[agent.name]
        agent.pipe.cancel()
    assert not root.pipe.cancelled
    second, second_backgrounds = deployment.build()
    assert second.pipe is not root.pipe
    for first, fresh in zip(backgrounds, second_backgrounds, strict=True):
        assert fresh.pipe is not first.pipe
        assert not fresh.pipe.cancelled


@pytest.mark.parametrize("collision", ["root", "foreground", "background", "nested"])
def test_deployment_rejects_cross_branch_names_before_build(collision):
    def unexpected(name):
        raise AssertionError("Must validate names before allocating sinks")

    background = _definition("background", subagents=(_definition("nested"),))
    definition = _definition(
        "root",
        subagents=(_definition("foreground"),),
        background_agents=(background, _definition(collision)),
    )
    with pytest.raises(ValueError, match="unique"):
        Deployment(root=definition, event_sink_factory=unexpected).build()
    with pytest.raises(ValueError, match="unique"):
        definition.agent_names(include_background=False)
