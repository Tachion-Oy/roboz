import json
from pathlib import Path

from roboz_shed.assistant import WorkspacePermissions, build_assistant
from roboz_shed.demo import main

from roboz import Ctx, Empty, Message, Str, tool
from roboz.llm import MockLLMEndpoint
from roboz.runtime import PersistenceSink, RunLifecycleEvent


def test_installed_demo_writes_file_and_completed_conversation(tmp_path: Path, capsys):
    workspace = tmp_path / "workspace"
    data = tmp_path / "data"
    main(["--mock", "--workspace", str(workspace), "--data-path", str(data)])
    files = list(workspace.glob("roboz-demo-*.txt"))
    assert len(files) == 1
    assert files[0].read_text() == "Hello from Roboz!\n"
    logs = [json.loads(path.read_text()) for path in data.rglob("*.json")]
    assert logs
    assert any('"completed"' in json.dumps(log) for log in logs)
    assert "Demo complete." in capsys.readouterr().out


def test_assistant_uses_injected_tools_and_pipe(tmp_path: Path):
    seen = []
    pipes = []

    @tool
    def custom(input: Empty, messages: list[Message]) -> Str:
        seen.append(True)
        return Str(value="custom result")

    def tools(pipe):
        pipes.append(pipe)
        return [custom]

    events = []
    agent = build_assistant(
        endpoint=MockLLMEndpoint(
            [
                {"action": "custom", "rationale": "test extension"},
                {"action": "stop", "rationale": "done", "value": "ok"},
            ]
        ),
        workspace=WorkspacePermissions.local(tmp_path),
        tool_builders=[tools],
        event_sinks=[events.append, PersistenceSink.for_path(tmp_path / "logs")],
    )
    result, _ = agent.invoke()
    assert result.value == "ok"
    assert seen == [True]
    assert pipes == [agent.pipe]
    assert any(
        isinstance(event, RunLifecycleEvent) and event.kind == "stopped"
        for event in events
    )


def test_workspace_denies_escape_and_symlink_target(tmp_path: Path):
    from roboz_shed.models import ActionVerdict, Operation
    from roboz_shed.tools.guard import resolve_allow_verdict

    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    (root / "link.txt").symlink_to(outside)
    workspace = WorkspacePermissions.local(root)
    ctx = Ctx(
        base=workspace.base,
        takes_precedence=workspace.takes_precedence,
        allow=list(workspace.allow),
        deny=[],
        ask=[],
        default_verdict=workspace.default_verdict,
    )
    for path in [outside, root / ".." / "outside.txt", root / "link.txt"]:
        assert resolve_allow_verdict(path, Operation.READ, ctx)[0] == ActionVerdict.deny
    assert (
        resolve_allow_verdict(root / "new.txt", Operation.CREATE, ctx)[0]
        == ActionVerdict.allow
    )
