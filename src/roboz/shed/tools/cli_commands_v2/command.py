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
from .tokens import validate_tokens
from .sequence import split_command
from .specs import COMMANDS


@factory
def resolve_tagged_command(
    input: TaggedFileCommand, messages: list[Message], ctx: Path
) -> ResolvedFileCommand[CommandExecution, CommandReady]:
    r"""Read, create, update, copy, or move files using ordered [value, tag] pairs.

    Start each command with cp, mv, pwd, cat, head, tail, wc, tee, touch, or mkdir
    tagged CMD.
    Tag flags FLG, file paths PTH, and count values or stdin '-' ARG.
    Place separate flags before operands; -- ends options. Unknown, repeated,
    bundled, and attached options are unsupported.
    pwd prints the physical base directory, accepts -P/--physical, and requires
    READ on the base. It takes no operands. cat accepts -n/--number,
    -b/--number-nonblank (overrides -n), -s/--squeeze-blank, -E/--show-ends,
    and -T/--show-tabs. head/tail default to ten lines and accept -n/--lines
    or -c/--bytes followed by an unsigned decimal ARG, including zero, plus
    -q/--quiet/--silent or -v/--verbose. Line/byte and quiet/verbose modes conflict.
    Signed counts, size suffixes, and tail follow mode are unsupported.
    wc accepts combinations of -l/--lines, -w/--words, -c/--bytes, -m/--chars,
    and -L/--max-line-length; indirect file lists are unsupported.
    Readers require READ on each selected regular file; directories are rejected.
    File operands retain order and repeats. Omitted file operands or ['-', 'ARG']
    read stdin; ['-', 'PTH'] reads the literal file. Without a pipe stdin is empty.
    Example: value=[['cat', 'CMD'], ['data.txt', 'PTH'], ['|', 'CTL'],
    ['head', 'CMD'], ['-n', 'FLG'], ['2', 'ARG'], ['|', 'CTL'],
    ['wc', 'CMD'], ['-l', 'FLG']].

    tee writes stdin to each literal destination PTH and to stdout. It creates
    missing files and overwrites existing files; -a/--append appends instead.
    One optional content ARG after flags and before destination PTHs supplies
    inline text, encoded as UTF-8. Example: value=[['tee', 'CMD'], ['-a', 'FLG'],
    ['Hello\n', 'ARG'], ['notes.txt', 'PTH']]. Incoming pipe bytes override inline
    text, including empty output; with neither, stdin is empty. Without destination
    paths tee only produces stdout and needs no filesystem permissions.
    touch creates missing named files unless -c/--no-create is supplied, and
    updates timestamps on existing regular files or directories without recursion.
    It requires at least one target PTH. Wildcard targets select existing paths;
    if a pattern matches nothing, the command fails before execution, even with -c,
    and never creates a filename containing the unmatched wildcard. This matches
    zsh's default and Bash with failglob, rather than default Bash.
    touch accepts -a (access time), -m (modification time), -c/--no-create,
    -d/--date followed by a date ARG, -t followed by [[CC]YY]MMDDhhmm[.ss] ARG,
    and -r/--reference followed by a literal existing PTH requiring READ.
    -a and -m may be combined, as may -r and -d for reference-relative dates.
    -t conflicts with -d and -r. Native touch interprets dates and calendar values.
    tee destinations and touch references reject wildcards. Both commands support
    --, require CREATE on targets, and also require READ and DELETE on existing
    regular files, including append and timestamp updates. Missing touch -c targets
    are no-ops but still require CREATE. New files require existing parents.
    All target permissions and approvals pass before any file is changed.

    mkdir creates directories from one or more literal PTH operands. It accepts
    -p/--parents to create missing parents and tolerate existing directories,
    -v/--verbose to report created directories, and --. Modes (-m/--mode) and
    security-context options are unsupported. Require CREATE on every explicit
    target, including existing directories, and every missing parent created by
    -p; existing intermediate directories need no additional permissions.
    For example, mkdir -p a/../b may create both a and b and checks both paths.
    Symlinks and existing non-directory components reject the whole command.
    Operand order and duplicates are preserved, so mkdir a a/b works without -p.
    Native missing-parent and existing-directory failures retain any partial
    effects. Use && before dependent touch or tee commands.

    cp/mv accept only PTH operands. Example: value=[['cp', 'CMD'],
    ['source.txt', 'PTH'], ['copy.txt', 'PTH']]. To copy a directory use
    -r/-R/--recursive, e.g.
    value=[['cp', 'CMD'], ['-R', 'FLG'], ['src', 'PTH'], ['backup', 'PTH']].
    mv moves directories without a recursive flag. Both commands allow
    -v/--verbose, -f/--force, --strip-trailing-slashes, -t/--target-directory
    followed by a PTH directory, -T/--no-target-directory, and -- to end options.
    Sources allow '*' in any component, e.g. projects/*/src/*.py. Each star matches
    zero or more characters within one component. One standalone '**' component
    recurses through zero or more directories, e.g. src/**/*.py or **/file.py.
    A final '**' also selects files: sdf/** includes sdf/ and its visible tree;
    sdf/**/ selects directories. Bare ** excludes the implicit current directory.
    Hidden names require a leading dot in their component; recursion skips them.
    Repeated stars within ordinary components, e.g. report**.py, are nonrecursive.
    Matches are sorted in filesystem byte order per operand;
    zero matches fail the command. Destinations, including -t values, must be
    literal and reject '*'. Multiple recursive '**' components, '?', and bracket
    patterns are unsupported.
    Trailing '/' selects directories;
    '/.' and '/..' suffixes retain their native meaning. No shell expansion is used.
    Without -t, the last PTH is the destination; multiple expanded sources need a
    directory. Place flags before operands. -T requires one expanded source and an
    exact destination; -t and -T conflict. cp merges directory contents; src/.
    copies the contents directly into the destination. mv can replace an empty
    directory, but cannot merge directories. Symlinks, hard-linked files, and
    special files are unsupported. Transfers reject duplicate sources, conflicting
    destinations, source/destination overlap, and cross-filesystem moves.
    Ancestor/descendant sources are allowed with separate outputs;
    mv may move a parent then fail on vanished descendants. Native partial effects
    and exit status are preserved.
    Paths may be absolute or relative to the configured base. cp requires READ
    on sources; mv requires DELETE. Destinations require CREATE; overwriting an
    existing file or replacing an empty directory also requires READ and DELETE
    there. Permissions cover every descendant, including hidden entries and empty
    directories; copy merges require CREATE on existing directories without DELETE.
    -f does not bypass policy or approval. Ask rules apply to every required permission.
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
    input.ready = None
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

    input.ready = prepared.ready
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
    """Build the opt-in v2 CLI resolve -> guard -> execute chain.

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
