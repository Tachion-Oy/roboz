"""Execute one guarded tagged step and return its result or continuation."""

import errno
import shlex
import subprocess

from roboz.models import Message, Str
from roboz.models.truncation import Severity, Truncation, TruncationSpec
from roboz.shed.models import CommandReady, GuardFilesResult, GuardStatus
from roboz.shed.tools import runner
from roboz.shed.tools.cli_commands.utilities.constants import (
    ERR_COMMAND_NOT_FOUND,
    ERR_OUTPUT_TOO_LARGE,
    ERR_TIMEOUT,
    MAX_COMMAND_OUTPUT_CHARS,
    PIPE_OUTPUT_TO_NEXT_COMMAND,
    PIPE_STDIN_FROM_PREVIOUS_COMMAND,
    SUBPROCESS_TIMEOUT_SECONDS,
    SUCCESS_NO_OUTPUT,
)
from roboz.shed.tools.cli_commands.utilities.formatting import _framed_cli_output
from roboz.shed.tools.contexts import FileCommandExecutionContext
from roboz.tooling.decorators import factory

from .contracts import CommandExecution
from .sequence import next_command, split_command


def _run_command(
    ready: CommandReady, ctx: FileCommandExecutionContext
) -> subprocess.CompletedProcess[str]:
    """Map expected lookup and launch failures to shell exit statuses."""
    binding = ctx.commands.binding_for(ready.command_name)
    try:
        executable = binding.require()
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            ready.argv, 127, "", ERR_COMMAND_NOT_FOUND.format(cmd=ready.command_name)
        )
    except PermissionError as error:
        return subprocess.CompletedProcess(ready.argv, 126, "", str(error))
    argv = [str(executable), *ready.argv[1:]]
    try:
        return runner.run_cli_argv(argv, ready.base_workdir, ready.stdin)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            argv, 124, "", ERR_TIMEOUT.format(timeout=SUBPROCESS_TIMEOUT_SECONDS)
        )
    except FileNotFoundError as error:
        if error.filename != argv[0] or not ready.base_workdir.is_dir():
            raise
        return subprocess.CompletedProcess(argv, 127, "", str(error))
    except PermissionError as error:
        return subprocess.CompletedProcess(argv, 126, "", str(error))
    except OSError as error:
        if error.errno != errno.ENOEXEC:
            raise
        return subprocess.CompletedProcess(argv, 126, "", str(error))


def _append_result(
    execution: CommandExecution,
    command: str,
    result: subprocess.CompletedProcess[str],
) -> None:
    """Accumulate reached command frames while keeping piped stdout out of history."""
    _, tail = split_command(execution.remaining)
    piped = bool(tail and tail[0] == ("|", "CTL"))
    body = PIPE_OUTPUT_TO_NEXT_COMMAND if piped else result.stdout or ""
    if result.returncode != 0:
        body = f"[error] Command failed (exit {result.returncode})\n{body}"
    elif not body:
        body = SUCCESS_NO_OUTPUT.format(command=command)
    if execution.stdin is not None:
        body = f"{PIPE_STDIN_FROM_PREVIOUS_COMMAND}\n{body}"
    if result.stderr:
        body += f"\nstderr:\n{result.stderr}"
    execution.accumulated_output += _framed_cli_output(command, body) + "\n"


def _final_result(
    execution: CommandExecution, returncode: int, truncation: TruncationSpec
) -> Str:
    status = "success" if returncode == 0 else "failure"
    return Str(
        value=f"Overall: {status} (exit {returncode})\n{execution.accumulated_output.rstrip()}",
        truncation=truncation,
    )


def _execute_step(
    input: GuardFilesResult[CommandExecution, CommandReady],
    ctx: FileCommandExecutionContext,
) -> tuple[str, subprocess.CompletedProcess[str]] | Str:
    """Produce a guarded step's outcome, stopping on unexpected execution errors."""
    execution = input.original_input
    tokens, _ = split_command(execution.remaining)
    command = shlex.join(value for value, _ in tokens)
    try:
        failure = execution.failure
        if input.status != GuardStatus.ALLOWED:
            failure = input.message or "Permission denied"
        if failure is not None:
            return command, subprocess.CompletedProcess([], 1, "", failure)
        ready = input.items[0].value
        command = ready.display_command
        return command, _run_command(ready, ctx)
    except Exception as error:
        return Str(
            value=f"Overall: failure (execution error)\n{execution.accumulated_output}"
            + _framed_cli_output(command, str(error)),
            truncation=ctx.truncation,
        )


def _completed_result(
    execution: CommandExecution,
    command: str,
    result: subprocess.CompletedProcess[str],
    truncation: TruncationSpec,
) -> Str | CommandExecution:
    """Bound captured output and record the normalized outcome before continuing."""
    captured_chars = len(result.stdout or "") + len(result.stderr or "")
    if captured_chars > MAX_COMMAND_OUTPUT_CHARS:
        return Str(
            value=f"Overall: failure (output too large; exit {result.returncode})\n"
            + ERR_OUTPUT_TOO_LARGE.format(
                actual_chars=captured_chars, max_chars=MAX_COMMAND_OUTPUT_CHARS
            ),
            truncation=truncation,
        )
    result.returncode = (
        result.returncode if result.returncode >= 0 else 128 - result.returncode
    )
    _append_result(execution, command, result)
    return _continue_or_finish(execution, result, truncation)


def _continue_or_finish(
    execution: CommandExecution,
    result: subprocess.CompletedProcess[str],
    truncation: TruncationSpec,
) -> Str | CommandExecution:
    """Select the next guarded step and its stdin, or report the final status."""
    execution.remaining, piped = next_command(execution.remaining, result.returncode)
    if execution.remaining:
        execution.stdin = (result.stdout or "") if piped else None
        execution.truncation = Truncation(threshold=0, severity=Severity.REMOVE)
        return execution
    return _final_result(execution, result.returncode, truncation)


@factory
def execute_tagged_command(
    input: GuardFilesResult[CommandExecution, CommandReady],
    messages: list[Message],
    ctx: FileCommandExecutionContext,
) -> Str | CommandExecution:
    """Execute an allowed step; apply control operators to every ordinary outcome."""
    outcome = _execute_step(input, ctx)
    if isinstance(outcome, Str):
        return outcome
    command, result = outcome
    return _completed_result(input.original_input, command, result, ctx.truncation)
