"""Tests for the apply_patch connector and Python string-replace executor."""

from pathlib import Path
from unittest.mock import patch

import pytest
from roboz.models import Str
from roboz.runtime import EventPipe
from roboz.models.truncation import Severity, Truncation
from roboz.shed.models import (
    ActionVerdict,
    ApplyPatch,
    ApplyPatchReady,
    GuardFilesResult,
    GuardStatus,
    Operation,
    PermissionRule,
)
from roboz.shed.tools import get_apply_patch
from roboz.shed.tools.types import ResolvedFileCommand


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
    assert [item.operation for item in out.items] == [
        Operation.CREATE,
        Operation.READ,
        Operation.DELETE,
    ]
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


@pytest.mark.parametrize("operation", [Operation.READ, Operation.DELETE])
def test_patch_checks_overwrite_policy_before_asking_to_create(
    tmp_path: Path, operation: Operation
) -> None:
    target = tmp_path / "file.txt"
    target.write_text("before")
    entry, guard, execute = get_apply_patch(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        deny_rules=[PermissionRule("file.txt", {operation})],
        ask_rules=[PermissionRule("file.txt", {Operation.CREATE})],
        pipe=EventPipe(),
    )
    with patch("roboz.runtime.interact_with_user") as prompt:
        result = guard(
            entry(ApplyPatch(path="file.txt", old_string="", new_string="after"), []),
            [],
        )
    assert result.status == GuardStatus.DENIED
    assert not execute.chain_condition(result)
    prompt.assert_not_called()
    assert target.read_text() == "before"


def test_patch_overwrite_requires_delete_approval(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("before")
    entry, guard, execute = get_apply_patch(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        ask_rules=[PermissionRule("file.txt", {Operation.DELETE})],
        pipe=EventPipe(),
    )
    with patch("roboz.runtime.interact_with_user", return_value="no") as prompt:
        result = guard(
            entry(
                ApplyPatch(path="file.txt", old_string="before", new_string="after"), []
            ),
            [],
        )
    assert result.status == GuardStatus.DENIED
    assert not execute.chain_condition(result)
    prompt.assert_called_once()
    assert target.read_text() == "before"


@pytest.mark.parametrize("name", [" spaced ", r"back\slash"])
def test_patch_permission_patterns_preserve_literal_names(
    tmp_path: Path, name: str
) -> None:
    entry, guard, execute = get_apply_patch(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(name, {Operation.CREATE})],
    )
    result = guard(
        entry(ApplyPatch(path=name, old_string="", new_string="content"), []), []
    )
    assert result.status == GuardStatus.ALLOWED
    execute(result, [])
    assert (tmp_path / name).read_text() == "content"


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
