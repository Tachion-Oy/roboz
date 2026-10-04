"""File-command resolver, permission guard, and execution chain."""

from pathlib import Path

from roboz.models import Message
from roboz.models.truncation import TruncationSpec
from roboz.runtime import EventPipe
from roboz.shed.identifiers import (
    CLI_TOOLS_SKILL_NAME,
    CONTINUE_FILE_COMMAND_TOOL_NAME,
    RUN_FILE_COMMAND_TOOL_NAME,
)
from roboz.shed.models import (
    ActionVerdict,
    CommandReady,
    GuardFileSingle,
    GuardFilesResult,
    PermissionRule,
)
from roboz.shed.tools.contexts import FileCommandExecutionContext, GuardContext
from roboz.shed.tools.guard import build_guarded_tool_chain, guard_items
from roboz.shed.tools.runner import ExecutableCommandCatalog
from roboz.shed.tools.truncation import default_cli_truncation
from roboz.shed.tools.types import ResolvedFileCommand
from roboz.tooling import Tool
from roboz.tooling.decorators import factory

from .contracts import CommandExecution, FileCommand
from .execute import execute_file_command
from .paths import resolve_tool_base
from .tokens import validate_tokens
from .sequence import split_command
from .specs import COMMANDS


@factory
def resolve_command(
    input: FileCommand, messages: list[Message], ctx: Path
) -> ResolvedFileCommand[CommandExecution, CommandReady]:
    """Read, search, compare, create, update, transfer, or delete files.

    Paths are relative to the configured working directory or absolute. Each
    reached command must pass its filesystem permission checks and approvals.
    Commands execute directly; earlier file changes are not rolled back.
    """
    return _prepare_step(CommandExecution(request=input, remaining=input.value), ctx)


@factory
def guard_file_command(
    input: ResolvedFileCommand[CommandExecution, CommandReady],
    messages: list[Message],
    ctx: GuardContext,
) -> GuardFilesResult[CommandExecution, CommandReady]:
    """Check every required filesystem permission, then obtain all approvals."""
    # Keep the CLI's concrete graph types while sharing the permission engine.
    return guard_items(
        items_to_guard=input.items, original_input=input.original_input, ctx=ctx
    )


@factory
def continue_file_command(
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


def get_run_file_command(
    *,
    base: Path,
    default_verdict: ActionVerdict,
    deny_rules: list[PermissionRule] | None = None,
    allow_rules: list[PermissionRule] | None = None,
    ask_rules: list[PermissionRule] | None = None,
    takes_precedence: ActionVerdict = ActionVerdict.deny,
    execute_cli_truncation: TruncationSpec = default_cli_truncation(),
    pipe: EventPipe | None = None,
    cli_skill_name: str = CLI_TOOLS_SKILL_NAME,
) -> list[Tool]:
    """Build the guarded file-command resolve -> guard -> execute chain.

    Supply an absolute base and allow/deny/ask rules. Patterns match literal POSIX
    names, preserving spaces and backslashes. Only the entry tool is exposed to
    the agent; permission checks and execution are automatic chained steps.
    For direct Agent use, register roboz.shed.skills.cli_skill with
    auto_loaded_skills or skills; cli_skill_name must match the registered skill's
    name. FileCommands binds both tools and guidance automatically.
    These guards do not provide an OS sandbox or
    protection against concurrent filesystem changes.
    """
    resolved_base = resolve_tool_base(base)
    entry = resolve_command(resolved_base)
    entry = entry.copy(
        name=RUN_FILE_COMMAND_TOOL_NAME,
        description=f"{entry.description}\nRead the `{cli_skill_name}` skill for syntax, commands, and chaining examples.",
    )
    return build_guarded_tool_chain(
        entry=entry,
        guard=guard_file_command(
            GuardContext(
                base=resolved_base,
                default_verdict=default_verdict,
                takes_precedence=takes_precedence,
                allow=list(allow_rules or []),
                deny=list(deny_rules or []),
                ask=list(ask_rules or []),
                pipe=pipe,
            )
        ),
        execute=execute_file_command(
            FileCommandExecutionContext(
                truncation=execute_cli_truncation,
                commands=ExecutableCommandCatalog.from_names(COMMANDS),
            )
        ),
        continuation=continue_file_command(resolved_base).copy(
            name=CONTINUE_FILE_COMMAND_TOOL_NAME,
            chain_condition=lambda output: isinstance(output, CommandExecution),
        ),
    )
