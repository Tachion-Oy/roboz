"""Tests for run_file_command tool."""

import re
from pathlib import Path

import pytest
from roboz.models import Str

from roboshed.models import (
    ActionVerdict,
    CommandReady,
    Help,
    Operation,
    ParseError,
    PermissionRule,
    RunFileCommand,
    RunFileCommands,
)
from roboshed.tools import get_run_file_command
from roboshed.tools.cli_commands.run_file_command.command import _validate_input
from roboshed.tools.cli_commands.run_file_command.resolve import (
    _guard_items,
    _resolve_paths_and_argv,
)
from roboshed.tools.cli_commands.run_file_command.specs import (
    CP,
    FILE_COMMANDS_DELETE,
    FILE_COMMANDS_READ,
    FILE_COMMANDS_WRITE,
    MV,
)
from roboshed.tools.types import ResolvedFileCommand
from roboshed.tools.cli_commands.utilities.cmd_spec import ArgPattern, CmdSpec
from roboshed.tools.cli_commands.utilities.formatting import (
    format_cli_commands_help,
    format_cli_constraints,
    format_cli_full_help,
)

FILE_COMMANDS = FILE_COMMANDS_READ + FILE_COMMANDS_WRITE + FILE_COMMANDS_DELETE
_SPECS_BY_NAME = {spec.name: spec for spec in FILE_COMMANDS}


def _resolve_paths(input: RunFileCommands, base: Path) -> list[Path]:
    cli_command = input.file_commands[0]
    spec = _SPECS_BY_NAME[cli_command.command.strip().lower()]
    paths, _, _, _ = _resolve_paths_and_argv(input, base, spec)
    return paths


# -----------------------------------------------------------------------------
# CmdSpec operations
# -----------------------------------------------------------------------------


def test_file_command_read_specs_use_read_operation() -> None:
    for spec in FILE_COMMANDS_READ:
        assert spec.operation == Operation.READ
        assert spec.source_operation is None


def test_file_command_write_specs_use_create_operation() -> None:
    for spec in FILE_COMMANDS_WRITE:
        assert spec.operation == Operation.CREATE
        if spec.name == "mv":
            assert spec.source_operation == Operation.DELETE
        elif spec.name == "cp":
            assert spec.source_operation == Operation.READ
        else:
            assert spec.source_operation is None


def test_file_command_delete_specs_use_delete_operation() -> None:
    for spec in FILE_COMMANDS_DELETE:
        assert spec.operation == Operation.DELETE
        assert spec.source_operation is None


def test_cmdspec_source_operation_requires_create() -> None:
    with pytest.raises(ValueError, match="source_operation requires operation=CREATE"):
        CmdSpec(
            name="bad",
            operation=Operation.DELETE,
            source_operation=Operation.DELETE,
        )
    assert (
        CmdSpec(
            name="ok",
            operation=Operation.CREATE,
            source_operation=Operation.DELETE,
        ).source_operation
        == Operation.DELETE
    )


# -----------------------------------------------------------------------------
# format_cli_commands_help / format_cli_full_help
# -----------------------------------------------------------------------------


def test_format_cli_commands_help_lists_all_commands() -> None:
    """format_cli_commands_help lists all commands and their forbidden patterns."""
    out = format_cli_commands_help(FILE_COMMANDS)
    assert "Available CLI commands" in out
    assert "grep" in out
    assert "rg" in out
    assert "pwd" in out
    assert "cat" in out
    assert "cp" in out
    assert "find" in out
    assert "forbidden" in out
    assert "-exec" in out


def test_format_cli_commands_help_includes_chaining_examples() -> None:
    """format_cli_commands_help mentions pipe, and, or chaining examples."""
    out = format_cli_commands_help(FILE_COMMANDS)
    assert "pipe" in out
    assert "and" in out
    assert "chain" in out
    assert "file_commands" in out
    assert '"argv": [".", "-name", "*.py"]' in out
    assert '"command": "rg"' in out
    assert '"command": "cp"' in out
    assert "todo|fixme|bug" in out
    assert '"command": "wc"' in out
    assert '"command": "head"' in out and '"command": "tail"' in out


def test_format_cli_commands_help_custom_specs() -> None:
    """format_cli_commands_help uses provided specs with allow/forbidden patterns."""
    specs = (
        CmdSpec(
            name="foo",
            forbidden_patterns=[ArgPattern(re.compile(r"-x"))],
        ),
        CmdSpec(
            name="zap",
            allowed_patterns=[
                ArgPattern(re.compile(r"-n")),
                ArgPattern(re.compile(r"TODO")),
            ],
        ),
        CmdSpec(name="bar"),
    )
    out = format_cli_commands_help(specs)
    assert "bar" in out
    assert "foo" in out
    assert "zap" in out
    assert "forbidden" in out
    assert "-x" in out
    assert "allowed" in out
    assert "TODO" in out


def test_format_cli_full_help_includes_commands_and_constraints(
    tmp_path: Path,
) -> None:
    """format_cli_full_help is commands plus permissions; all arguments required."""
    out = format_cli_full_help(
        FILE_COMMANDS,
        tmp_path,
        [PermissionRule(pattern="**", operations={Operation.READ})],
        [],
        [],
        ActionVerdict.allow,
        ActionVerdict.allow,
    )
    assert "Available CLI commands" in out
    assert "CLI constraints" in out
    assert str(tmp_path.resolve()) in out
    assert "Scope" in out


# -----------------------------------------------------------------------------
# format_cli_constraints
# -----------------------------------------------------------------------------


def test_format_cli_constraints_mentions_scope_and_overwrite(tmp_path: Path) -> None:
    """format_cli_constraints mentions base_path, scope, and overwrite/DELETE rule."""
    out = format_cli_constraints(
        base_path=tmp_path,
        allow_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
        deny_rules=[],
        ask_rules=[],
        takes_precedence=ActionVerdict.allow,
        default_verdict=ActionVerdict.allow,
        command_specs=FILE_COMMANDS,
    )
    assert "Scope" in out
    assert "shorthand" in out.lower() or "relative" in out.lower()
    assert "absolute" in out.lower()
    assert "** matches zero or more segments" in out
    assert "dir/** also matches dir itself" in out
    assert "Authorization" in out
    assert "Overwrite requires DELETE" in out
    assert "CREATE" in out and "DELETE" in out


def test_format_cli_constraints_tool_behaviors_with_standard_specs(
    tmp_path: Path,
) -> None:
    """format_cli_constraints includes tool-specific behaviors for COMMAND_SPECS."""
    out = format_cli_constraints(
        base_path=tmp_path,
        allow_rules=[],
        deny_rules=[],
        ask_rules=[],
        takes_precedence=ActionVerdict.deny,
        default_verdict=ActionVerdict.deny,
        command_specs=FILE_COMMANDS,
    )
    assert "-a" in out
    assert "**find**" in out
    assert "**rg**" in out
    # find may surface either forbidden patterns or an explicit hint, depending on spec priority.
    assert (
        "search roots" in out.lower()
        or "predicates" in out.lower()
        or "-exec" in out
        or "-delete" in out
        or "forbidden" in out.lower()
    )
    names = {spec.name for spec in FILE_COMMANDS}
    if "rm" in names:
        assert "**rm**" in out
        assert "confirmation" in out
    if "git" in names:
        assert "**git**" in out
        assert "--hard" in out


def test_format_cli_constraints_reflects_code_task_generator_rules(
    tmp_path: Path,
) -> None:
    """format_cli_constraints reflects allow/deny rules for code_task_generator config."""
    allow_rules: list[PermissionRule] = [
        PermissionRule(pattern="**", operations={Operation.READ}),
        PermissionRule(pattern="plans", operations={Operation.CREATE}),
        PermissionRule(
            pattern="plans/*.md", operations={Operation.CREATE, Operation.DELETE}
        ),
    ]
    deny_rules: list[PermissionRule] = [
        PermissionRule(pattern="**", operations={Operation.CREATE})
    ]
    out = format_cli_constraints(
        base_path=tmp_path,
        allow_rules=allow_rules,
        deny_rules=deny_rules,
        ask_rules=[],
        takes_precedence=ActionVerdict.allow,
        default_verdict=ActionVerdict.deny,
        command_specs=FILE_COMMANDS,
    )
    assert "CREATE" in out
    assert "DELETE" in out
    assert "plans" in out
    assert "Precedence" in out or "precedence" in out


def test_format_cli_constraints_handles_unresolved_dynamic_rule(tmp_path: Path) -> None:
    out = format_cli_constraints(
        base_path=tmp_path,
        allow_rules=[
            PermissionRule(
                pattern=lambda: (_ for _ in ()).throw(RuntimeError("not ready")),
                operations={Operation.CREATE},
            )
        ],
        deny_rules=[],
        ask_rules=[],
        takes_precedence=ActionVerdict.allow,
        default_verdict=ActionVerdict.deny,
        command_specs=FILE_COMMANDS,
    )
    assert "<unresolved dynamic path>" in out
    assert "CREATE" in out


# -----------------------------------------------------------------------------
# help command (run_file_command with command='help')
# -----------------------------------------------------------------------------


def test_get_run_file_command_returns_stable_tool_chain(tmp_path: Path) -> None:
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        deny_rules=[],
        allow_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
        ask_rules=[],
        takes_precedence=ActionVerdict.allow,
    )

    assert [tool.name for tool in tools] == [
        "run_file_command",
        "operation_guard",
        "execute_file_command",
        "run_file_command_passive",
    ]
    assert {
        dependency.dependency_id
        for tool in tools
        for dependency in tool.external_dependencies
    } == {f"executable:{spec.name}" for spec in FILE_COMMANDS}


def test_get_run_file_command_rejects_relative_base() -> None:
    with pytest.raises(ValueError, match="Base must be absolute"):
        get_run_file_command(
            base=Path("relative/workspace"),
            default_verdict=ActionVerdict.deny,
        )


def test_get_run_file_command_requires_base() -> None:
    with pytest.raises(TypeError):
        get_run_file_command()  # type: ignore[call-arg]


def test_run_file_command_uses_configured_base_not_process_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "workspace"
    ambient = tmp_path / "ambient"
    base.mkdir()
    ambient.mkdir()
    (base / "target.txt").write_text("base-content")
    (ambient / "target.txt").write_text("ambient-content")
    monkeypatch.chdir(ambient)

    run_cli, guard, execute, _ = get_run_file_command(
        base=base,
        default_verdict=ActionVerdict.allow,
        deny_rules=[],
        allow_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
        ask_rules=[],
        takes_precedence=ActionVerdict.allow,
    )

    parsed = run_cli(
        input=RunFileCommands(
            chain="pipe",
            file_commands=[RunFileCommand(command="cat", argv=["target.txt"])],
        ),
        messages=[],
    )
    assert isinstance(parsed, ResolvedFileCommand)
    assert isinstance(parsed.items[0].value, CommandReady)
    assert parsed.items[0].value.base_workdir == base.resolve()
    assert parsed.items[0].value.argv == ["cat", str((base / "target.txt").resolve())]

    guarded = guard(input=parsed, messages=[])
    result = execute(input=guarded, messages=[])

    assert isinstance(result, Str)
    assert "base-content" in result.value
    assert "ambient-content" not in result.value


def test_help_command_returns_help_output(tmp_path: Path) -> None:
    """Invoking run_file_command with command='help' returns formatted help."""
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        deny_rules=[],
        allow_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
        ask_rules=[],
        takes_precedence=ActionVerdict.allow,
    )
    run_cli = tools[0]

    # Run the chain: run_file_command -> operation_guard -> execute_file_command
    input_cmd = RunFileCommands(
        chain="pipe",
        file_commands=[
            RunFileCommand(
                command="help",
                argv=[],
            )
        ],
    )
    result = run_cli(input=input_cmd, messages=[])
    assert isinstance(result, Help)
    assert result.kind == "help"
    assert "Available CLI commands" in result.message
    assert "CLI constraints" in result.message
    assert "grep" in result.message
    assert "rg" in result.message
    assert "cat" in result.message


def test_help_command_rejected_with_args(tmp_path: Path) -> None:
    """Help with args is rejected (treated as unknown)."""
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        deny_rules=[],
        allow_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
        ask_rules=[],
        takes_precedence=ActionVerdict.allow,
    )
    run_cli = tools[0]

    input_cmd = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="help", argv=["-x"])],
    )
    result = run_cli(input=input_cmd, messages=[])
    # Help with arguments is a parse error, not a help request.
    assert isinstance(result, ParseError)
    assert result.kind == "parse_error"
    assert "not allowed" in result.message or "help" in result.message.lower()


def test_cli_parse_error_includes_help_summary(tmp_path: Path) -> None:
    """Parse errors include full help (commands + permissions)."""
    tools = get_run_file_command(
        base=tmp_path,
        default_verdict=ActionVerdict.deny,
        deny_rules=[],
        allow_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
        ask_rules=[],
        takes_precedence=ActionVerdict.deny,
    )
    run_cli = tools[0]

    input_cmd = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="unknown_cmd", argv=[])],
    )
    result = run_cli(input=input_cmd, messages=[])
    assert isinstance(result, ParseError)
    assert "not allowed" in result.message
    assert "Available CLI commands" in result.message
    assert "CLI constraints" in result.message
    assert "grep" in result.message
    assert "rg" in result.message
    assert "cat" in result.message


# -----------------------------------------------------------------------------
# _validate_input
# -----------------------------------------------------------------------------


def test_validate_input_valid_grep() -> None:
    """Valid grep: flags, pattern, and path in ``argv`` order."""
    input = RunFileCommands(
        chain="pipe",
        file_commands=[
            RunFileCommand(
                command="grep",
                argv=["-n", "def", "src/foo.py"],
            )
        ],
    )
    assert _validate_input(input, FILE_COMMANDS) is None


def test_validate_input_valid_rg() -> None:
    """Valid rg: flags, pattern, and search path in ``argv``."""
    input = RunFileCommands(
        chain="pipe",
        file_commands=[
            RunFileCommand(
                command="rg",
                argv=["-n", "def main", "src/"],
            )
        ],
    )
    assert _validate_input(input, FILE_COMMANDS) is None


def test_validate_input_valid_cat() -> None:
    """Valid cat: path operand in ``argv``."""
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="cat", argv=["file.txt"])],
    )
    assert _validate_input(input, FILE_COMMANDS) is None


def test_validate_input_command_not_allowed() -> None:
    """Disallowed command returns error with help hint."""
    input = RunFileCommands(
        chain="pipe", file_commands=[RunFileCommand(command="curl", argv=["x"])]
    )
    err = _validate_input(input, FILE_COMMANDS)
    assert err is not None
    assert isinstance(err, ParseError)
    msg = err.message
    assert "curl" in msg
    assert "not allowed" in msg
    assert "help" in msg


def test_validate_input_forbidden_pattern() -> None:
    """Forbidden ``argv`` token: find rejects -exec, -ok, -delete."""
    input = RunFileCommands(
        chain="pipe",
        file_commands=[
            RunFileCommand(
                command="find",
                argv=[".", "-name", "*.py", "-exec", "rm"],
            )
        ],
    )
    err = _validate_input(input, FILE_COMMANDS)
    assert err is not None
    assert isinstance(err, ParseError)
    msg = err.message
    assert "Forbidden pattern" in msg
    assert "-exec" in msg


def test_validate_input_invalid_path_forbidden_char() -> None:
    """Path-like tokens are no longer globally blocked by character filters."""
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="cat", argv=["foo;bar"])],
    )
    assert _validate_input(input, FILE_COMMANDS) is None


def test_validate_input_invalid_arg_forbidden_char() -> None:
    """Arg metacharacters are governed by command regex rules, not global filters."""
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="cat", argv=["foo;bar", "file.txt"])],
    )
    assert _validate_input(input, FILE_COMMANDS) is None


def test_validate_input_arg_allows_pipe_for_regex() -> None:
    """``|`` is allowed in args (e.g. ripgrep regex alternation); subprocess uses argv, not a shell."""
    input = RunFileCommands(
        chain="pipe",
        file_commands=[
            RunFileCommand(
                command="rg",
                argv=["-n", "foo|bar", "src/"],
            )
        ],
    )
    assert _validate_input(input, FILE_COMMANDS) is None


def test_validate_input_requires_separator_for_leading_hyphen_paths() -> None:
    """Dash-prefixed files require -- or an explicit path prefix."""
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="cat", argv=["-bad"])],
    )
    assert isinstance(_validate_input(input, FILE_COMMANDS), ParseError)
    input.file_commands[0].argv = ["--", "-bad"]
    assert _validate_input(input, FILE_COMMANDS) is None


def test_validate_input_allows_gio_trash_with_path() -> None:
    """gio accepts only trash subcommand when at least one path is provided."""
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="gio", argv=["trash", "file.txt"])],
    )
    assert _validate_input(input, FILE_COMMANDS) is None


def test_validate_input_rejects_gio_non_trash_subcommand() -> None:
    """gio subcommands other than trash are rejected."""
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="gio", argv=["list", "."])],
    )
    err = _validate_input(input, FILE_COMMANDS)
    assert err is not None
    assert isinstance(err, ParseError)
    assert "not allowed by command spec" in err.message


def test_validate_input_allows_gio_trash_without_paths() -> None:
    """gio trash without explicit paths remains valid for base-guard behavior."""
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="gio", argv=["trash"])],
    )
    assert _validate_input(input, FILE_COMMANDS) is None


def test_validate_input_allows_mv_supported_flags() -> None:
    input = RunFileCommands(
        chain="pipe",
        file_commands=[
            RunFileCommand(
                command="mv",
                argv=[
                    "--verbose",
                    "--strip-trailing-slashes",
                    "src.txt",
                    "dst.txt",
                ],
            )
        ],
    )
    assert _validate_input(input, FILE_COMMANDS) is None


@pytest.mark.parametrize("flag", ["-f", "--force", "-n", "--no-clobber"])
def test_validate_input_rejects_mv_clobber_flags(flag: str) -> None:
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="mv", argv=[flag, "src.txt", "dst.txt"])],
    )
    err = _validate_input(input, FILE_COMMANDS)
    assert err is not None
    assert isinstance(err, ParseError)
    assert "not allowed by command spec" in err.message
    assert flag in err.message


def test_validate_input_allows_cp_supported_flags() -> None:
    input = RunFileCommands(
        chain="pipe",
        file_commands=[
            RunFileCommand(
                command="cp",
                argv=[
                    "--recursive",
                    "--verbose",
                    "src",
                    "dst",
                ],
            )
        ],
    )
    assert _validate_input(input, FILE_COMMANDS) is None


@pytest.mark.parametrize(
    "flag",
    ["-f", "--force", "-n", "--no-clobber", "-i", "--backup", "-a", "--preserve"],
)
def test_validate_input_rejects_unsupported_cp_flags(flag: str) -> None:
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="cp", argv=[flag, "src", "dst"])],
    )
    err = _validate_input(input, FILE_COMMANDS)
    assert err is not None
    assert isinstance(err, ParseError)
    assert "not allowed by command spec" in err.message
    assert flag in err.message


def test_validate_input_rejects_cp_combined_short_flags() -> None:
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="cp", argv=["-Rv", "src", "dst"])],
    )
    err = _validate_input(input, FILE_COMMANDS)
    assert err is not None
    assert isinstance(err, ParseError)
    assert "-Rv" in err.message


def test_validate_input_rejects_mv_interactive_flag() -> None:
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="mv", argv=["-i", "src.txt", "dst.txt"])],
    )
    err = _validate_input(input, FILE_COMMANDS)
    assert err is not None
    assert isinstance(err, ParseError)
    assert "not allowed by command spec" in err.message
    assert "-i" in err.message


def test_validate_input_rejects_mv_backup_flag() -> None:
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="mv", argv=["--backup", "a", "b"])],
    )
    err = _validate_input(input, FILE_COMMANDS)
    assert err is not None
    assert isinstance(err, ParseError)
    assert "not allowed by command spec" in err.message
    assert "--backup" in err.message


def test_validate_input_rejects_mv_target_directory_equals_form() -> None:
    input = RunFileCommands(
        chain="pipe",
        file_commands=[
            RunFileCommand(
                command="mv",
                argv=["--target-directory=dest", "src.txt"],
            )
        ],
    )
    err = _validate_input(input, FILE_COMMANDS)
    assert err is not None
    assert isinstance(err, ParseError)
    assert "not allowed by command spec" in err.message
    assert "--target-directory=dest" in err.message


@pytest.mark.parametrize(
    ("command", "argv"),
    [
        ("mv", []),
        ("mv", ["source.txt"]),
        ("mv", ["-t", "destination"]),
        ("cp", []),
        ("cp", ["source.txt"]),
        ("cp", ["-t", "destination"]),
    ],
)
def test_validate_input_requires_source_and_destination_paths(
    command: str, argv: list[str]
) -> None:
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command=command, argv=argv)],
    )
    err = _validate_input(input, FILE_COMMANDS)
    assert err is not None
    assert isinstance(err, ParseError)
    assert "source path" in err.message
    assert "destination path" in err.message


def test_validate_input_rejects_gio_trash_empty_flag() -> None:
    """gio trash --empty is rejected by the trash-only argv allowlist."""
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="gio", argv=["trash", "--empty", "."])],
    )
    err = _validate_input(input, FILE_COMMANDS)
    assert err is not None
    assert isinstance(err, ParseError)
    assert "not allowed by command spec" in err.message


def test_validate_input_custom_specs() -> None:
    """Validation uses provided specs, not just COMMAND_SPECS."""
    specs = (CmdSpec(name="only"),)
    input = RunFileCommands(
        chain="pipe", file_commands=[RunFileCommand(command="only", argv=["x"])]
    )
    assert _validate_input(input, specs) is None

    input_bad = RunFileCommands(
        chain="pipe", file_commands=[RunFileCommand(command="grep", argv=["x"])]
    )
    err = _validate_input(input_bad, specs)
    assert err is not None
    assert isinstance(err, ParseError)
    assert "help" in err.message


def test_cmdspec_rejects_allow_and_forbidden_together() -> None:
    """CmdSpec disallows non-empty allow and forbidden sets together."""
    with pytest.raises(ValueError):
        CmdSpec(
            name="bad",
            allowed_patterns=[ArgPattern(re.compile(r"-n"))],
            forbidden_patterns=[ArgPattern(re.compile(r"-x"))],
        )


def test_validate_input_allowed_patterns_allows_only_listed_args() -> None:
    """Allow mode: every arg token must appear in allowed_patterns."""
    specs = (
        CmdSpec(
            name="only",
            allowed_patterns=[
                ArgPattern(re.compile(r"-n")),
                ArgPattern(re.compile(r"def")),
            ],
        ),
    )
    ok_input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="only", argv=["-n", "def"])],
    )
    assert _validate_input(ok_input, specs) is None

    bad_input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="only", argv=["-n", "disallowed-token"])],
    )
    err = _validate_input(bad_input, specs)
    assert err is not None
    assert isinstance(err, ParseError)
    assert "not allowed by command spec" in err.message
    assert "disallowed-token" in err.message


def test_validate_input_allowed_patterns_rejects_empty_args() -> None:
    """Position-bound allow rules require that positional token to exist."""
    specs = (
        CmdSpec(
            name="only",
            allowed_patterns=[ArgPattern(re.compile("def"), position=0)],
        ),
    )
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="only", argv=[])],
    )
    err = _validate_input(input, specs)
    assert err is not None
    assert isinstance(err, ParseError)
    assert "Missing required whitelisted" in err.message


def test_validate_input_allowed_patterns_exact_not_substring() -> None:
    """Allowed list matches whole tokens only, not substrings of longer args."""
    specs = (
        CmdSpec(
            name="only",
            allowed_patterns=[ArgPattern(re.compile(r"def"))],
        ),
    )
    ok_input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="only", argv=["def"])],
    )
    assert _validate_input(ok_input, specs) is None
    bad_input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="only", argv=["define"])],
    )
    err = _validate_input(bad_input, specs)
    assert err is not None
    assert isinstance(err, ParseError)
    assert "define" in err.message


def test_validate_input_forbidden_pattern_fullmatch() -> None:
    """Forbidden patterns use fullmatch; only exact-token matches are blocked."""
    specs = (
        CmdSpec(
            name="off",
            forbidden_patterns=[ArgPattern(re.compile(r"\*"))],
        ),
    )
    ok_input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="off", argv=["anything", "x"])],
    )
    assert _validate_input(ok_input, specs) is None

    blocked_input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="off", argv=["*", "x"])],
    )
    err = _validate_input(blocked_input, specs)
    assert err is not None
    assert isinstance(err, ParseError)
    assert "Forbidden pattern" in err.message
    assert "*" in err.message


# -----------------------------------------------------------------------------
# _resolve_paths
# -----------------------------------------------------------------------------


def test_resolve_paths_single_file(tmp_path: Path) -> None:
    """Single existing file, no glob."""
    (tmp_path / "hello.txt").write_text("hi")
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="cat", argv=["hello.txt"])],
    )
    result = _resolve_paths(input, tmp_path)
    assert result == [tmp_path / "hello.txt"]


def test_resolve_paths_single_file_nonexistent(tmp_path: Path) -> None:
    """Single nonexistent file, no glob - still returns path for guard to check."""
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="cat", argv=["missing.txt"])],
    )
    result = _resolve_paths(input, tmp_path)
    assert result == [tmp_path / "missing.txt"]


def test_resolve_paths_multiple_literal(tmp_path: Path) -> None:
    """Multiple literal paths."""
    (tmp_path / "a").write_text("")
    (tmp_path / "b").write_text("")
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="cat", argv=["a", "b"])],
    )
    result = _resolve_paths(input, tmp_path)
    assert result == [tmp_path / "a", tmp_path / "b"]


def test_resolve_paths_glob_star_matches_files(tmp_path: Path) -> None:
    """Glob *.py expands to matching files, sorted."""
    (tmp_path / "b.py").write_text("")
    (tmp_path / "a.py").write_text("")
    (tmp_path / "c.py").write_text("")
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="cat", argv=["*.py"])],
    )
    result = _resolve_paths(input, tmp_path)
    assert result == [
        tmp_path / "a.py",
        tmp_path / "b.py",
        tmp_path / "c.py",
    ]


def test_resolve_paths_glob_star_no_matches(tmp_path: Path) -> None:
    """Glob with no matches returns empty for that pattern."""
    (tmp_path / "a.txt").write_text("")
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="cat", argv=["*.py"])],
    )
    result = _resolve_paths(input, tmp_path)
    assert result == []


def test_resolve_paths_glob_star_mixed_with_literal(tmp_path: Path) -> None:
    """One glob, one literal - both contribute."""
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.py").write_text("")
    (tmp_path / "readme.txt").write_text("")
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="cat", argv=["*.py", "readme.txt"])],
    )
    result = _resolve_paths(input, tmp_path)
    assert result == [
        tmp_path / "a.py",
        tmp_path / "b.py",
        tmp_path / "readme.txt",
    ]


def test_resolve_paths_glob_in_subdir(tmp_path: Path) -> None:
    """Glob in subdirectory: src/*.py."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text("")
    (src / "util.py").write_text("")
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="cat", argv=["src/*.py"])],
    )
    result = _resolve_paths(input, tmp_path)
    assert result == [src / "main.py", src / "util.py"]


def test_resolve_paths_recursive_glob_in_subdir(tmp_path: Path) -> None:
    """Recursive glob in path operands expands through unknown-depth folders."""
    src = tmp_path / "src"
    deep = src / "pkg" / "feature"
    deep.mkdir(parents=True)
    (src / "main.py").write_text("")
    (deep / "nested.py").write_text("")
    (deep / "notes.txt").write_text("")
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="cat", argv=["src/**/*.py"])],
    )

    result = _resolve_paths(input, tmp_path)

    assert result == [src / "main.py", deep / "nested.py"]


def test_resolve_paths_absolute_recursive_glob(tmp_path: Path) -> None:
    """Absolute glob operands use the deepest non-glob anchor."""
    workspace = tmp_path / "workspace"
    src = workspace / "src"
    deep = src / "pkg" / "feature"
    deep.mkdir(parents=True)
    (src / "main.py").write_text("")
    (deep / "nested.py").write_text("")
    (deep / "notes.txt").write_text("")
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="cat", argv=[str(src / "**" / "*.py")])],
    )

    result = _resolve_paths(input, tmp_path / "base")

    assert result == [src / "main.py", deep / "nested.py"]


def test_resolve_paths_glob_question_mark(tmp_path: Path) -> None:
    """Glob ? matches single char."""
    (tmp_path / "f1.txt").write_text("")
    (tmp_path / "f2.txt").write_text("")
    (tmp_path / "f10.txt").write_text("")
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="cat", argv=["f?.txt"])],
    )
    result = _resolve_paths(input, tmp_path)
    assert isinstance(result, list)
    assert result == [tmp_path / "f1.txt", tmp_path / "f2.txt"]
    assert tmp_path / "f10.txt" not in result


def test_resolve_paths_glob_brackets(tmp_path: Path) -> None:
    """Glob [ab] matches a or b."""
    (tmp_path / "a").write_text("")
    (tmp_path / "b").write_text("")
    (tmp_path / "c").write_text("")
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="cat", argv=["[ab]"])],
    )
    result = _resolve_paths(input, tmp_path)
    assert result == [tmp_path / "a", tmp_path / "b"]


def test_resolve_paths_resolves_relative(tmp_path: Path) -> None:
    """Paths resolved relative to base."""
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "x").write_text("")
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="cat", argv=["sub/x"])],
    )
    result = _resolve_paths(input, tmp_path)
    assert result == [sub / "x"]


def test_resolve_paths_absolute_ignores_base(tmp_path: Path) -> None:
    """Absolute paths resolve as given, not under base."""
    other = tmp_path / "other_repo"
    other.mkdir()
    f = other / "abs.txt"
    f.write_text("x")
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="cat", argv=[str(f)])],
    )
    result = _resolve_paths(input, tmp_path / "workspace")
    assert result == [f]


def test_resolve_paths_glob_and_literal_same_file(tmp_path: Path) -> None:
    """Same file via glob and literal can appear twice - no deduplication."""
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.py").write_text("")
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="cat", argv=["*.py", "a.py"])],
    )
    result = _resolve_paths(input, tmp_path)
    assert isinstance(result, list)
    # a.py appears twice - from glob and from literal
    assert len(result) == 3
    assert result.count(tmp_path / "a.py") == 2
    assert tmp_path / "b.py" in result


def test_resolve_paths_head_skips_value_after_n_flag(tmp_path: Path) -> None:
    """head -n 50 file: only file is treated as a path operand."""
    (tmp_path / "notes.txt").write_text("x")
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="head", argv=["-n", "50", "notes.txt"])],
    )
    result = _resolve_paths(input, tmp_path)
    assert result == [tmp_path / "notes.txt"]


def test_resolve_paths_tail_skips_value_after_bytes_flag(tmp_path: Path) -> None:
    """tail --bytes 20 file: only file is treated as a path operand."""
    (tmp_path / "notes.txt").write_text("x")
    input = RunFileCommands(
        chain="pipe",
        file_commands=[
            RunFileCommand(command="tail", argv=["--bytes", "20", "notes.txt"])
        ],
    )
    result = _resolve_paths(input, tmp_path)
    assert result == [tmp_path / "notes.txt"]


def test_guard_items_mv_uses_delete_for_source_and_create_for_destination(
    tmp_path: Path,
) -> None:
    (tmp_path / "src.txt").write_text("x")
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="mv", argv=["src.txt", "dst.txt"])],
    )
    spec = MV
    items = _guard_items(input, tmp_path, spec)
    assert [item.operation for item in items] == [Operation.DELETE, Operation.CREATE]
    assert items[0].location == (tmp_path / "src.txt")
    assert items[1].location == (tmp_path / "dst.txt")
    ready = items[0].value
    assert isinstance(ready, CommandReady)
    assert ready.argv == [
        "mv",
        str((tmp_path / "src.txt").resolve()),
        str((tmp_path / "dst.txt").resolve()),
    ]


def test_guard_items_cp_uses_read_for_source_and_create_for_destination(
    tmp_path: Path,
) -> None:
    (tmp_path / "src.txt").write_text("x")
    input = RunFileCommands(
        chain="pipe",
        file_commands=[RunFileCommand(command="cp", argv=["src.txt", "dst.txt"])],
    )
    items = _guard_items(input, tmp_path, CP)
    assert [item.operation for item in items] == [Operation.READ, Operation.CREATE]
    assert items[0].location == (tmp_path / "src.txt")
    assert items[1].location == (tmp_path / "dst.txt")
    ready = items[0].value
    assert isinstance(ready, CommandReady)
    assert ready.argv == [
        "cp",
        str((tmp_path / "src.txt").resolve()),
        str((tmp_path / "dst.txt").resolve()),
    ]


def test_guard_items_cp_target_directory_expands_per_source(tmp_path: Path) -> None:
    src_a = tmp_path / "a.txt"
    src_b = tmp_path / "b.txt"
    target = tmp_path / "copies"
    src_a.write_text("a")
    src_b.write_text("b")
    target.mkdir()
    input = RunFileCommands(
        chain="pipe",
        file_commands=[
            RunFileCommand(command="cp", argv=["-t", "copies", "a.txt", "b.txt"])
        ],
    )
    items = _guard_items(input, tmp_path, CP)
    assert [item.operation for item in items] == [
        Operation.READ,
        Operation.CREATE,
        Operation.READ,
        Operation.CREATE,
    ]
    assert items[0].location == src_a
    assert items[1].location == (target / "a.txt")
    assert items[2].location == src_b
    assert items[3].location == (target / "b.txt")


def test_guard_items_mv_directory_destination_expands_per_source(
    tmp_path: Path,
) -> None:
    src_a = tmp_path / "a.txt"
    src_b = tmp_path / "b.txt"
    target = tmp_path / "archive"
    src_a.write_text("a")
    src_b.write_text("b")
    target.mkdir()
    input = RunFileCommands(
        chain="pipe",
        file_commands=[
            RunFileCommand(command="mv", argv=["a.txt", "b.txt", "archive"])
        ],
    )
    spec = MV
    items = _guard_items(input, tmp_path, spec)
    assert [item.operation for item in items] == [
        Operation.DELETE,
        Operation.CREATE,
        Operation.DELETE,
        Operation.CREATE,
    ]
    assert items[0].location == src_a
    assert items[1].location == (target / "a.txt")
    assert items[2].location == src_b
    assert items[3].location == (target / "b.txt")


def test_guard_items_mv_target_directory_flag_expands_per_source(
    tmp_path: Path,
) -> None:
    src_a = tmp_path / "a.txt"
    src_b = tmp_path / "b.txt"
    target = tmp_path / "archive"
    src_a.write_text("a")
    src_b.write_text("b")
    target.mkdir()
    input = RunFileCommands(
        chain="pipe",
        file_commands=[
            RunFileCommand(command="mv", argv=["-t", "archive", "a.txt", "b.txt"])
        ],
    )
    spec = MV
    items = _guard_items(input, tmp_path, spec)
    assert [item.operation for item in items] == [
        Operation.DELETE,
        Operation.CREATE,
        Operation.DELETE,
        Operation.CREATE,
    ]
    assert items[0].location == src_a
    assert items[1].location == (target / "a.txt")
    assert items[2].location == src_b
    assert items[3].location == (target / "b.txt")
