"""Opt-in tagged command resolver, permission guard, and execution chain."""

from pathlib import Path

from roboz.models import Message
from roboz.models.truncation import TruncationSpec
from roboz.runtime import EventPipe
from roboz.shed.models import (
    ActionVerdict,
    CommandReady,
    GuardFileSingle,
    GuardFilesResult,
    PermissionRule,
)
from roboz.shed.tools.contexts import FileCommandExecutionContext, GuardContext
from roboz.shed.tools.runner import ExecutableCommandCatalog
from roboz.shed.tools.truncation import default_cli_truncation
from roboz.shed.tools.types import ResolvedFileCommand
from roboz.shed.tools.utils import resolve_tool_base
from roboz.tooling import Tool
from roboz.tooling.decorators import factory

from .contracts import CommandExecution, TaggedFileCommand
from .execute import execute_tagged_command
from .guard import guard_tagged_file_command
from .helpers import validate_tokens
from .sequence import split_command
from .specs import COMMANDS


@factory
def resolve_tagged_command(
    input: TaggedFileCommand, messages: list[Message], ctx: Path
) -> ResolvedFileCommand[CommandExecution, CommandReady]:
    """Copy or move files and directories using ordered [value, tag] pairs in value.

    Start with ['cp', 'CMD'] or ['mv', 'CMD']. Tag flags FLG and paths PTH;
    ARG is unsupported. Example: value=[['cp', 'CMD'], ['source.txt', 'PTH'],
    ['copy.txt', 'PTH']]. To copy a directory use -r/-R/--recursive, e.g.
    value=[['cp', 'CMD'], ['-R', 'FLG'], ['src', 'PTH'], ['backup', 'PTH']].
    mv moves directories without a recursive flag. Both commands allow
    -v/--verbose, -f/--force, --strip-trailing-slashes, -t/--target-directory
    followed by a PTH directory, -T/--no-target-directory, and -- to end options.
    Sources allow '*' in any component, e.g. projects/*/src/*.py. Each star matches
    zero or more characters within one component; hidden names require a leading
    dot in that component. Matches are sorted in filesystem byte order per operand;
    zero matches fail the command. Destinations, including -t values, must be
    literal and reject '*'. '**', '?', and bracket patterns are unsupported.
    Trailing '/' selects directories;
    '/.' and '/..' suffixes retain their native meaning. No shell expansion is used.
    Without -t, the last PTH is the destination; multiple expanded sources need a
    directory. Place flags before operands. -T requires one expanded source and an
    exact destination; -t and -T conflict. cp merges directory contents; src/.
    copies the contents directly into the destination. mv can replace an empty
    directory, but cannot merge directories. Repeated flags, bundled flags, attached flag values,
    symlinks, hard-linked files, special files, overlapping transfers, and
    cross-filesystem moves are unsupported.
    Paths may be absolute or relative to the configured base. cp requires READ
    on sources; mv requires DELETE. Destinations require CREATE; overwriting an
    existing file or replacing an empty directory also requires READ and DELETE
    there. Permissions cover every descendant, including empty directories; copy
    merges require CREATE on existing directories without DELETE. -f does not
    bypass policy or approval. Ask rules apply to every required permission.
    All policy checks for a command pass before its approvals are requested.
    Separate commands with CTL tokens: ['&&', 'CTL'] on success, ['||', 'CTL']
    on failure, [';', 'CTL'] unconditionally, or ['|', 'CTL'] to pass stdout.
    Pipelines bind first; && and || have equal precedence and run left to right.
    Each reached command is resolved and guarded afresh; skipped commands are
    not inspected. Preparation errors and denials count as failures, so fallback
    commands can run under their own permission checks. Pipes buffer exact stdout
    sequentially, keep stderr separate, and use the last stage's status.
    Invalid sequence grammar rejects the whole call. Timeouts discard partial
    output and return status 124; cancellation, oversized output, and unexpected
    execution errors stop the sequence. Earlier writes are never rolled back.
    """
    return _prepare_step(CommandExecution(request=input, remaining=input.value), ctx)


@factory
def continue_tagged_command(
    input: CommandExecution, messages: list[Message], ctx: Path
) -> ResolvedFileCommand[CommandExecution, CommandReady]:
    """Resolve the next reached command using progress from the guarded executor."""
    return _prepare_step(input, ctx)


def _prepare_step(
    input: CommandExecution, base: Path
) -> ResolvedFileCommand[CommandExecution, CommandReady]:
    input.failure = None
    tokens, _ = split_command(input.remaining)
    try:
        name = tokens[0][0]
        if name not in COMMANDS:
            raise ValueError(f"Unsupported command: {name!r}")
        spec = COMMANDS[name]
        parsed = validate_tokens(tokens, spec)
        prepared = spec.prepare_command(parsed, base)
    except (ValueError, OSError) as error:
        input.failure = str(error)
        return ResolvedFileCommand(original_input=input, items=[])

    prepared.ready.stdin = input.stdin
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
    execute = execute_tagged_command(
        FileCommandExecutionContext(
            truncation=execute_cli_truncation,
            commands=ExecutableCommandCatalog.from_names(COMMANDS),
        )
    ).copy(
        name="execute_tagged_file_command",
        chained_to=guard,
        chain_condition=lambda output: isinstance(output, GuardFilesResult),
    )
    continuation = continue_tagged_command(resolved_base).copy(
        name="continue_tagged_file_command",
        chained_to=execute,
        chain_condition=lambda output: isinstance(output, CommandExecution),
    )
    guard.chain(continuation)
    return [entry, guard, execute, continuation]
