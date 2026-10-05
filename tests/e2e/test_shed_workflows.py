"""Exercise guarded file tools through the real agent and event pipeline."""

import json
from types import SimpleNamespace
import tempfile
from pathlib import Path

from roboz.shed.agents import orchestrator as orchestrator_definition
from roboz.shed.capabilities import (
    ArtifactRetention,
    ConversationSnapshots,
    Filesystem,
    MaintenanceCadence,
    MemoryConsolidation,
)
from roboz.shed.sandbox import Sandbox

from roboz import Agent
from roboz.agent import AgentMode
from roboz.tools import stop
from roboz.deployment import (
    Capability,
    DeployableAgent,
    ToolLabel,
)
from roboz.llm import MockLLMEndpoint
from roboz.runtime import EventPipe, PersistenceSink


def _build_with_persistence(
    definition: DeployableAgent,
    sandbox: Sandbox,
    *,
    event_sinks=(),
):
    return definition.build(
        event_sinks=event_sinks,
        event_sink_factory=lambda name: (
            PersistenceSink.for_path(sandbox.project_logs_dir() / name),
        ),
    )


def test_guarded_read_edit_read_and_denied_escape(tmp_path: Path) -> None:
    sandbox = Sandbox(tmp_path / "sandbox", scope="project")
    sandbox.project_dir().mkdir(parents=True)
    note = sandbox.project_dir() / "note.txt"
    note.write_text("before-marker")
    outside = tmp_path / "private.txt"
    outside.write_text("private-marker")
    (sandbox.resolved_root / "escape.txt").symlink_to(outside)

    def read(path: str) -> dict:
        return {
            "action": "run_file_command",
            "rationale": "read",
            "value": [["cat", "CMD"], [path, "PTH"]],
        }

    definition = DeployableAgent(
        name="file_worker",
        system_prompt="Complete the file task and stop.",
        capabilities=(
            Capability(label=ToolLabel("stop"), value=stop),
            Filesystem(),
        ),
    )
    definition.set_attributes(sandbox=sandbox)
    definition.set_agent_endpoint(
        MockLLMEndpoint(
            [
                read("projects/project/note.txt"),
                {
                    "action": "apply_patch",
                    "rationale": "edit",
                    "path": "projects/project/note.txt",
                    "old_string": "before-marker",
                    "new_string": "after-marker",
                },
                read("projects/project/note.txt"),
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
        )
    )
    agent, _ = definition.build(
        event_sinks=(PersistenceSink.for_path(tmp_path / "logs"),)
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
    assert len(reads) == 4
    assert all(value.startswith("Overall: failure") for value in reads[2:])
    assert "before-marker" in reads[0] and "after-marker" in reads[1]
    assert all("private-marker" not in value for value in reads)
    denials = [
        output
        for output in outputs
        if output.get("caller") in {"operation_guard", "guard_file_command"}
        and output.get("status") == "denied"
    ]
    assert len(denials) == 2
    assert "Symlinks" in reads[3] or "symlink" in reads[3]
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
    sandbox.configure_scope(project_slug)
    logs = sandbox.project_logs_dir()
    snapshots = sandbox.project_snapshots_dir()
    memory_root = sandbox.project_memory_dir()
    author = Agent(
        name="author",
        tools=[stop],
        system_prompt="Remember the project decision.",
        initial_messages=["The project uses a blue robot emblem."],
        event_pipe=EventPipe(event_sinks=[PersistenceSink.for_path(logs / "author")]),
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
    librarian = DeployableAgent(
        name="librarian",
        mode=AgentMode.DETERMINISTIC,
        automatic_tool_prompt=False,
        capabilities=(
            ConversationSnapshots(
                endpoint=MockLLMEndpoint(responses[:1]) if separate_endpoints else None,
                token_growth_threshold=1,
            ),
            MemoryConsolidation(
                endpoint=MockLLMEndpoint(responses[1:]) if separate_endpoints else None,
                min_pending_snapshots=1,
            ),
            ArtifactRetention(
                max_snapshot_files=0,
                max_log_files=0,
                max_memory_files=1,
            ),
            MaintenanceCadence(seconds=0),
        ),
    )
    librarian.set_agent_endpoint(
        None if separate_endpoints else MockLLMEndpoint(responses)
    )
    librarian.set_attributes(sandbox=sandbox, watched_agent_names={"author"})
    librarian, _ = librarian.build(
        event_sink_factory=lambda name: (PersistenceSink.for_path(logs / name),)
    )
    result, _ = librarian.invoke()
    assert "watched agents inactive" in result.value
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
    from roboz.deployment import DeployableAgent, Capability
    from roboz.runtime import (
        bind_api_user_io,
        reset_api_user_io,
    )

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
        capabilities=(Capability(label=ToolLabel("stop"), value=stop),),
    )
    child.set_agent_endpoint(
        MockLLMEndpoint(
            [{"action": "stop", "rationale": "done", "value": "specialist result"}]
        )
    )
    sandbox = Sandbox(tmp_path)
    project_slug = "collaboration"
    sandbox.configure_scope(project_slug)
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
        nested_agents=(child,),
    )
    events = []
    agent, background_agents = _build_with_persistence(
        definition, sandbox, event_sinks=(events.append,)
    )
    replies = Replies()
    token = bind_api_user_io(replies)
    try:
        result, messages = agent.invoke()
    finally:
        reset_api_user_io(token)
    assert result.value == "session ended"
    assert len(replies.prompts) == 2
    assert any("specialist result" in m.content for m in messages)
    logs = sandbox.project_logs_dir()
    assert list((logs / "orchestrator").rglob("*.json"))
    assert list((logs / "specialist").rglob("*.json"))
    assert any(getattr(event, "agent_name", None) == "specialist" for event in events)
    assert background_agents == ()


def test_repeated_deployment_construction_without_a_web_host(tmp_path: Path) -> None:
    from roboz.llm import LLMEndpointRoute
    from roboz.llm import LLMEndpoint

    sandbox = Sandbox(tmp_path)
    observed = []

    def forbidden():
        raise AssertionError("Unused specialist initialized its client")

    borrowed = LLMEndpoint(
        client=SimpleNamespace(
            chat=object(), models=object(), close=forbidden, materialize=forbidden
        ),
        api_name="test",
        model_name="borrowed",
    )
    route = LLMEndpointRoute(lambda: borrowed)
    sandbox.configure_scope("standalone")

    def configure():
        specialist = DeployableAgent(
            name="unused",
            system_prompt="Unused specialist.",
        )
        specialist.set_agent_endpoint(route)
        definition = orchestrator_definition(
            sandbox,
            agent_endpoint=MockLLMEndpoint(
                [
                    {
                        "action": "stop",
                        "rationale": "user requested stop",
                        "value": "complete",
                    },
                ]
            ),
            nested_agents=(specialist,),
        )
        return _build_with_persistence(
            definition, sandbox, event_sinks=(observed.append,)
        )

    first = configure()[0]
    second = configure()[0]
    assert not sandbox.project_dir().exists()
    assert first is not second
    assert first.pipe is not second.pipe
    assert first.agent_endpoint is not second.agent_endpoint
    assert first.external_dependencies() == second.external_dependencies()
    assert borrowed in first.external_dependencies()
    assert "materialized" not in borrowed.__dict__
    assert first.invoke()[0].value == second.invoke()[0].value == "complete"
    assert observed
    assert list(sandbox.project_logs_dir().rglob("*.json"))


def test_filesystem_capability_writes_and_removes_with_its_skill(tmp_path):
    sandbox = Sandbox(tmp_path / "sandbox", scope="project")
    sandbox.project_dir().mkdir(parents=True)
    folder = "projects/project/notes"
    path = f"{folder}/note.txt"
    definition = DeployableAgent(
        name="cli_worker",
        system_prompt="Write, verify, and remove the note.",
        capabilities=(Capability(label=ToolLabel("stop"), value=stop), Filesystem()),
    )
    definition.set_attributes(sandbox=sandbox)
    definition.set_agent_endpoint(
        MockLLMEndpoint(
            [
                {
                    "action": "run_file_command",
                    "rationale": "Exercise file commands",
                    "value": [
                        ["mkdir", "CMD"],
                        [folder, "PTH"],
                        ["&&", "CTL"],
                        ["tee", "CMD"],
                        ["created by the CLI\n", "ARG"],
                        [path, "PTH"],
                        ["&&", "CTL"],
                        ["cat", "CMD"],
                        [path, "PTH"],
                        ["&&", "CTL"],
                        ["rm", "CMD"],
                        [path, "PTH"],
                    ],
                },
                {"action": "stop", "rationale": "Done", "value": "done"},
            ]
        )
    )
    agent, _ = definition.build()
    assert [skill.name for skill in agent.auto_loaded_skills] == ["filesystem"]
    assert "run_file_command" not in {tool.name for tool in agent.active_tools.values()}
    _, messages = agent.invoke()
    active_names = {tool.name for tool in agent.active_tools.values()}
    assert {"run_file_command", "apply_patch"} <= active_names
    assert (
        not {"operation_guard", "execute_file_command", "continue_file_command"}
        & active_names
    )
    results = [json.loads(m.content) for m in messages if m.role.value == "user"]
    final = [
        r["value"]
        for r in results
        if r.get("caller") == "execute_file_command" and "value" in r
    ][-1]
    assert final.startswith("Overall: success") and "created by the CLI" in final
    assert (sandbox.resolved_root / folder).is_dir()
    assert not (sandbox.resolved_root / path).exists()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        test_guarded_read_edit_read_and_denied_escape(Path(directory))
    print("PASS guarded read/edit/read and denied escape")

    for scenario in (
        test_filesystem_capability_writes_and_removes_with_its_skill,
        test_repeated_deployment_construction_without_a_web_host,
        test_conversation_snapshot_memory_retention,
        test_conversation_snapshot_memory_with_separate_models,
        test_persistent_orchestrator_delegates_and_accepts_another_request,
    ):
        with tempfile.TemporaryDirectory() as directory:
            scenario(Path(directory))
        print(f"PASS {scenario.__name__}")
