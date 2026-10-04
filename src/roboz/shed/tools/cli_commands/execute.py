"""Execute one guarded command step and return its result or continuation."""

import errno
import subprocess

from roboz.models import Message, Str
from roboz.models.truncation import LIGHT_MAX_CHARS, Severity, Truncation, TruncationSpec
from roboz.shed.models import CommandReady, GuardFilesResult, GuardStatus
from roboz.shed.tools.cli_commands.constants import (
    ERR_COMMAND_NOT_FOUND,
    ERR_OUTPUT_TOO_LARGE,
    ERR_TIMEOUT,
    MAX_COMMAND_OUTPUT_CHARS,
    PIPE_OUTPUT_TO_NEXT_COMMAND,
    PIPE_STDIN_FROM_PREVIOUS_COMMAND,
    SUBPROCESS_TIMEOUT_SECONDS,
    SUCCESS_NO_OUTPUT,
)
from roboz.shed.tools.formatting import _framed_cli_output
from roboz.shed.tools.contexts import FileCommandExecutionContext
from roboz.tooling.decorators import factory

from .contracts import CommandExecution
from .sequence import next_command, split_command

HISTORY_TRUNCATION_MARKER = "[earlier command output omitted]\n"


def _bounded_history(history: str, max_chars: int = LIGHT_MAX_CHARS) -> str:
    """Keep the newest output within the report budget, marking any omission."""
    if len(history) <= max_chars:
        return history
    tail_chars = max_chars - len(HISTORY_TRUNCATION_MARKER)
    return HISTORY_TRUNCATION_MARKER + history[-tail_chars:]


def _run_command(
    ready: CommandReady, ctx: FileCommandExecutionContext, stdin: bytes | None
) -> subprocess.CompletedProcess[bytes]:
    """Run with pipe bytes or UTF-8 inline text and map expected launch failures."""
    binding = ctx.commands.binding_for(ready.command_name)
    try:
        executable = binding.require()
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            ready.argv, 127, b"", ERR_COMMAND_NOT_FOUND.format(cmd=ready.command_name).encode()
        )
    except PermissionError as error:
        return subprocess.CompletedProcess(ready.argv, 126, b"", str(error).encode())
    argv = [str(executable), *ready.argv[1:]]
    try:
        if stdin is None:
            stdin = (ready.stdin or "").encode("utf-8")
        return subprocess.run(
            argv,
            cwd=ready.base_workdir,
            input=stdin,
            capture_output=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            argv, 124, b"", ERR_TIMEOUT.format(timeout=SUBPROCESS_TIMEOUT_SECONDS).encode()
        )
    except FileNotFoundError as error:
        if error.filename != argv[0] or not ready.base_workdir.is_dir():
            raise
        return subprocess.CompletedProcess(argv, 127, b"", str(error).encode())
    except PermissionError as error:
        return subprocess.CompletedProcess(argv, 126, b"", str(error).encode())
    except OSError as error:
        if error.errno != errno.ENOEXEC:
            raise
        return subprocess.CompletedProcess(argv, 126, b"", str(error).encode())


def _append_result(
    execution: CommandExecution,
    command: str,
    result: subprocess.CompletedProcess[bytes],
) -> None:
    """Accumulate reached command frames while keeping piped stdout out of history."""
    _, tail = split_command(execution.remaining)
    piped = bool(tail and tail[0] == ("|", "CTL"))
    stdout = (result.stdout or b"").decode("utf-8", "backslashreplace")
    stderr = (result.stderr or b"").decode("utf-8", "backslashreplace")
    body = PIPE_OUTPUT_TO_NEXT_COMMAND if piped else stdout
    if result.returncode != 0:
        body = f"[error] Command failed (exit {result.returncode})\n{body}"
    elif not body:
        body = SUCCESS_NO_OUTPUT.format(command=command)
    if execution.stdin is not None:
        body = f"{PIPE_STDIN_FROM_PREVIOUS_COMMAND}\n{body}"
    if stderr:
        body += f"\nstderr:\n{stderr}"
    execution.accumulated_output = _bounded_history(
        execution.accumulated_output + _framed_cli_output(command, body) + "\n"
    )


def _final_result(history: str, summary: str, truncation: TruncationSpec) -> Str:
    """Reserve space for the status so message truncation cannot hide the tail."""
    header = f"Overall: {summary}\n"
    return Str(
        value=header + _bounded_history(history.rstrip(), LIGHT_MAX_CHARS - len(header)),
        truncation=truncation,
    )


def _execute_step(
    input: GuardFilesResult[CommandExecution, CommandReady],
    ctx: FileCommandExecutionContext,
) -> tuple[str, subprocess.CompletedProcess[bytes]] | Str:
    """Produce a guarded step's outcome, stopping on unexpected execution errors."""
    execution = input.original_input
    tokens, _ = split_command(execution.remaining)
    ready = execution.ready
    command = ready.display_command if ready is not None else tokens[0][0]
    try:
        failure = execution.failure
        if input.status != GuardStatus.ALLOWED:
            failure = input.message or "Permission denied"
        if failure is not None:
            return command, subprocess.CompletedProcess([], 1, b"", failure.encode())
        if ready is None:
            raise ValueError("Missing prepared command")
        return command, _run_command(ready, ctx, execution.stdin)
    except Exception as error:
        return _final_result(
            execution.accumulated_output + _framed_cli_output(command, str(error)),
            "failure (execution error)",
            ctx.truncation,
        )


def _completed_result(
    execution: CommandExecution,
    command: str,
    result: subprocess.CompletedProcess[bytes],
    truncation: TruncationSpec,
) -> Str | CommandExecution:
    """Bound captured output and record the normalized outcome before continuing."""
    captured_chars = len((result.stdout or b"").decode("utf-8", "backslashreplace")) + len(
        (result.stderr or b"").decode("utf-8", "backslashreplace")
    )
    if captured_chars > MAX_COMMAND_OUTPUT_CHARS:
        return _final_result(
            ERR_OUTPUT_TOO_LARGE.format(
                actual_chars=captured_chars, max_chars=MAX_COMMAND_OUTPUT_CHARS
            ),
            f"failure (output too large; exit {result.returncode})",
            truncation,
        )
    result.returncode = (
        result.returncode if result.returncode >= 0 else 128 - result.returncode
    )
    _append_result(execution, command, result)
    return _continue_or_finish(execution, result, truncation)


def _continue_or_finish(
    execution: CommandExecution,
    result: subprocess.CompletedProcess[bytes],
    truncation: TruncationSpec,
) -> Str | CommandExecution:
    """Select the next guarded step and its stdin, or report the final status."""
    execution.remaining, piped = next_command(execution.remaining, result.returncode)
    if execution.remaining:
        execution.stdin = (result.stdout or b"") if piped else None
        execution.truncation = Truncation(threshold=0, severity=Severity.REMOVE)
        return execution
    status = "success" if result.returncode == 0 else "failure"
    return _final_result(
        execution.accumulated_output, f"{status} (exit {result.returncode})", truncation
    )


@factory
def execute_file_command(
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
