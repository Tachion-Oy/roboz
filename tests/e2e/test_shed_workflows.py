"""Exercise guarded file tools through the real assistant and event pipeline."""

import json
from pathlib import Path
import tempfile

from roboz.llm import MockLLMEndpoint
from roboz.runtime import Output, PersistenceSink
from roboz_shed.assistant import WorkspacePermissions, build_assistant


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
        workspace=WorkspacePermissions.local(workspace),
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


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        test_guarded_read_edit_read_and_denied_escape(Path(directory))
    print("PASS guarded read/edit/read and denied escape")
