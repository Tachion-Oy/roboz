"""Canonical imports, tool wiring, and the public file-command schema."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from roboz.shed.models import ActionVerdict
from roboz.shed.tools import get_run_file_command
from roboz.shed.tools.cli_commands import FileCommand
from roboz.shed.tools.cli_commands import get_run_file_command as cli_factory
from roboz.shed.tools.cli_commands.specs import COMMANDS


def test_canonical_factory_exposes_one_entry_and_declares_its_executables(tmp_path):
    assert cli_factory is get_run_file_command
    entry, guard, execute, continuation = get_run_file_command(
        base=tmp_path, default_verdict=ActionVerdict.allow, cli_skill_name="custom_cli"
    )
    assert [tool.name for tool in (entry, guard, execute, continuation)] == [
        "run_file_command",
        "guard_file_command",
        "execute_file_command",
        "continue_file_command",
    ]
    assert entry.chained_to is None or entry.chained_to == []
    assert guard.chained_to == [entry, continuation]
    assert execute.chained_to == [guard] and continuation.chained_to == [execute]
    assert "`custom_cli`" in entry.description
    assert {dep.dependency_id for dep in execute.external_dependencies()} == {
        f"executable:{command}" for command in COMMANDS
    }
    schema = FileCommand.model_json_schema()
    assert schema["required"] == ["value"]
    assert (
        not {"chain", "file_commands", "stdin", "remaining", "ready"}
        & schema["properties"].keys()
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"chain": "&&", "file_commands": [{"command": "pwd", "argv": []}]},
        {"value": [["pwd", "CMD"]], "chain": ";"},
        {"value": [["tee", "CMD"]], "stdin": "content"},
        {"value": [["help", "CMD"]]},
    ],
)
def test_removed_call_shapes_are_rejected(payload):
    with pytest.raises(ValidationError):
        FileCommand.model_validate(payload)


def test_factory_requires_an_absolute_base():
    with pytest.raises(ValueError, match="Base must be absolute"):
        get_run_file_command(base=Path("relative"), default_verdict=ActionVerdict.deny)


def test_file_commands_use_configured_base_not_process_cwd(tmp_path, monkeypatch):
    base = tmp_path / "base"
    base.mkdir()
    (base / "note").write_text("configured base")
    (tmp_path / "note").write_text("ambient directory")
    monkeypatch.chdir(tmp_path)
    entry, guard, execute, _ = get_run_file_command(
        base=base, default_verdict=ActionVerdict.allow
    )
    result = execute(
        guard(entry(FileCommand(value=[("cat", "CMD"), ("note", "PTH")]), []), []), []
    )
    assert "configured base" in result.value and "ambient directory" not in result.value
