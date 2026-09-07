"""Exercise guarded file tools through the real assistant and event pipeline."""

import json
import tempfile
from pathlib import Path

from roboshed.agents import librarian as librarian_definition
from roboshed.assistant import build_assistant
from roboshed.capabilities import (
    ArtifactRetention,
    ConversationSnapshots,
    MaintenanceCadence,
    MemoryConsolidation,
)
from roboshed.workspace import Project, Workspace, WorkspacePermissions

from roboz import Agent, stop
from roboz.llm import MockLLMEndpoint
from roboz.runtime import EventPipe, Output, PersistenceSink


def test_guarded_read_edit_read_and_denied_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    note = workspace / "note.txt"
    note.write_text("before-marker")
    outside = tmp_path / "private.txt"
    outside.write_text("private-marker")
    (workspace / "escape.txt").symlink_to(outside)

    def read(path: str) -> dict:
        return {
            "action": "run_file_command",
            "rationale": "read",
            "chain": "and",
            "file_commands": [{"command": "cat", "argv": [path]}],
        }

    agent = build_assistant(
        project=Project(Workspace(tmp_path), "test"),
        permissions=WorkspacePermissions.local(workspace),
        interaction_mode=Output.API,
        event_sinks=[PersistenceSink.for_path(tmp_path / "logs")],
        endpoint=MockLLMEndpoint(
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
    )
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
    paths = Project(Workspace(tmp_path), "test")
    author = Agent(
        name="author",
        interaction_mode=None,
        tools=[stop],
        system_prompt="Remember the project decision.",
        initial_messages=["The project uses a blue robot emblem."],
        event_pipe=EventPipe(
            event_sinks=[PersistenceSink.for_path(paths.logs / "author")]
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
    source = next((paths.logs / "author").rglob("*.json"))
    responses = [
        {"value": "The project uses a blue robot emblem."},
        {"value": "Retain the blue robot emblem decision."},
    ]
    librarian = librarian_definition(
        agent_endpoint=None if separate_endpoints else MockLLMEndpoint(responses),
        capabilities=(
            ConversationSnapshots(
                paths,
                {"author"},
                endpoint=MockLLMEndpoint(responses[:1]) if separate_endpoints else None,
                token_growth_threshold=1,
            ),
            MemoryConsolidation(
                paths,
                {"author"},
                endpoint=MockLLMEndpoint(responses[1:]) if separate_endpoints else None,
                min_pending_snapshots=1,
            ),
            ArtifactRetention(
                paths, max_snapshot_files=0, max_log_files=0, max_memory_files=1
            ),
            MaintenanceCadence(paths, {"author"}, seconds=0),
        ),
    ).build(
        event_sink_factory=lambda name: (PersistenceSink.for_path(paths.logs / name),)
    )
    result, _ = librarian.invoke()
    assert "project idle" in result.value
    memory = list(paths.memory.glob("*.md"))
    assert len(memory) == 1
    assert "blue robot emblem" in memory[0].read_text()
    # Retention happens after consolidation: the memory survives its sources.
    assert not source.exists()
    assert not list(paths.snapshots.rglob("*.md"))
    assert not any(path.is_dir() for path in paths.snapshots.iterdir())
    assert list((paths.logs / "librarian").rglob("*.json"))


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
    project = Project(Workspace(tmp_path), "collaboration")
    events = []
    bundle = AgenticFactory(project=project, orchestrator=definition).build(
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
    assert list((project.logs / "orchestrator").rglob("*.json"))
    assert list((project.logs / "specialist").rglob("*.json"))
    assert any(getattr(event, "agent_name", None) == "specialist" for event in events)
    assert bundle.background_agents == ()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        test_guarded_read_edit_read_and_denied_escape(Path(directory))
    print("PASS guarded read/edit/read and denied escape")

    for scenario in (
        test_conversation_snapshot_memory_retention,
        test_conversation_snapshot_memory_with_separate_models,
        test_persistent_orchestrator_delegates_and_accepts_another_request,
    ):
        with tempfile.TemporaryDirectory() as directory:
            scenario(Path(directory))
        print(f"PASS {scenario.__name__}")
