"""Run guarded command payloads."""

import os
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from types import MappingProxyType

from roboshed.models import (
    CommandReady,
    GuardFilesResult,
    GuardStatus,
    RunFileCommands,
)
from roboshed.tools.cli_commands.utilities.constants import (
    ERR_COMMAND_NOT_FOUND,
    ERR_EXIT_NONZERO,
    ERR_EXIT_NONZERO_NO_OUTPUT,
    ERR_OUTPUT_TOO_LARGE,
    ERR_TIMEOUT,
    MAX_COMMAND_OUTPUT_CHARS,
    PIPE_OUTPUT_TO_NEXT_COMMAND,
    PIPE_STDIN_FROM_PREVIOUS_COMMAND,
    SUBPROCESS_TIMEOUT_SECONDS,
    SUCCESS_NO_OUTPUT,
)
from roboshed.tools.cli_commands.utilities.formatting import _framed_cli_output
from roboz import Ctx
from roboz.models import Message, Str
from roboz.models.truncation import Severity, Truncation, TruncationSpec
from roboz.dependencies import (
    ExecutableDependency,
    ExternalDependency,
    ExternalDependencySource,
)
from roboz.tooling.context import _prepare_context
from roboz.tooling.decorators import factory


def run_cli_argv(
    argv: list[str], cwd: Path, stdin: str | None, command_line: str
) -> tuple[bool, str]:
    """Run argv. Return (True, output) on success or (False, formatted error)."""
    environment = os.environ.copy()
    # Keep executable parsing aligned with the validated GNU-style argv. A
    # ripgrep config can inject arguments that never went through the resolver.
    environment.pop("POSIXLY_CORRECT", None)
    environment.pop("RIPGREP_CONFIG_PATH", None)
    result = subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
        input=stdin,
        env=environment,
    )
    out = (result.stdout or "") + (result.stderr or "")
    return _format_run_result(result.returncode, out, command_line)


def _format_run_result(
    returncode: int, out: str, command_line: str
) -> tuple[bool, str]:
    """Shared (ok, output|formatted-error) shaping for argv runners."""
    if returncode == 0:
        return True, out
    msg = (
        ERR_EXIT_NONZERO.format(code=returncode, out=out).strip()
        if out.strip()
        else ERR_EXIT_NONZERO_NO_OUTPUT.format(code=returncode, command=command_line)
    )
    return False, msg


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


def _accumulate_error(
    new_input: RunFileCommands,
    command_line: str,
    message: str,
    truncation: TruncationSpec,
) -> Str | RunFileCommands:
    """Stop on pipe failures; accumulate and continue for AND chains."""
    if new_input.chain != "and":
        return Str(value=message, truncation=truncation)
    framed = _framed_cli_output(command_line, message)
    combined = (new_input.accumulated_output or "") + framed + "\n"
    if len(new_input.file_commands) > 1:
        new_input.file_commands = new_input.file_commands[1:]
        new_input.accumulated_output = combined
        new_input.truncation = Truncation(threshold=0, severity=Severity.REMOVE)
        return new_input
    return Str(value=combined.strip(), truncation=truncation)


def _format_step_body(
    *,
    chain: str,
    is_last_step: bool,
    has_previous_step: bool,
    body: str,
) -> str:
    if chain != "pipe":
        return body
    parts: list[str] = []
    if has_previous_step:
        parts.append(PIPE_STDIN_FROM_PREVIOUS_COMMAND)
    parts.append(body if is_last_step else PIPE_OUTPUT_TO_NEXT_COMMAND)
    return "\n".join(parts)


def _continue_chain(
    *,
    new_input: RunFileCommands,
    combined: str,
    out: str,
) -> RunFileCommands:
    """Advance to the next file command in the chain."""
    new_input.file_commands = new_input.file_commands[1:]
    if new_input.chain == "pipe":
        new_input.file_commands[0].stdin = out
    new_input.accumulated_output = combined
    new_input.truncation = Truncation(threshold=0, severity=Severity.REMOVE)
    return new_input


def _oversized_output_result(
    *, command_line: str, actual_chars: int, truncation: TruncationSpec
) -> Str:
    """Fail closed when command output is too large for safe chaining/persistence."""
    message = ERR_OUTPUT_TOO_LARGE.format(
        actual_chars=actual_chars, max_chars=MAX_COMMAND_OUTPUT_CHARS
    )
    body = _framed_cli_output(command_line, message)
    return Str(value=body.strip(), truncation=truncation)


@dataclass(frozen=True)
class ExecutableCommandCatalog(ExternalDependencySource):
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
    ctx: Ctx,
) -> Str | RunFileCommands:
    """Run a permitted file command and return bounded output."""
    truncation = ctx.truncation
    if input.status != GuardStatus.ALLOWED:
        return Str(
            value=input.message
            or f"internal error: unexpected guard status {input.status}",
            truncation=truncation,
        )
    if not isinstance(input.original_input, RunFileCommands):
        return Str(
            value="internal error: expected RunFileCommands original_input",
            truncation=truncation,
        )

    ready = command_ready_from_guard_result(input)
    argv, cwd, stdin, command_line = argv_from_guard_result(input)
    new_input = input.original_input

    try:
        executable = ctx.commands.binding_for(ready.command_name)
        argv = [str(executable.require()), *argv[1:]]
        ok, out = run_cli_argv(argv, cwd, stdin, command_line)
        if len(out) > MAX_COMMAND_OUTPUT_CHARS:
            return _oversized_output_result(
                command_line=command_line, actual_chars=len(out), truncation=truncation
            )
        if not ok:
            return _accumulate_error(new_input, command_line, out, truncation)

        body = out if out.strip() else SUCCESS_NO_OUTPUT.format(command=command_line)
        is_last_step = len(new_input.file_commands) == 1
        has_previous_step = bool((new_input.accumulated_output or "").strip())
        body = _format_step_body(
            chain=new_input.chain,
            is_last_step=is_last_step,
            has_previous_step=has_previous_step,
            body=body,
        )
        framed = _framed_cli_output(command_line, body)
        combined = (new_input.accumulated_output or "") + framed + "\n"

        if is_last_step:
            return Str(value=combined.strip(), truncation=truncation)
        return _continue_chain(new_input=new_input, combined=combined, out=out)

    except Exception as e:
        match e:
            case subprocess.TimeoutExpired():
                msg = ERR_TIMEOUT.format(timeout=SUBPROCESS_TIMEOUT_SECONDS)
            case FileNotFoundError():
                msg = ERR_COMMAND_NOT_FOUND.format(cmd=argv[0])
            case _:
                msg = str(e)
        return _accumulate_error(new_input, command_line, msg, truncation)


execute_file_command._prepare_ctx = partial(
    _prepare_context, required=("truncation", "commands")
)
