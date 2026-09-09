"""Exercise guarded file tools through the real agent and event pipeline."""

import json
import tempfile
from pathlib import Path

from roboshed.agents import librarian as librarian_definition
from roboshed.capabilities import (
    ArtifactRetention,
    ConversationSnapshots,
    FileCommands,
    FileEditing,
    MaintenanceCadence,
    MemoryConsolidation,
)
from roboshed.sandbox import PermissionPolicy, Sandbox

from roboz import Agent, stop
from roboz.deployment import AgentDefinition, Capability
from roboz.llm import MockLLMEndpoint
from roboz.runtime import EventPipe, Output, PersistenceSink


def test_guarded_read_edit_read_and_denied_escape(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    note = sandbox / "note.txt"
    note.write_text("before-marker")
    outside = tmp_path / "private.txt"
    outside.write_text("private-marker")
    (sandbox / "escape.txt").symlink_to(outside)

    def read(path: str) -> dict:
        return {
            "action": "run_file_command",
            "rationale": "read",
            "chain": "and",
            "file_commands": [{"command": "cat", "argv": [path]}],
        }

    permissions = PermissionPolicy.local(sandbox)
    agent = AgentDefinition(
        name="file_worker",
        system_prompt="Complete the file task and stop.",
        capabilities=(
            Capability(tools=(stop,)),
            FileCommands(permissions),
            FileEditing(permissions),
        ),
        interaction_mode=Output.API,
        agent_endpoint=MockLLMEndpoint(
            [
                read("note.txt"),
                {
                    "action": "apply_patch",
                    "rationale": "edit",
                    "path": "note.txt",
                    "old_string": "before-marker",
                    "new_string": "after-marker",
                },
                read("note.txt"),
                read("../private.txt"),
                read("escape.txt"),
                {
                    "action": "apply_patch",
                    "rationale": "escape",
                    "path": str(outside),
                    "old_string": "private-marker",
                    "new_string": "compromised",
                },
                {"action": "stop", "rationale": "finished", "value": "done"},
            ]
        ),
    ).build(event_sinks=(PersistenceSink.for_path(tmp_path / "logs"),))
    result, messages = agent.invoke()
    assert result.value == "done"
    assert note.read_text() == "after-marker"
    assert outside.read_text() == "private-marker"
    # Tool outputs, not model requests, prove both reads and guard denials.
    outputs = [
        json.loads(message.content)
        for message in messages
        if message.role.value == "user"
    ]
    reads = [
        output["value"]
        for output in outputs
        if output.get("caller") == "execute_file_command"
    ]
    assert len(reads) == 2
    assert "before-marker" in reads[0] and "after-marker" in reads[1]
    assert all("private-marker" not in value for value in reads)
    denials = [
        output
        for output in outputs
        if output.get("caller") == "operation_guard"
        and output.get("status") == "denied"
    ]
    assert len(denials) == 3
    assert list((tmp_path / "logs").rglob("*.json"))


def test_conversation_snapshot_memory_retention(tmp_path: Path) -> None:
    _conversation_snapshot_memory_retention(tmp_path, separate_endpoints=False)


def test_conversation_snapshot_memory_with_separate_models(tmp_path: Path) -> None:
    _conversation_snapshot_memory_retention(tmp_path, separate_endpoints=True)


def _conversation_snapshot_memory_retention(
    tmp_path: Path, *, separate_endpoints: bool
) -> None:
    sandbox = Sandbox(tmp_path)
    project_slug = "test"
    logs = sandbox.project_logs_dir(project_slug)
    snapshots = sandbox.project_snapshots_dir(project_slug)
    memory_dir = sandbox.project_memory_dir(project_slug)
    author = Agent(
        name="author",
        interaction_mode=None,
        tools=[stop],
        system_prompt="Remember the project decision.",
        initial_messages=["The project uses a blue robot emblem."],
        event_pipe=EventPipe(
            event_sinks=[PersistenceSink.for_path(logs / "author")]
        ),
        agent_endpoint=MockLLMEndpoint(
            [
                {
                    "action": "stop",
                    "rationale": "record decision",
                    "value": "Use the blue robot.",
                },
            ]
        ),
    )
    author.invoke()
    source = next((logs / "author").rglob("*.json"))
    responses = [
        {"value": "The project uses a blue robot emblem."},
        {"value": "Retain the blue robot emblem decision."},
    ]
    librarian = librarian_definition(
        agent_endpoint=None if separate_endpoints else MockLLMEndpoint(responses),
        capabilities=(
            ConversationSnapshots(
                sandbox,
                project_slug,
                {"author"},
                endpoint=MockLLMEndpoint(responses[:1]) if separate_endpoints else None,
                token_growth_threshold=1,
            ),
            MemoryConsolidation(
                sandbox,
                project_slug,
                {"author"},
                endpoint=MockLLMEndpoint(responses[1:]) if separate_endpoints else None,
                min_pending_snapshots=1,
            ),
            ArtifactRetention(
                sandbox,
                project_slug,
                max_snapshot_files=0,
                max_log_files=0,
                max_memory_files=1,
            ),
            MaintenanceCadence(sandbox, project_slug, {"author"}, seconds=0),
        ),
    ).build(
        event_sink_factory=lambda name: (PersistenceSink.for_path(logs / name),)
    )
    result, _ = librarian.invoke()
    assert "project idle" in result.value
    memory = list(memory_dir.glob("*.md"))
    assert len(memory) == 1
    assert "blue robot emblem" in memory[0].read_text()
    # Retention happens after consolidation: the memory survives its sources.
    assert not source.exists()
    assert not list(snapshots.rglob("*.md"))
    assert not any(path.is_dir() for path in snapshots.iterdir())
    assert list((logs / "librarian").rglob("*.json"))


def test_persistent_orchestrator_delegates_and_accepts_another_request(
    tmp_path: Path,
) -> None:
    from roboshed.agents import orchestrator
    from roboshed.deployments.robosprawl import AgenticFactory

    from roboz.deployment import AgentDefinition, Capability, SubAgentSpec
    from roboz.runtime import Output, bind_api_user_io, reset_api_user_io

    class Replies:
        def __init__(self):
            self.prompts = []
            self.answers = iter(("Do another task.", "Stop the session."))

        def request_input(self, message, timeout=None):
            self.prompts.append(message)
            return next(self.answers)

        def notify(self, message):
            raise AssertionError("This scenario expects questions with replies")

    child = AgentDefinition(
        name="specialist",
        system_prompt="Complete the delegated task.",
        agent_endpoint=MockLLMEndpoint(
            [{"action": "stop", "rationale": "done", "value": "specialist result"}]
        ),
        capabilities=(Capability(tools=(stop,)),),
        interaction_mode=Output.API,
    )
    definition = orchestrator(
        agent_endpoint=MockLLMEndpoint(
            [
                {
                    "action": "prompt_user",
                    "rationale": "first task complete",
                    "value": "First task done. What next?",
                },
                {"action": "delegate", "rationale": "handle next task"},
                {
                    "action": "prompt_user",
                    "rationale": "remain available",
                    "value": "Second task done. What next?",
                },
                {
                    "action": "stop",
                    "rationale": "user requested stop",
                    "value": "session ended",
                },
            ]
        ),
        subagents=(SubAgentSpec(child, "delegate", "Run the specialist."),),
        interaction_mode=Output.API,
    )
    sandbox = Sandbox(tmp_path)
    project_slug = "collaboration"
    events = []
    bundle = AgenticFactory(
        sandbox=sandbox, project_slug=project_slug, orchestrator=definition
    ).build(
        event_sinks=(events.append,)
    )
    replies = Replies()
    token = bind_api_user_io(replies)
    try:
        result, messages = bundle.agent.invoke()
    finally:
        reset_api_user_io(token)
    assert result.value == "session ended"
    assert len(replies.prompts) == 2
    assert any("specialist result" in m.content for m in messages)
    logs = sandbox.project_logs_dir(project_slug)
    assert list((logs / "orchestrator").rglob("*.json"))
    assert list((logs / "specialist").rglob("*.json"))
    assert any(getattr(event, "agent_name", None) == "specialist" for event in events)
    assert bundle.background_agents == ()


def test_repeated_deployment_construction_without_a_web_host(tmp_path: Path) -> None:
    from roboshed.agents import orchestrator
    from roboshed.deployments.robosprawl import AgenticFactory, DeploymentFactory
    from roboz import ExternalDependencyKind, LazyExternalDependency
    from roboz.llm import LLMEndpoint

    sandbox = Sandbox(tmp_path)
    project_slug = "standalone"
    endpoints = []
    routes = []
    observed = []
    sink_calls = []
    borrowed = LazyExternalDependency(
        "model:test:borrowed", ExternalDependencyKind.MODEL_ENDPOINT, {},
        lambda: LLMEndpoint(client=object(), api_name="test", model_name="borrowed", max_context_tokens=4096),
    )

    def recipe(sandbox, project_slug, *, orchestrator_endpoint):
        routes.append(orchestrator_endpoint)
        endpoint = MockLLMEndpoint([
            {"action": "stop", "rationale": "user requested stop", "value": "complete"},
        ])
        endpoints.append(endpoint)
        return AgenticFactory(
            sandbox=sandbox,
            project_slug=project_slug,
            orchestrator=orchestrator(agent_endpoint=endpoint, interaction_mode=Output.CLI),
        )

    def sinks():
        events = []
        sink_calls.append(events)
        return (events.append,)

    deployment = DeploymentFactory(recipe, event_sink_factory=sinks)
    first = deployment(
        sandbox,
        project_slug,
        endpoint_getter=lambda: borrowed,
        event_sinks=(observed.append,),
    )
    second = deployment(
        sandbox,
        project_slug,
        endpoint_getter=lambda: borrowed,
        event_sinks=(observed.append,),
    )
    assert not sandbox.project_dir(project_slug).exists()
    assert first.agent is not second.agent
    assert first.agent.pipe is not second.agent.pipe
    assert endpoints[0] is not endpoints[1]
    assert routes[0] is not routes[1]
    assert routes[0].external_dependencies() == routes[1].external_dependencies() == (borrowed,)
    assert "materialized" not in borrowed.__dict__
    assert routes[0].materialize() is routes[1].materialize()
    assert first.agent.invoke()[0].value == second.agent.invoke()[0].value == "complete"
    assert sink_calls[0] and sink_calls[1] and observed
    assert list(sandbox.project_logs_dir(project_slug).rglob("*.json"))


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        test_guarded_read_edit_read_and_denied_escape(Path(directory))
    print("PASS guarded read/edit/read and denied escape")

    for scenario in (
        test_repeated_deployment_construction_without_a_web_host,
        test_conversation_snapshot_memory_retention,
        test_conversation_snapshot_memory_with_separate_models,
        test_persistent_orchestrator_delegates_and_accepts_another_request,
    ):
        with tempfile.TemporaryDirectory() as directory:
            scenario(Path(directory))
        print(f"PASS {scenario.__name__}")
