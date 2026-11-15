"""Run agent-written Bash commands, each only after the user approves it."""

import re
from glob import escape
from pathlib import Path

from pydantic import ConfigDict, Field

from roboz.models import Empty, Message
from roboz.runtime import EventPipe
from roboz.shed.identifiers import RUN_SHELL_COMMAND_TOOL_NAME
from roboz.shed.models import (
    ActionVerdict,
    CommandReady,
    GuardFileSingle,
    Operation,
    PermissionRule,
)
from roboz.shed.tools.cli_commands.command import guard_file_command
from roboz.shed.tools.cli_commands.contracts import CommandExecution
from roboz.shed.tools.cli_commands.execute import execute_file_command
from roboz.shed.tools.contexts import FileCommandExecutionContext, GuardContext
from roboz.shed.tools.guard import build_guarded_tool_chain
from roboz.shed.tools.runner import ExecutableCommandCatalog
from roboz.shed.tools.truncation import default_cli_truncation
from roboz.shed.tools.types import ResolvedFileCommand
from roboz.tooling import Tool
from roboz.tooling.decorators import factory


class ShellCommand(Empty):
    """One Bash command line, shown to the user and run exactly as written."""

    command: str = Field(
        ...,
        min_length=1,
        description=(
            "Bash command line. Break a long command across several lines "
            "with trailing backslashes or newlines."
        ),
    )
    model_config = ConfigDict(extra="forbid")


def _approval_prompt(command: str, base: Path) -> str:
    # A fence longer than any backtick run keeps the whole command inside the block.
    fence = "`" * max(3, max(map(len, re.findall(r"`+", command)), default=0) + 1)
    return (
        f"The agent wants to run this command in `{base}`:\n\n"
        f"{fence}bash\n{command}\n{fence}\n\n"
        "Allow? (yes/no)"
    )


@factory
def resolve_shell_command(
    input: ShellCommand, messages: list[Message], ctx: Path
) -> ResolvedFileCommand[CommandExecution, CommandReady]:
    """Run one Bash command line after the user approves it.

    The user is shown the exact command and answers yes or no before every
    run; a declined command does not run. Pipes, redirects, `&&`, `||`, `;`,
    and `&` work as in a terminal. The command inherits this application's
    environment and privileges, receives no interactive input, and returns its
    output and exit status when it finishes.
    """
    ready = CommandReady(
        command_name="bash",
        argv=["bash", "-c", input.command],
        base_workdir=ctx,
        display_command=input.command,
    )
    return ResolvedFileCommand(
        original_input=CommandExecution(remaining=[], ready=ready),
        items=[
            GuardFileSingle(
                operation=Operation.EXECUTE,
                location=ctx,
                value=ready,
                prompt=_approval_prompt(input.command, ctx),
            )
        ],
    )


def get_run_shell_command(*, base: Path, pipe: EventPipe) -> list[Tool]:
    """Build the resolve -> approve -> execute chain for arbitrary Bash commands.

    Every call asks the user; no rule skips the prompt, and a run without
    user interaction denies the command. Commands run in `base` with the
    limits of the guarded file-command executor. User approval is the only
    protection: this is not an OS sandbox.
    """
    base = base.resolve()
    entry = resolve_shell_command(base)
    entry = entry.copy(
        name=RUN_SHELL_COMMAND_TOOL_NAME,
        description=f"{entry.description}\nWorking directory: {base}",
    )
    # Denied unless the rule matches, and a match always asks.
    rule = PermissionRule(pattern=escape(base.as_posix()), operations={Operation.EXECUTE})
    return build_guarded_tool_chain(
        entry=entry,
        guard=guard_file_command(
            GuardContext(
                base=base,
                default_verdict=ActionVerdict.deny,
                takes_precedence=ActionVerdict.deny,
                allow=[rule],
                deny=[],
                ask=[rule],
                pipe=pipe,
            )
        ),
        execute=execute_file_command(
            FileCommandExecutionContext(
                truncation=default_cli_truncation(),
                commands=ExecutableCommandCatalog.from_names(["bash"]),
            )
        ),
    )
