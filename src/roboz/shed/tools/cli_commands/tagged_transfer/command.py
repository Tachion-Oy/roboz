"""Opt-in tagged command resolver, permission guard, and execution chain."""

from pathlib import Path

from roboz.models import Message
from roboz.models.truncation import Severity, Truncation, TruncationSpec
from roboz.runtime import EventPipe
from roboz.shed.models import (
    ActionVerdict,
    CommandReady,
    GuardFileSingle,
    GuardFilesResult,
    GuardStatus,
    ParseError,
    PermissionRule,
)
from roboz.shed.tools.contexts import FileCommandExecutionContext, GuardContext
from roboz.shed.tools.runner import ExecutableCommandCatalog, execute_file_command
from roboz.shed.tools.truncation import default_cli_truncation
from roboz.shed.tools.types import ResolvedFileCommand
from roboz.shed.tools.utils import resolve_tool_base
from roboz.tooling import Tool
from roboz.tooling.decorators import factory

from .contracts import TaggedFileCommand
from .guard import guard_tagged_file_command
from .helpers import validate_tokens
from .specs import COMMANDS


@factory
def resolve_tagged_command(
    input: TaggedFileCommand, messages: list[Message], ctx: Path
) -> ResolvedFileCommand[TaggedFileCommand, CommandReady] | ParseError:
    """Copy or move regular files using ordered [value, tag] pairs in value.

    Start with ['cp', 'CMD'] or ['mv', 'CMD']. Tag flags FLG and literal paths PTH;
    ARG is unsupported. Example: value=[['cp', 'CMD'], ['source.txt', 'PTH'],
    ['copy.txt', 'PTH']]. Allowed flags: -v/--verbose, -t/--target-directory followed
    by a PTH directory, -T/--no-target-directory, and -- to end options. Without
    -t, the last PTH is the destination; multiple sources need a directory.
    Place flags before operands. -T requires one source and an exact file
    destination. -t and -T conflict.
    Repeated flags, bundled flags, attached flag values, globs, directory sources,
    symlinks, hard-linked files, special files, and cross-filesystem moves are
    unsupported.
    Paths may be absolute or relative to the configured base. cp requires READ
    on sources; mv requires DELETE. Destinations require CREATE; overwriting an
    existing file also requires READ and DELETE there. Ask rules apply to every
    required permission, including overwrites. All policy checks pass before any
    approvals are requested. One command per call; earlier writes are not rolled
    back if the executable fails partway through a multi-file transfer.
    """
    try:
        name = input.value[0][0]
        if name not in COMMANDS:
            raise ValueError(f"Unsupported command: {name!r}")
        spec = COMMANDS[name]
        parsed = validate_tokens(input.value, spec)
        prepared = spec.prepare_command(parsed, ctx)
    except (ValueError, OSError) as error:
        return ParseError(
            message=str(error),
            truncation=Truncation(threshold=0, severity=Severity.LIGHT),
        )

    return ResolvedFileCommand(
        original_input=input,
        items=[
            GuardFileSingle(operation=operation, location=path, value=prepared.ready)
            for operation, path in prepared.operations
        ],
    )


def get_run_tagged_file_command(
    *,
    base: Path,
    default_verdict: ActionVerdict,
    deny_rules: list[PermissionRule] | None = None,
    allow_rules: list[PermissionRule] | None = None,
    ask_rules: list[PermissionRule] | None = None,
    takes_precedence: ActionVerdict = ActionVerdict.deny,
    execute_cli_truncation: TruncationSpec = default_cli_truncation(),
    pipe: EventPipe | None = None,
) -> list[Tool]:
    """Build the experimental tagged cp/mv resolve -> guard -> execute chain.

    Supply an absolute base and allow/deny/ask rules. Patterns match literal POSIX
    names, preserving spaces and backslashes. Only the entry tool is exposed to
    the agent; permission checks and execution are automatic chained steps.
    This is an opt-in prototype with no OS sandbox or protection against
    concurrent filesystem changes.
    """
    resolved_base = resolve_tool_base(base)
    entry = resolve_tagged_command(resolved_base).copy(name="run_tagged_file_command")
    guard = guard_tagged_file_command(
        GuardContext(
            base=resolved_base,
            default_verdict=default_verdict,
            takes_precedence=takes_precedence,
            allow=list(allow_rules or []),
            deny=list(deny_rules or []),
            ask=list(ask_rules or []),
            pipe=pipe,
        )
    ).copy(
        chained_to=entry,
        chain_condition=lambda output: isinstance(output, ResolvedFileCommand),
    )
    execute = execute_file_command(
        FileCommandExecutionContext(
            truncation=execute_cli_truncation,
            commands=ExecutableCommandCatalog.from_names(COMMANDS),
        )
    ).copy(
        name="execute_tagged_file_command",
        chained_to=guard,
        chain_condition=lambda output: (
            isinstance(output, GuardFilesResult)
            and output.status == GuardStatus.ALLOWED
        ),
    )
    return [entry, guard, execute]
