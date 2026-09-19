import pytest

from roboshed.sandbox import Sandbox
from roboz.agent import AgentMode
from roboz.models import Empty, Message, Stop
from roboz.tools import stop
from roboz import tool
from roboz.deployment import Capability, DeployableAgent
from roboz.llm import MockLLMEndpoint
from roboz.runtime import default_event_sinks


def _definition(name, subagents=(), background_agents=()):
    definition = DeployableAgent(
        name=name,
        system_prompt="Complete the task.",
        subagents=subagents,
        background_agents=background_agents,
        default_capabilities=(Capability(tools=(stop,)),),
    )
    definition.set_agent_endpoint(MockLLMEndpoint([]))
    return definition


@pytest.mark.parametrize("background", [False, True])
def test_child_slot_selects_invocation_behavior(background):
    from threading import Event, current_thread

    entered, release = Event(), Event()
    threads = []
    pipes = []

    class Work:
        @property
        def required_attributes(self):
            return {}

        def build(self, agent, pipe):
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
        mode=AgentMode.DETERMINISTIC,
        default_capabilities=(Work(),),
    )
    definition = _definition(
        "root",
        subagents=() if background else (child,),
        background_agents=(child,) if background else (),
    )
    responses = [] if background else [{"action": "worker", "rationale": "delegate"}]
    responses.append({"action": "stop", "rationale": "done", "value": "root finished"})
    definition.set_agent_endpoint(MockLLMEndpoint(responses))

    root, backgrounds = definition.build()
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


def test_build_collects_nested_backgrounds_and_isolates_sinks_and_state(tmp_path):
    pipes = {}

    class RecordPipe:
        def __init__(self, name):
            self.name = name

        @property
        def required_attributes(self):
            return {}

        def build(self, agent, pipe):
            pipes[self.name] = pipe
            return Capability(tools=(stop,))

    def node(name, subagents=(), background_agents=()):
        definition = DeployableAgent(
            name=name,
            system_prompt="Complete the task.",
            subagents=subagents,
            background_agents=background_agents,
            default_capabilities=(RecordPipe(name),),
        )
        definition.set_agent_endpoint(MockLLMEndpoint([]))
        return definition

    background = node(
        "background",
        subagents=(node("background_child"),),
        background_agents=(node("nested"),),
    )
    child = node("child", background_agents=(background,))
    definition = node("root", subagents=(child,), background_agents=(node("other"),))
    definition.set_initial_messages(("existing",))
    caller_events = []
    sandbox = Sandbox(tmp_path)
    sandbox.configure_scope("scope")

    def sinks(name):
        return default_event_sinks(
            data_path=sandbox.project_logs_dir() / name,
            include_cli=False,
        )

    root, backgrounds = definition.build(
        event_sinks=(caller_events.append,), event_sink_factory=sinks
    )
    assert [agent.name for agent in backgrounds] == ["background", "nested", "other"]
    foreground = definition.agent_names(include_background=False)
    assert foreground == {"root", "child"}
    assert root.initial_messages == ("existing",)
    assert definition.initial_messages == ("existing",)
    assert not list(tmp_path.iterdir())
    for name, pipe in pipes.items():
        assert pipe.data_path == sandbox.project_logs_dir() / name
        assert (caller_events.append in pipe.event_sinks) is (name in foreground)
    for agent in backgrounds:
        assert agent.pipe is pipes[agent.name]
        agent.pipe.cancel()
    assert not root.pipe.cancelled

    second, second_backgrounds = definition.build(event_sink_factory=sinks)
    assert second.pipe is not root.pipe
    for first, fresh in zip(backgrounds, second_backgrounds, strict=True):
        assert fresh.pipe is not first.pipe
        assert not fresh.pipe.cancelled


@pytest.mark.parametrize("collision", ["root", "foreground", "background", "nested"])
def test_build_rejects_cross_branch_names_before_sinks(collision, tmp_path):
    background = _definition("background", subagents=(_definition("nested"),))
    definition = _definition(
        "root",
        subagents=(_definition("foreground"),),
        background_agents=(background, _definition(collision)),
    )

    def unexpected(_name):
        raise AssertionError("must validate before creating sinks")

    with pytest.raises(ValueError, match="unique"):
        definition.build(event_sink_factory=unexpected)
    assert not list(tmp_path.iterdir())
    with pytest.raises(ValueError, match="unique"):
        definition.agent_names(include_background=False)
