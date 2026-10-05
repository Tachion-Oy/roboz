"""Credential-free E2E contracts, also executed from installed wheels."""

from roboz.deployment import ToolLabel
import tempfile
from pathlib import Path

from roboz import Agent
from roboz.tools import stop
from roboz.agent.nested_agent import run_nested_agent
from roboz.llm import MockLLMEndpoint
from roboz.runtime import EventPipe, PersistenceSink, RunLifecycleEvent


def test_nested_agent_completion(tmp_path: Path) -> None:
    events = []
    child = Agent(
        name="child",
        tools=[stop],
        system_prompt="Finish.",
        agent_endpoint=MockLLMEndpoint(
            [
                {"action": "stop", "rationale": "finished", "value": "child completed"},
            ]
        ),
    )
    delegate = run_nested_agent(child)
    parent = Agent(
        name="parent",
        tools=[delegate, stop],
        system_prompt="Delegate, then finish.",
        event_pipe=EventPipe(
            event_sinks=[events.append, PersistenceSink.for_path(tmp_path)]
        ),
        agent_endpoint=MockLLMEndpoint(
            [
                {"action": delegate.name, "rationale": "delegate"},
                {
                    "action": "stop",
                    "rationale": "finished",
                    "value": "parent completed",
                },
            ]
        ),
    )
    result, messages = parent.invoke()
    assert result.value == "parent completed"
    assert any("child completed" in message.content for message in messages)
    assert any(
        isinstance(event, RunLifecycleEvent) and event.kind == "stopped"
        for event in events
    )
    assert list(tmp_path.rglob("*.json"))


def test_generic_definitions_build_without_application_packages(tmp_path: Path) -> None:
    from roboz.models import Empty, Message
    from roboz import tool
    from roboz.deployment import DeployableAgent, Capability

    root_ticks = []

    @tool
    def root_tick(input: Empty, messages: list[Message]) -> Empty:
        root_ticks.append("tick")
        return Empty()

    child = DeployableAgent(
        name="worker",
        capabilities=(Capability(label=ToolLabel("stop"), value=stop),),
        system_prompt="Finish the delegated work.",
    )
    child.set_agent_endpoint(
        MockLLMEndpoint(
            [{"action": "stop", "rationale": "done", "value": "worker result"}]
        )
    )
    root = DeployableAgent(
        name="coordinator",
        capabilities=(Capability(label=ToolLabel("stop"), value=stop),),
        nested_agents=(child,),
        system_prompt="Delegate, then finish.",
    )
    root.set_agent_endpoint(
        MockLLMEndpoint(
            [
                {"action": "worker", "rationale": "ask worker"},
                {"action": "stop", "rationale": "done", "value": "all done"},
            ]
        )
    )
    root.set_initial_messages(("Explicit caller-provided context.",))
    bare, bare_backgrounds = root.build()
    assert bare_backgrounds == ()
    assert bare.pipe.event_sinks == ()
    assert bare.pipe.data_path is None
    assert tuple(bare.initial_messages) == root.initial_messages
    assert list(tmp_path.iterdir()) == []

    shared_events = []
    sink_batches = {"coordinator": [], "worker": []}

    def sinks(name):
        own_events = []
        sink_batches[name].append(own_events)
        # Arbitrary caller-selected locations: no project layout is required.
        return (
            own_events.append,
            PersistenceSink.for_path(tmp_path / f"{name}-history"),
        )

    root.add_capabilities(
        Capability(label=ToolLabel("root_tick", default=True), value=root_tick)
    )
    agent, backgrounds = root.build(
        event_sinks=(shared_events.append,),
        event_sink_factory=sinks,
    )
    another, another_backgrounds = root.build(event_sink_factory=sinks)
    assert backgrounds == another_backgrounds == ()
    assert agent.pipe is not another.pipe
    assert all(len(batches) == 2 for batches in sink_batches.values())
    assert list(tmp_path.iterdir()) == []
    result, messages = agent.invoke()
    assert result.value == "all done"
    assert any("worker result" in message.content for message in messages)
    assert root_ticks == [
        "tick",
        "tick",
    ]  # Root capabilities must not run on the child.
    for name, batches in sink_batches.items():
        assert batches[0] and not batches[1]
        assert all(getattr(event, "agent_name", name) == name for event in batches[0])
        assert list((tmp_path / f"{name}-history").rglob("*.json"))
    assert {getattr(event, "agent_name", None) for event in shared_events} >= {
        "coordinator",
        "worker",
    }


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        test_nested_agent_completion(Path(directory))
    print("PASS nested agent completion")

    with tempfile.TemporaryDirectory() as directory:
        test_generic_definitions_build_without_application_packages(Path(directory))
    print("PASS generic definitions without application packages")
