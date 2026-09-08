from pathlib import Path

from roboshed.agents import orchestrator

from roboz import Empty, Message, Str, tool
from roboz.deployment import AgentCapability, Capability
from roboz.llm import MockLLMEndpoint
from roboz.runtime import PersistenceSink, RunLifecycleEvent


def test_orchestrator_uses_injected_capabilities_and_pipe(tmp_path: Path):
    seen = []
    pipes = []

    @tool
    def custom(input: Empty, messages: list[Message]) -> Str:
        seen.append(True)
        return Str(value="custom result")

    class CustomCapability(AgentCapability):
        def build(self, pipe, *, default_endpoint):
            pipes.append(pipe)
            return Capability(tools=(custom,))

    events = []
    agent = orchestrator(
        agent_endpoint=MockLLMEndpoint(
            [
                {"action": "custom", "rationale": "test extension"},
                {"action": "stop", "rationale": "done", "value": "ok"},
            ]
        ),
        capabilities=[CustomCapability()],
    ).build(event_sinks=(events.append, PersistenceSink.for_path(tmp_path / "logs")))
    result, _ = agent.invoke()
    assert result.value == "ok"
    assert seen == [True]
    assert pipes == [agent.pipe]
    assert any(
        isinstance(event, RunLifecycleEvent) and event.kind == "stopped"
        for event in events
    )
