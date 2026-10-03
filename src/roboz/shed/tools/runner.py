"""Run guarded command payloads."""

import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

from roboz.shed.models import (
    CommandReady,
    GuardFilesResult,
    GuardStatus,
    RunFileCommands,
)
from roboz.shed.tools.cli_commands.utilities.constants import (
    ERR_COMMAND_NOT_FOUND,
    ERR_EXIT_NONZERO_NO_OUTPUT,
    ERR_OUTPUT_TOO_LARGE,
    ERR_TIMEOUT,
    MAX_COMMAND_OUTPUT_CHARS,
    PIPE_OUTPUT_TO_NEXT_COMMAND,
    PIPE_STDIN_FROM_PREVIOUS_COMMAND,
    SUBPROCESS_TIMEOUT_SECONDS,
    SUCCESS_NO_OUTPUT,
)
from roboz.shed.tools.cli_commands.utilities.formatting import _framed_cli_output
from roboz.models import Message, Str
from roboz.models.truncation import Severity, Truncation, TruncationSpec
from roboz.dependencies import (
    ExecutableDependency,
    ExternalDependency,
)
from roboz.tooling.context import HasExternalDependencies
from roboz.shed.tools.contexts import FileCommandExecutionContext
from roboz.tooling.decorators import factory


class _CommandOutcome(StrEnum):
    """Overall outcomes reported by the command executor."""

    SUCCESS = "success"
    FAILURE = "failure"


def run_cli_argv(
    argv: list[str], cwd: Path, stdin: str | None, *, preserve_bytes: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run argv and preserve its exit status and separate output streams.

    With preserve_bytes, map each input/output byte to its Latin-1 character
    without newline conversion. This reversible string transport keeps buffered
    pipes serializable; callers decode output for display separately.
    """
    if preserve_bytes:
        result = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
            input=stdin.encode("latin-1") if stdin is not None else None,
        )
        return subprocess.CompletedProcess(
            result.args,
            result.returncode,
            result.stdout.decode("latin-1"),
            result.stderr.decode("latin-1"),
        )
    return subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
        input=stdin,
    )


def argv_from_guard_result(
    input: GuardFilesResult,
) -> tuple[list[str], Path, str | None, str]:
    """Build subprocess argv, cwd, stdin, and label from a guard result."""
    ready_value = command_ready_from_guard_result(input)
    return (
        ready_value.argv,
        ready_value.base_workdir,
        ready_value.stdin,
        ready_value.display_command,
    )


def command_ready_from_guard_result(input: GuardFilesResult) -> CommandReady:
    """Return the resolver-validated command payload carried through the guard."""
    ready_value = input.items[0].value
    if not isinstance(ready_value, CommandReady):
        raise ValueError("expected CommandReady guard payload")
    return ready_value


def _append_frame(
    input: RunFileCommands | None, command_line: str, body: str, stderr: str = ""
) -> str:
    """Add one command's output to the ordered execution trace."""
    parts: list[str] = []
    if input is not None and input.chain == "|" and input.accumulated_output:
        parts.append(PIPE_STDIN_FROM_PREVIOUS_COMMAND)
    parts.append(body)
    if stderr:
        parts.append(f"stderr:\n{stderr}")
    frame = _framed_cli_output(command_line, "\n".join(parts))
    history = input.accumulated_output if input is not None else ""
    return history + frame + "\n"


def _should_continue(input: RunFileCommands, succeeded: bool) -> bool:
    """Apply the requested operator after an ordinary command outcome."""
    if len(input.file_commands) == 1:
        return False
    return (
        input.chain in ("|", ";")
        or (input.chain == "&&" and succeeded)
        or (input.chain == "||" and not succeeded)
    )


def _final_result(
    history: str, outcome: _CommandOutcome, detail: str, truncation: TruncationSpec
) -> Str:
    """Return the final status and the frames for commands that ran."""
    return Str(
        value=f"Overall: {outcome} ({detail})\n{history.rstrip()}",
        truncation=truncation,
    )


def _continue_chain(
    *,
    new_input: RunFileCommands,
    combined: str,
    out: str,
) -> RunFileCommands:
    """Advance to the next file command in the chain."""
    new_input.file_commands = new_input.file_commands[1:]
    if new_input.chain == "|":
        new_input.file_commands[0].stdin = out
    new_input.accumulated_output = combined
    new_input.truncation = Truncation(threshold=0, severity=Severity.REMOVE)
    return new_input


def _oversized_output_result(
    *, command_line: str, actual_chars: int, status: int, truncation: TruncationSpec
) -> Str:
    """Fail closed when command output is too large for safe chaining/persistence."""
    message = ERR_OUTPUT_TOO_LARGE.format(
        actual_chars=actual_chars, max_chars=MAX_COMMAND_OUTPUT_CHARS
    )
    body = _framed_cli_output(command_line, message)
    return _final_result(
        body, _CommandOutcome.FAILURE, f"output too large; exit {status}", truncation
    )


def _completed_body(
    input: RunFileCommands | None,
    command_line: str,
    result: subprocess.CompletedProcess[str],
) -> str:
    """Format one process result, hiding stdout sent to a later pipe stage."""
    stdout, stderr = result.stdout or "", result.stderr or ""
    piped = input is not None and input.chain == "|" and len(input.file_commands) > 1
    shown_stdout = PIPE_OUTPUT_TO_NEXT_COMMAND if piped else stdout
    if result.returncode == 0:
        return shown_stdout or SUCCESS_NO_OUTPUT.format(command=command_line)
    if not stdout and not stderr and not piped:
        return ERR_EXIT_NONZERO_NO_OUTPUT.format(
            code=result.returncode, command=command_line
        )
    failure = f"[error] Command failed (exit {result.returncode})"
    return f"{failure}\n{shown_stdout}" if shown_stdout else failure


def _completed_result(
    input: RunFileCommands | None,
    command_line: str,
    result: subprocess.CompletedProcess[str],
    truncation: TruncationSpec,
) -> Str | RunFileCommands:
    """Render a process result and follow its exit status."""
    stdout, stderr = result.stdout or "", result.stderr or ""
    history = _append_frame(
        input, command_line, _completed_body(input, command_line, result), stderr
    )
    if input is not None and _should_continue(input, result.returncode == 0):
        return _continue_chain(new_input=input, combined=history, out=stdout)
    outcome = (
        _CommandOutcome.SUCCESS if result.returncode == 0 else _CommandOutcome.FAILURE
    )
    return _final_result(history, outcome, f"exit {result.returncode}", truncation)


def _timeout_result(
    input: RunFileCommands | None, command_line: str, truncation: TruncationSpec
) -> Str | RunFileCommands:
    """Discard partial timeout output and stop a pipeline immediately."""
    message = ERR_TIMEOUT.format(timeout=SUBPROCESS_TIMEOUT_SECONDS)
    history = _append_frame(input, command_line, message)
    if (
        input is not None
        and input.chain != "|"
        and _should_continue(input, succeeded=False)
    ):
        return _continue_chain(new_input=input, combined=history, out="")
    return _final_result(history, _CommandOutcome.FAILURE, "timeout", truncation)


def _terminal_error(
    input: RunFileCommands | None,
    command_line: str,
    kind: str,
    error: Exception,
    truncation: TruncationSpec,
) -> Str:
    """Stop after an unexpected error, retaining reached command frames."""
    history = _append_frame(input, command_line, str(error) or kind)
    return _final_result(history, _CommandOutcome.FAILURE, kind, truncation)


def _missing_executable_result(
    argv: list[str], command_name: str
) -> subprocess.CompletedProcess[str]:
    """Represent a missing permitted executable with shell status 127."""
    return subprocess.CompletedProcess(
        argv, 127, "", ERR_COMMAND_NOT_FOUND.format(cmd=command_name)
    )


@dataclass(frozen=True)
class ExecutableCommandCatalog(HasExternalDependencies):
    """Immutable command implementations and their inspectable dependencies."""

    bindings: Mapping[str, ExecutableDependency]

    def __post_init__(self) -> None:
        """Validate and freeze executable bindings by command name."""
        bindings = dict(self.bindings)
        for command_name, binding in bindings.items():
            if command_name != binding.executable:
                raise ValueError(
                    "command binding must use its executable name: "
                    f"{command_name!r} != {binding.executable!r}"
                )
        object.__setattr__(self, "bindings", MappingProxyType(bindings))

    @classmethod
    def from_names(cls, names: Iterable[str]) -> "ExecutableCommandCatalog":
        """Build a command catalog from unique executable names."""
        bindings: dict[str, ExecutableDependency] = {}
        for name in names:
            if name in bindings:
                raise ValueError(f"duplicate executable command: {name}")
            bindings[name] = ExecutableDependency(name)
        return cls(bindings)

    def binding_for(self, command_name: str) -> ExecutableDependency:
        """Return the declared binding for a resolved command."""
        try:
            return self.bindings[command_name]
        except KeyError as error:
            raise RuntimeError(
                f"resolved command has no implementation: {command_name}"
            ) from error

    def external_dependencies(self) -> tuple[ExternalDependency, ...]:
        """Return all executable dependencies in catalog order."""
        return tuple(self.bindings.values())


@factory
def execute_file_command(
    input: GuardFilesResult,
    messages: list[Message],
    ctx: FileCommandExecutionContext,
) -> Str | RunFileCommands:
    """Execute a guarded CommandReady payload and return bounded output.

    Continue CLI sequences when original_input carries RunFileCommands; other
    guarded inputs execute once without a chaining context.
    """
    truncation = ctx.truncation
    if input.status != GuardStatus.ALLOWED:
        return Str(
            value=input.message
            or f"internal error: unexpected guard status {input.status}",
            truncation=truncation,
        )
    ready = command_ready_from_guard_result(input)
    argv, cwd, stdin, command_line = argv_from_guard_result(input)
    chain_input = (
        input.original_input
        if isinstance(input.original_input, RunFileCommands)
        else None
    )

    try:
        executable = ctx.commands.binding_for(ready.command_name)
    except Exception as error:
        return _terminal_error(
            chain_input, command_line, "internal error", error, truncation
        )

    try:
        executable_path = executable.require()
    except FileNotFoundError:
        result = _missing_executable_result(argv, ready.command_name)
    except Exception as error:
        return _terminal_error(
            chain_input, command_line, "lookup error", error, truncation
        )
    else:
        argv = [str(executable_path), *argv[1:]]
        try:
            result = run_cli_argv(argv, cwd, stdin)
        except subprocess.TimeoutExpired:
            return _timeout_result(chain_input, command_line, truncation)
        except FileNotFoundError as error:
            if error.filename != argv[0] or not cwd.is_dir():
                return _terminal_error(
                    chain_input, command_line, "launch error", error, truncation
                )
            result = _missing_executable_result(argv, ready.command_name)
        except Exception as error:
            return _terminal_error(
                chain_input, command_line, "execution error", error, truncation
            )

    stdout, stderr = result.stdout or "", result.stderr or ""
    captured_chars = len(stdout) + len(stderr)
    if captured_chars > MAX_COMMAND_OUTPUT_CHARS:
        return _oversized_output_result(
            command_line=command_line,
            actual_chars=captured_chars,
            status=result.returncode,
            truncation=truncation,
        )
    return _completed_result(chain_input, command_line, result, truncation)
