"""Run agent-written Bash commands, each only after the user approves it."""

import os
import re
from dataclasses import dataclass, field
from glob import escape
from pathlib import Path

from pydantic import ConfigDict, Field

from roboz.dependencies import ExecutableDependency, ExternalDependency
from roboz.models import Empty, Message, Str
from roboz.runtime import EventPipe
from roboz.shed.identifiers import RUN_SHELL_COMMAND_TOOL_NAME
from roboz.shed.models import (
    ActionVerdict,
    CommandReady,
    GuardFileSingle,
    GuardFilesResult,
    Operation,
    PermissionRule,
)
from roboz.shed.tools.bash_runner import ExecutionPolicy, bash_events, collect_output
from roboz.shed.tools.cli_commands.command import guard_file_command
from roboz.shed.tools.cli_commands.constants import SUCCESS_NO_OUTPUT
from roboz.shed.tools.cli_commands.contracts import CommandExecution
from roboz.shed.tools.cli_commands.execute import (
    MAX_PERMISSION_HISTORY_CHARS,
    PERMISSION_TRUNCATION_MARKER,
    _bounded_history,
    _final_result,
)
from roboz.shed.tools.contexts import GuardContext
from roboz.shed.tools.formatting import _framed_cli_output
from roboz.shed.tools.guard import build_guarded_tool_chain
from roboz.shed.tools.truncation import default_cli_truncation
from roboz.shed.tools.types import ResolvedFileCommand
from roboz.tooling import Tool
from roboz.tooling.context import HasExternalDependencies
from roboz.tooling.decorators import factory


class ShellCommand(Empty):
    """One Bash command line, shown to the user and run exactly as written."""

    command: str = Field(
        ...,
        min_length=1,
        description=(
            "Raw Bash as one plain string, not [value, tag] pairs, for example "
            "`grep -rn TODO src | head -n 5`. Break a long command across "
            "several lines."
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
    """Run one raw Bash command line after the user approves it.

    The user is shown the exact command and answers yes or no before every
    run; a declined command does not run. Pipes, redirects, `&&`, `||`, and
    `;` work as in a terminal. The command runs with this application's
    privileges and a minimal environment: `PATH`, `LANG`, and the variables
    the host allows. It receives no interactive input. Its output is shown to
    the user as it arrives and returned with the exit status when the command
    finishes. The command is stopped at the time limit, and nothing it starts
    keeps running after it: do not start background processes.
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


@dataclass(frozen=True)
class ShellCommandContext(HasExternalDependencies):
    """This run's event and cancellation pipe and the limits of every command."""

    pipe: EventPipe
    policy: ExecutionPolicy
    bash: ExecutableDependency = field(
        default_factory=lambda: ExecutableDependency("bash")
    )

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        """Report Bash without resolving or launching it."""
        return (self.bash,)


@factory
def execute_shell_command(
    input: GuardFilesResult[CommandExecution, CommandReady],
    messages: list[Message],
    ctx: ShellCommandContext,
) -> Str:
    """Run the approved command with the shared Bash runner and report its outcome."""
    del messages
    ready = input.original_input.ready
    if ready is None:
        raise ValueError("Missing prepared command")
    completion, output = collect_output(
        bash_events(
            ctx.bash,
            ready.argv[1:],
            ready.base_workdir,
            os.environ.get("PATH", os.defpath),
            ctx.policy,
            ctx.pipe.raise_if_cancelled,
        ),
        ctx.pipe.emit_script_output,
    )
    succeeded = completion.status == "success"
    if completion.exit_code is None:
        detail = completion.status
    else:
        detail = f"exit {completion.exit_code}"
    if succeeded and not output:
        output = SUCCESS_NO_OUTPUT.format(command=ready.display_command)
    return _final_result(
        _framed_cli_output(ready.display_command, output),
        f"{'success' if succeeded else 'failure'} ({detail})",
        default_cli_truncation(),
        _bounded_history(
            input.message or "",
            MAX_PERMISSION_HISTORY_CHARS,
            marker=PERMISSION_TRUNCATION_MARKER,
        ),
    )


def get_run_shell_command(
    *, base: Path, pipe: EventPipe, policy: ExecutionPolicy = ExecutionPolicy()
) -> list[Tool]:
    """Build the resolve -> approve -> execute chain for arbitrary Bash commands.

    Every call asks the user; no rule skips the prompt, and a run without
    user interaction denies the command. Commands run in `base` under
    `policy`, through the runner trusted scripts also use: a minimal environment,
    output streamed to the pipe, and the whole process group ended at the
    time limit or on cancellation. User approval is the only protection: this
    is not an OS sandbox.
    """
    base = base.resolve()
    entry = resolve_shell_command(base)
    entry = entry.copy(
        name=RUN_SHELL_COMMAND_TOOL_NAME,
        description=(
            f"{entry.description}\nWorking directory: {base}"
            f"\nTime limit: {policy.timeout_s:g} seconds"
        ),
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
        execute=execute_shell_command(ShellCommandContext(pipe=pipe, policy=policy)),
    )
