"""Exercise guarded file tools through the real agent and event pipeline."""

import json
import tempfile
from dataclasses import replace
from pathlib import Path

from roboshed.agents import librarian as librarian_definition
from roboshed.agents import orchestrator as orchestrator_definition
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
from roboz.deployment import DeployableAgent, Capability
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
    agent = DeployableAgent(
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
    memory_root = sandbox.project_memory_dir(project_slug)
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
    librarian = replace(
        librarian_definition(
            sandbox,
            {"author"},
            agent_endpoint=(
                None if separate_endpoints else MockLLMEndpoint(responses)
            ),
        ),
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
    memory_files = list(memory_root.glob("*.md"))
    assert len(memory_files) == 1
    assert "blue robot emblem" in memory_files[0].read_text()
    # Retention happens after consolidation: the memory survives its sources.
    assert not source.exists()
    assert not list(snapshots.rglob("*.md"))
    assert not any(path.is_dir() for path in snapshots.iterdir())
    assert list((logs / "librarian").rglob("*.json"))


def test_persistent_orchestrator_delegates_and_accepts_another_request(
    tmp_path: Path,
) -> None:
    from roboshed.deployments import Deployment

    from roboz.deployment import DeployableAgent, Capability
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

    child = DeployableAgent(
        name="specialist",
        system_prompt="Complete the delegated task.",
        agent_endpoint=MockLLMEndpoint(
            [{"action": "stop", "rationale": "done", "value": "specialist result"}]
        ),
        capabilities=(Capability(tools=(stop,)),),
        interaction_mode=Output.API,
    )
    sandbox = Sandbox(tmp_path)
    definition = orchestrator_definition(
        sandbox,
        agent_endpoint=MockLLMEndpoint(
            [
                {
                    "action": "prompt_user",
                    "rationale": "first task complete",
                    "value": "First task done. What next?",
                },
                {"action": "specialist", "rationale": "handle next task"},
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
        subagents=(child,),
        interaction_mode=Output.API,
    )
    project_slug = "collaboration"
    events = []
    sandbox.configure_scope(project_slug)
    agent, background_agents = Deployment(
        agent=definition, sandbox=sandbox, event_sinks=[events.append],
    ).build()
    replies = Replies()
    token = bind_api_user_io(replies)
    try:
        result, messages = agent.invoke()
    finally:
        reset_api_user_io(token)
    assert result.value == "session ended"
    assert len(replies.prompts) == 2
    assert any("specialist result" in m.content for m in messages)
    logs = sandbox.project_logs_dir(project_slug)
    assert list((logs / "orchestrator").rglob("*.json"))
    assert list((logs / "specialist").rglob("*.json"))
    assert any(getattr(event, "agent_name", None) == "specialist" for event in events)
    assert background_agents == ()


def test_repeated_deployment_construction_without_a_web_host(tmp_path: Path) -> None:
    from roboz import DependencyRoute, ExternalDependencyKind, LazyExternalDependency
    from roboshed.deployments import Deployment
    from roboz.llm import LLMEndpoint

    sandbox = Sandbox(tmp_path)
    observed = []
    borrowed = LazyExternalDependency(
        "model:test:borrowed", ExternalDependencyKind.MODEL_ENDPOINT, {},
        lambda: LLMEndpoint(client=object(), api_name="test", model_name="borrowed"),
    )
    route = DependencyRoute(lambda: borrowed)
    sandbox.configure_scope("standalone")

    def configure():
        return Deployment(
            agent=orchestrator_definition(
                sandbox,
                agent_endpoint=MockLLMEndpoint([
                    {"action": "stop", "rationale": "user requested stop", "value": "complete"},
                ]),
                subagents=(
                    DeployableAgent(
                        name="unused",
                        agent_endpoint=route,
                        system_prompt="Unused specialist.",
                    ),
                ),
            ),
            sandbox=sandbox,
            event_sinks=[observed.append],
        )

    first = configure().build()[0]
    second = configure().build()[0]
    assert not sandbox.project_dir("standalone").exists()
    assert first is not second
    assert first.pipe is not second.pipe
    assert first.agent_endpoint is not second.agent_endpoint
    assert first.external_dependencies() == second.external_dependencies()
    assert borrowed in first.external_dependencies()
    assert "materialized" not in borrowed.__dict__
    assert first.invoke()[0].value == second.invoke()[0].value == "complete"
    assert observed
    assert list(sandbox.project_logs_dir("standalone").rglob("*.json"))


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
