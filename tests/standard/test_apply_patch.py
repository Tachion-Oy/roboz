"""Tests for the apply_patch connector and Python string-replace executor."""

from pathlib import Path

import pytest
from roboz.models import Severity, Str, Truncation

from roboz.standard.identifiers import APPLY_PATCH_TOOL_NAME
from roboz.standard.models import (
    ActionVerdict,
    ApplyPatch,
    ApplyPatchReady,
    GuardFilesResult,
    GuardStatus,
    Operation,
    PermissionRule,
)
from roboz.standard.skills.file_edit.tools import get_apply_patch
from roboz.standard.skills.cli_commands.runtime.types import ResolvedFileCommand


def test_get_apply_patch_rejects_relative_base() -> None:
    with pytest.raises(ValueError, match="Base must be absolute"):
        get_apply_patch(
            base=Path("relative/workspace"),
            default_verdict=ActionVerdict.deny,
        )


def test_get_apply_patch_requires_base() -> None:
    with pytest.raises(TypeError):
        get_apply_patch()  # type: ignore[call-arg]


def test_apply_patch_connector_builds_guard_input_for_modify(tmp_path: Path) -> None:
    (tmp_path / "demo.txt").write_text("old\n")
    tools = get_apply_patch(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        allow_rules=[
            PermissionRule(
                pattern="**",
                operations={Operation.READ, Operation.CREATE, Operation.DELETE},
            )
        ],
        deny_rules=[],
        takes_precedence=ActionVerdict.allow,
    )
    entry = tools[0]
    assert [tool.name for tool in tools] == [
        APPLY_PATCH_TOOL_NAME,
        "guard_operation",
        "execute_apply_patch_replace",
    ]
    assert entry.InputModel is ApplyPatch
    assert tools[1].chained_to == [entry]
    assert tools[2].chained_to == [tools[1]]
    out = entry(
        input=ApplyPatch(
            path="demo.txt",
            old_string="old\n",
            new_string="new\n",
            replace_all=False,
        ),
        messages=[],
    )
    assert isinstance(out, ResolvedFileCommand)
    assert len(out.items) == 1
    assert out.items[0].operation == Operation.CREATE
    assert isinstance(out.items[0].value, ApplyPatchReady)
    assert out.items[0].value.path == "demo.txt"
    assert out.items[0].value.old_string == "old\n"
    assert out.items[0].value.new_string == "new\n"
    assert out.items[0].value.replace_all is False


def test_apply_patch_accepts_paths_with_spaces(tmp_path: Path) -> None:
    (tmp_path / "demo file.txt").write_text("hello world\n")
    tools = get_apply_patch(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        allow_rules=[
            PermissionRule(
                pattern="**",
                operations={Operation.READ, Operation.CREATE, Operation.DELETE},
            )
        ],
        deny_rules=[],
        takes_precedence=ActionVerdict.allow,
    )
    entry = tools[0]
    out = entry(
        input=ApplyPatch(
            path="demo file.txt",
            old_string="world",
            new_string="there",
            replace_all=False,
        ),
        messages=[],
    )
    assert isinstance(out, ResolvedFileCommand)


def test_apply_patch_accepts_absolute_path_input(tmp_path: Path) -> None:
    file_path = tmp_path / "demo.txt"
    file_path.write_text("old\n")
    tools = get_apply_patch(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        allow_rules=[
            PermissionRule(
                pattern=str(file_path.resolve()),
                operations={Operation.READ, Operation.CREATE, Operation.DELETE},
            )
        ],
        deny_rules=[],
        takes_precedence=ActionVerdict.allow,
        execute_cli_truncation=Truncation(threshold=0, severity=Severity.LIGHT),
    )
    entry, guard, execute = tools
    ready = entry(
        input=ApplyPatch(
            path=str(file_path.resolve()),
            old_string="old\n",
            new_string="new\n",
            replace_all=False,
        ),
        messages=[],
    )
    assert isinstance(ready, ResolvedFileCommand)
    guarded = guard(input=ready, messages=[])
    result = execute(input=guarded, messages=[])
    assert isinstance(result, Str)
    assert file_path.read_text() == "new\n"


def test_apply_patch_guard_denies_disallowed_write(tmp_path: Path) -> None:
    (tmp_path / "demo.txt").write_text("old\n")
    tools = get_apply_patch(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
        deny_rules=[],
        takes_precedence=ActionVerdict.deny,
        execute_cli_truncation=Truncation(threshold=0, severity=Severity.LIGHT),
    )
    entry, guard, _ = tools
    ready = entry(
        input=ApplyPatch(
            path="demo.txt",
            old_string="old\n",
            new_string="new\n",
            replace_all=False,
        ),
        messages=[],
    )
    assert isinstance(ready, ResolvedFileCommand)
    denied = guard(input=ready, messages=[])
    assert isinstance(denied, GuardFilesResult)
    assert denied.status == GuardStatus.DENIED


def test_apply_patch_executes_string_replace(tmp_path: Path) -> None:
    (tmp_path / "demo.txt").write_text("old\n")
    tools = get_apply_patch(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        allow_rules=[
            PermissionRule(
                pattern="**",
                operations={Operation.READ, Operation.CREATE, Operation.DELETE},
            )
        ],
        deny_rules=[],
        takes_precedence=ActionVerdict.allow,
        execute_cli_truncation=Truncation(threshold=0, severity=Severity.LIGHT),
    )
    entry, guard, execute = tools
    ready = entry(
        input=ApplyPatch(
            path="demo.txt",
            old_string="old\n",
            new_string="new\n",
            replace_all=False,
        ),
        messages=[],
    )
    guarded = guard(input=ready, messages=[])
    result = execute(input=guarded, messages=[])
    assert isinstance(result, Str)
    assert (tmp_path / "demo.txt").read_text() == "new\n"
    assert "string-replace" in result.value
    assert "Replaced 1 occurrence" in result.value


def test_apply_patch_rewrite_mode_overwrites_existing_file(tmp_path: Path) -> None:
    (tmp_path / "demo.txt").write_text("old\n")
    tools = get_apply_patch(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        allow_rules=[
            PermissionRule(
                pattern="**",
                operations={Operation.READ, Operation.CREATE, Operation.DELETE},
            )
        ],
        deny_rules=[],
        takes_precedence=ActionVerdict.allow,
        execute_cli_truncation=Truncation(threshold=0, severity=Severity.LIGHT),
    )
    entry, guard, execute = tools
    ready = entry(
        input=ApplyPatch(
            path="demo.txt",
            old_string="",
            new_string="brand new contents\n",
            replace_all=False,
        ),
        messages=[],
    )
    guarded = guard(input=ready, messages=[])
    result = execute(input=guarded, messages=[])
    assert isinstance(result, Str)
    assert (tmp_path / "demo.txt").read_text() == "brand new contents\n"
    assert "full-file rewrite" in result.value


def test_apply_patch_rewrite_mode_creates_missing_file(tmp_path: Path) -> None:
    tools = get_apply_patch(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        allow_rules=[
            PermissionRule(
                pattern="**",
                operations={Operation.READ, Operation.CREATE, Operation.DELETE},
            )
        ],
        deny_rules=[],
        takes_precedence=ActionVerdict.allow,
        execute_cli_truncation=Truncation(threshold=0, severity=Severity.LIGHT),
    )
    entry, guard, execute = tools
    ready = entry(
        input=ApplyPatch(
            path="created.txt",
            old_string="",
            new_string="created via rewrite mode\n",
            replace_all=False,
        ),
        messages=[],
    )
    guarded = guard(input=ready, messages=[])
    result = execute(input=guarded, messages=[])
    assert isinstance(result, Str)
    assert (tmp_path / "created.txt").read_text() == "created via rewrite mode\n"
    print(result)
    assert "full-file" in result.value


def test_apply_patch_replace_all(tmp_path: Path) -> None:
    (tmp_path / "demo.txt").write_text("foo bar foo\n")
    tools = get_apply_patch(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        allow_rules=[
            PermissionRule(
                pattern="**",
                operations={Operation.READ, Operation.CREATE, Operation.DELETE},
            )
        ],
        deny_rules=[],
        takes_precedence=ActionVerdict.allow,
        execute_cli_truncation=Truncation(threshold=0, severity=Severity.LIGHT),
    )
    entry, guard, execute = tools
    ready = entry(
        input=ApplyPatch(
            path="demo.txt",
            old_string="foo",
            new_string="baz",
            replace_all=True,
        ),
        messages=[],
    )
    guarded = guard(input=ready, messages=[])
    result = execute(input=guarded, messages=[])
    assert isinstance(result, Str)
    assert (tmp_path / "demo.txt").read_text() == "baz bar baz\n"
    assert "2 occurrence" in result.value


def test_apply_patch_single_mode_rejects_multiple_matches(tmp_path: Path) -> None:
    (tmp_path / "demo.txt").write_text("foo foo\n")
    tools = get_apply_patch(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        allow_rules=[
            PermissionRule(
                pattern="**",
                operations={Operation.READ, Operation.CREATE, Operation.DELETE},
            )
        ],
        deny_rules=[],
        takes_precedence=ActionVerdict.allow,
        execute_cli_truncation=Truncation(threshold=0, severity=Severity.LIGHT),
    )
    entry, guard, execute = tools
    ready = entry(
        input=ApplyPatch(
            path="demo.txt",
            old_string="foo",
            new_string="bar",
            replace_all=False,
        ),
        messages=[],
    )
    guarded = guard(input=ready, messages=[])
    result = execute(input=guarded, messages=[])
    assert isinstance(result, Str)
    assert "2 times" in result.value or "matched 2" in result.value
    assert (tmp_path / "demo.txt").read_text() == "foo foo\n"


def test_apply_patch_replace_all_requires_at_least_one_match(tmp_path: Path) -> None:
    (tmp_path / "demo.txt").write_text("hello\n")
    tools = get_apply_patch(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        allow_rules=[
            PermissionRule(
                pattern="**",
                operations={Operation.READ, Operation.CREATE, Operation.DELETE},
            )
        ],
        deny_rules=[],
        takes_precedence=ActionVerdict.allow,
        execute_cli_truncation=Truncation(threshold=0, severity=Severity.LIGHT),
    )
    entry, guard, execute = tools
    ready = entry(
        input=ApplyPatch(
            path="demo.txt",
            old_string="nope",
            new_string="x",
            replace_all=True,
        ),
        messages=[],
    )
    guarded = guard(input=ready, messages=[])
    result = execute(input=guarded, messages=[])
    assert isinstance(result, Str)
    assert "not found" in result.value.lower()
