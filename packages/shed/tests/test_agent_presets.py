from pathlib import Path

from roboshed.agents import librarian, orchestrator
from roboshed.capabilities import (
    ArtifactRetention,
    ConversationSnapshots,
    FileCommands,
    FileEditing,
    MaintenanceCadence,
    MemoryConsolidation,
)
from roboshed.deployments import Deployment
from roboshed.sandbox import Sandbox
from roboz import Empty, Message, Str, tool
from roboz.deployment import AgentCapability, Capability
from roboz.llm import MockLLMEndpoint
from roboz.runtime import PersistenceSink, RunLifecycleEvent


def test_role_constructors_own_their_builtin_capabilities(tmp_path: Path):
    sandbox = Sandbox(tmp_path)
    endpoint = MockLLMEndpoint([])
    orchestrator_definition = orchestrator(sandbox, agent_endpoint=endpoint)
    librarian_definition = librarian(
        sandbox, {"orchestrator"}, agent_endpoint=endpoint
    )

    assert [type(capability) for capability in orchestrator_definition.capabilities] == [
        Capability,
        FileCommands,
        FileEditing,
    ]
    assert [type(capability) for capability in librarian_definition.capabilities] == [
        ConversationSnapshots,
        MemoryConsolidation,
        ArtifactRetention,
        MaintenanceCadence,
    ]


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
    sandbox = Sandbox(tmp_path)
    sandbox.configure_scope("project")
    definition = orchestrator(
        sandbox,
        agent_endpoint=MockLLMEndpoint(
            [
                {"action": "custom", "rationale": "test extension"},
                {"action": "stop", "rationale": "done", "value": "ok"},
            ]
        ),
    )
    agent, _ = Deployment(
        agent=definition,
        sandbox=sandbox,
        additional_capabilities=(CustomCapability(),),
        event_sinks=[events.append, PersistenceSink.for_path(tmp_path / "logs")],
    ).build()
    result, _ = agent.invoke()
    assert result.value == "ok"
    assert seen == [True]
    assert pipes == [agent.pipe]
    assert any(
        isinstance(event, RunLifecycleEvent) and event.kind == "stopped"
        for event in events
    )
