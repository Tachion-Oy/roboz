"""Factory for the guarded Unix file-oriented CLI tool chain."""

from collections.abc import Sequence
from pathlib import Path

from roboz.shed.identifiers import (
    CLI_TOOLS_SKILL_NAME,
    RUN_FILE_COMMAND_PASSIVE_TOOL_NAME,
    RUN_FILE_COMMAND_TOOL_NAME,
)
from roboz.shed.models import ActionVerdict, PermissionRule, RunFileCommands
from roboz.shed.tools.cli_commands.utilities.cmd_spec import CmdSpec
from roboz.shed.tools.guard import build_guarded_tool_chain
from roboz.shed.tools.runner import (
    ExecutableCommandCatalog,
    execute_file_command,
)
from roboz.shed.tools.truncation import default_cli_truncation
from roboz.shed.tools.utils import resolve_tool_base
from roboz.shed.tools.contexts import (
    FileCommandExecutionContext,
    FileCommandResolverContext,
    GuardContext,
)
from roboz.models.truncation import TruncationSpec
from roboz.runtime.pipe import EventPipe
from roboz.tooling import Tool

from .resolve import _validate_input, resolve_input
from .specs import FILE_COMMANDS_DELETE, FILE_COMMANDS_READ, FILE_COMMANDS_WRITE

__all__ = [
    "_validate_input",
    "get_run_file_command",
    "resolve_input",
]


def _normalize_context(
    *,
    base: Path,
    allow_rules: list[PermissionRule] | None,
    deny_rules: list[PermissionRule] | None,
    ask_rules: list[PermissionRule] | None,
    takes_precedence: ActionVerdict | None,
    default_verdict: ActionVerdict,
    command_specs: Sequence[CmdSpec] | None,
    pipe: EventPipe | None,
) -> tuple[FileCommandResolverContext, GuardContext]:
    allow = list(allow_rules if allow_rules else [])
    deny = list(deny_rules if deny_rules else [])
    ask = list(ask_rules if ask_rules else [])
    precedence = takes_precedence if takes_precedence else ActionVerdict.deny
    specs = (
        command_specs
        if command_specs is not None
        else FILE_COMMANDS_READ + FILE_COMMANDS_WRITE + FILE_COMMANDS_DELETE
    )
    resolved_base = resolve_tool_base(base)

    cli_ctx = FileCommandResolverContext(
        specs=specs,
        base=resolved_base,
        allow_rules=allow,
        deny_rules=deny,
        ask_rules=ask,
        takes_precedence=precedence,
        default_verdict=default_verdict,
    )
    guard_ctx = GuardContext(
        base=resolved_base,
        takes_precedence=precedence,
        default_verdict=default_verdict,
        allow=allow,
        deny=deny,
        ask=ask,
        command_specs=specs,
        pipe=pipe,
    )
    return cli_ctx, guard_ctx


def get_run_file_command(
    *,
    base: Path,
    default_verdict: ActionVerdict,
    deny_rules: list[PermissionRule] | None = None,
    allow_rules: list[PermissionRule] | None = None,
    ask_rules: list[PermissionRule] | None = None,
    takes_precedence: ActionVerdict | None = None,
    command_specs: Sequence[CmdSpec] | None = None,
    execute_cli_truncation: TruncationSpec = default_cli_truncation(),
    pipe: EventPipe | None = None,
    cli_skill_name: str = CLI_TOOLS_SKILL_NAME,
) -> list[Tool]:
    """Create the ``run_file_command`` tool chain with path guards.

    Covers basic Unix CLI workflows for reading, searching, and modifying files under
    allow/deny rules—not Git-specific tools (those get a separate entry point).

    Args:
        base: Absolute directory against which relative paths are resolved.
        default_verdict: Decision used when no permission rule matches.
        deny_rules: Rules that reject matching filesystem operations.
        allow_rules: Rules that permit matching filesystem operations.
        ask_rules: Rules that require interactive approval when matched.
        takes_precedence: Verdict that wins when allow and deny rules both match.
        command_specs: Commands and operand policies exposed by the tool.
        execute_cli_truncation: Truncation policy for command output.
        pipe: Optional event pipe used for interactive guard prompts.
        cli_skill_name: Skill id (``Skill.name``) referenced in the tool description
            as the source of detailed CLI usage instructions; defaults to
            ``CLI_TOOLS_SKILL_NAME`` from ``roboz.shed.identifiers``.
    """
    cli_ctx, guard_ctx = _normalize_context(
        base=base,
        allow_rules=allow_rules,
        deny_rules=deny_rules,
        ask_rules=ask_rules,
        takes_precedence=takes_precedence,
        default_verdict=default_verdict,
        command_specs=command_specs,
        pipe=pipe,
    )

    description = (
        "Run Unix CLI tools under path guards. Invoke file_commands=[{command:'help', argv:[]}] for the full reference of "
        "available commands, input shape, and this run's path permissions (allow/deny patterns, scope, overwrite rules). "
        f"The possibly available `{cli_skill_name}` skill provides a detailed instruction and description on usage. "
        "Provide argv as subprocess-style tokens: flags, positionals, and path-like tokens in command order. "
        "Commands execute with base as cwd; path tokens may be relative to base or absolute. "
        "Allow/deny/ask permission patterns are evaluated relative to base. "
        "A separate default verdict applies when no rule matches. "
        "For find, place start directories before predicates/flags in argv. "
        "chain is required on every call: use chain='pipe' or chain='and'. These map to normal shell behavior ('|' and '&&'). "
        "Use 'pipe' only when the next command reads stdin; otherwise use 'and'. "
        "No shell syntax (|, ;, &&) in command strings. "
    )

    run_file_command_tool = resolve_input(cli_ctx).copy(
        name=RUN_FILE_COMMAND_TOOL_NAME, description=description
    )

    entry, guard, execute = build_guarded_tool_chain(
        entry=run_file_command_tool,
        guard_ctx=guard_ctx,
        execute=execute_file_command(
            FileCommandExecutionContext(
                truncation=execute_cli_truncation,
                commands=ExecutableCommandCatalog.from_names(
                    (spec.name for spec in cli_ctx.specs)
                ),
            )
        ),
    )
    continuation = entry.copy(
        name=RUN_FILE_COMMAND_PASSIVE_TOOL_NAME,
        chained_to=execute,
        chain_condition=lambda output: isinstance(output, RunFileCommands),
    )
    guard.chain(continuation)
    return [entry, guard, execute, continuation]
